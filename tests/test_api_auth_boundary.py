"""FastAPI server-side security boundary: 401 unauthenticated, 403 wrong-role,
allowed correct-role, forged-header rejection, and the patient-data startup guard.
Uses FastAPI TestClient with synthetic Entra principal headers."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _durable_audit_for_patient_mode(monkeypatch):
    """Patient-data mode fails closed if durable audit is unavailable. These tests
    exercise AUTH (401/403), so we wire a fake durable sink + durable config so the
    audit layer succeeds and we observe the auth decision. (Audit fail-closed is
    covered separately in test_patient_data_hardening.py.)"""
    from app.security import audit_sink as _as
    from app.security import secrets_provider as _sp

    class _FakeDurable:
        def write(self, rec):
            return True

    class _FakeKeyVault:
        def get_secret(self, name):
            assert name == "PSEUDONYM_SECRET"
            return "test-keyvault-salt"

    monkeypatch.setattr(_as, "get_audit_sink", lambda *a, **k: _FakeDurable())
    monkeypatch.setattr(_sp, "get_secrets_provider", lambda: _FakeKeyVault())
    monkeypatch.setenv("AUDIT_SINK", "durable")
    monkeypatch.setenv("SECRETS_PROVIDER", "keyvault")
    monkeypatch.delenv("PSEUDONYM_SECRET", raising=False)
    yield


@pytest.fixture(autouse=True)
def _seed_mimic_full_case(monkeypatch, tmp_path):
    """Seed a synthetic MIMIC-IV-ED-Full case so the (full-MIMIC-only) resolver
    finds cases (no credentialed data)."""
    proc = tmp_path / "processed"
    proc.mkdir()
    case = {
        "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000001,
        "subject_id": 10000001,
        "edstay": {"subject_id": 10000001, "stay_id": 30000001, "gender": "F",
                   "arrival_transport": "AMBULANCE", "disposition": "HOME"},
        "triage": {"subject_id": 10000001, "stay_id": 30000001, "heartrate": 88.0,
                   "chiefcomplaint": "CHEST PAIN", "acuity": 2},
        "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
    }
    (proc / "frontend_cases_override.jsonl").write_text(json.dumps(case))
    monkeypatch.setattr("app.config.settings.processed_dir", proc)
    yield


def _principal(groups):
    claims = [
        {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "u1"},
        {"typ": "name", "val": "Test User"},
    ] + [{"typ": "groups", "val": g} for g in groups]
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()
    claims = [
        {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "u1"},
        {"typ": "name", "val": "Test User"},
    ] + [{"typ": "groups", "val": g} for g in groups]
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


class TestPatientDataModeEnforcement:
    def test_unauthenticated_is_401(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.delenv("TRUSTED_AUTH_PROXY", raising=False)
        r = client.get("/cases")
        assert r.status_code == 401

    def test_trusted_ed_doctor_allowed(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        r = client.get("/cases",
                       headers={"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])})
        assert r.status_code == 200

    def test_wrong_role_is_403(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        # auditor cannot run an assessment
        r = client.post("/cases/kaggle-ktas~deadbeef/assessments",
                       headers={"X-MS-CLIENT-PRINCIPAL": _principal(["governance-auditors"])})
        assert r.status_code == 403

    def test_forged_header_without_trusted_proxy_is_401(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.delenv("TRUSTED_AUTH_PROXY", raising=False)
        r = client.get("/cases",
                       headers={"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])})
        assert r.status_code == 401

    def test_unmapped_group_gets_403(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        # valid identity but a group that maps to no role -> no permissions
        r = client.get("/cases",
                       headers={"X-MS-CLIENT-PRINCIPAL": _principal(["some-other-group"])})
        assert r.status_code == 403


class TestRbacAcrossRoutes:
    def _h(self, groups):
        return {"X-MS-CLIENT-PRINCIPAL": _principal(groups)}

    def test_researcher_cannot_submit_review(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        cuid = client.get("/cases", headers=self._h(["researchers"])).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews", json={"review_status": "ACCEPTED_AS_PRESENTED", "review_comment": "x"}, headers=self._h(["researchers"]))
        assert r.status_code == 403

    def test_nurse_cannot_view_governance(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        # governance report requires view_audit_log; nurse lacks it
        r = client.get("/governance/report", headers=self._h(["triage-nurses"]))
        assert r.status_code == 403

    def test_supervisor_can_view_governance(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        r = client.get("/governance/report", headers=self._h(["clinical-supervisors"]))
        # RBAC must let a supervisor through (not 403). The handler may return 200
        # (report present), 404 (report not generated in this env), or 500 (gen
        # error) — all of which mean RBAC allowed the request.
        assert r.status_code in (200, 404, 500)
        assert r.status_code != 403

    def test_patient_audit_events_reads_durable_sink(self, monkeypatch):
        from app.security import audit_sink as _as

        class _ReadableDurable:
            def write(self, rec):
                return True

            def read_recent(self, limit):
                return [{
                    "timestamp_utc": "2026-06-27T02:00:00+00:00",
                    "action": "view_audit_events",
                    "decision": "ALLOWED",
                }][:limit]

        monkeypatch.setattr(_as, "get_audit_sink", lambda *a, **k: _ReadableDurable())
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        r = client.get("/audit/events", headers=self._h(["security-admins"]))
        assert r.status_code == 200
        assert r.json()["source"] == "durable"
        assert r.json()["events"][0]["action"] == "view_audit_events"

    def test_patient_case_search_requires_indexed_backend(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        r = client.get("/cases", params={"q": "chest"},
                       headers=self._h(["ed-doctors"]))
        assert r.status_code == 503
        assert "search-index-backed" in r.json()["detail"]


class TestDemoModeStillEnforcesRbac:
    def test_demo_stub_can_view_but_not_submit_review(self, monkeypatch):
        for v in ["PATIENT_DATA_MODE", "AUTH_REQUIRED", "TRUSTED_AUTH_PROXY"]:
            monkeypatch.delenv(v, raising=False)
        # demo stub is a researcher: can view cases
        assert client.get("/cases").status_code == 200
        # but cannot submit a clinical review
        cuid = client.get("/cases").json()["cases"][0]["case_uid"]
        assert client.post(f"/cases/{cuid}/reviews", json={"review_status": "ACCEPTED_AS_PRESENTED", "review_comment": "x"}).status_code == 403

    def test_auth_session_reports_backend_permissions(self, monkeypatch):
        for v in ["PATIENT_DATA_MODE", "AUTH_REQUIRED", "TRUSTED_AUTH_PROXY"]:
            monkeypatch.delenv(v, raising=False)
        r = client.get("/auth/session", headers={"X-Demo-Role": "triage_nurse"})
        assert r.status_code == 200
        payload = r.json()
        assert payload["authenticated"] is True
        assert "can_run_assessment" in payload["permissions"]
        assert payload["demo_role_switcher_available"] is True

    def test_local_credentialed_ignores_demo_role_header(self, monkeypatch, tmp_path):
        for v in ["PATIENT_DATA_MODE", "AUTH_REQUIRED", "TRUSTED_AUTH_PROXY"]:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.setenv("LOCAL_RESEARCH_ROLE", "researcher")
        monkeypatch.setenv("BACKEND_BIND_HOST", "127.0.0.1")
        monkeypatch.setenv("PSEUDONYM_SECRET", "test-local-secret")
        monkeypatch.setenv("LOCAL_CREDENTIALED_OUTPUT_DIR", str(tmp_path / "out"))
        r = client.get("/auth/session", headers={"X-Demo-Role": "triage_nurse"})
        assert r.status_code == 200
        payload = r.json()
        assert payload["current_mode"] == "local_credentialed_research"
        assert payload["demo_role_switcher_available"] is False
        assert payload["display_name"] == "LOCAL CREDENTIALED RESEARCH USER"
        assert payload["source"] == "local_fixed_role"
        assert "LOCAL_RESEARCH_ROLE" in payload["local_role_change_instruction"]
        assert "Role switching disabled" in payload["demo_role_switcher_reason"]
        assert payload["roles"] == ["researcher"]
        assert "can_run_assessment" not in payload["permissions"]

    def test_azure_supervisor_demo_role_switcher_is_explicit(self, monkeypatch):
        for v in [
            "PATIENT_DATA_MODE",
            "AUTH_REQUIRED",
            "TRUSTED_AUTH_PROXY",
            "LOCAL_CREDENTIALED_RESEARCH",
            "REAL_PATIENT_DATA",
        ]:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("AZURE_SUPERVISOR_DEMO_MODE", "true")
        monkeypatch.setenv("ALLOW_DEMO_ROLE_SWITCHER", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "demo")
        r = client.get("/auth/session", headers={"X-Demo-Role": "triage_nurse"})
        assert r.status_code == 200
        payload = r.json()
        assert payload["current_mode"] == "azure_supervisor_demo"
        assert payload["demo_role_switcher_available"] is True
        assert payload["source"] == "azure_supervisor_demo_stub"
        assert "can_run_assessment" in payload["permissions"]

    def test_trusted_proxy_without_principal_does_not_fall_back_to_stub(self, monkeypatch):
        for v in ["PATIENT_DATA_MODE", "AUTH_REQUIRED", "LOCAL_CREDENTIALED_RESEARCH"]:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "azure")
        r = client.get("/auth/session", headers={"X-Demo-Role": "triage_nurse"})
        assert r.status_code == 200
        payload = r.json()
        assert payload["authenticated"] is False
        assert payload["demo_role_switcher_available"] is False
        assert payload["roles"] == []

    def test_ui_access_check_audits_allowed_and_denied(self, monkeypatch):
        for v in ["PATIENT_DATA_MODE", "AUTH_REQUIRED", "TRUSTED_AUTH_PROXY"]:
            monkeypatch.delenv(v, raising=False)
        allowed = client.post(
            "/auth/ui-access",
            headers={"X-Demo-Role": "triage_nurse"},
            json={
                "permission": "can_run_assessment",
                "action": "test_run_tab",
                "page": "Triage Review",
            },
        )
        assert allowed.status_code == 200
        assert allowed.json()["allowed"] is True
        denied = client.post(
            "/auth/ui-access",
            headers={"X-Demo-Role": "researcher"},
            json={
                "permission": "can_run_assessment",
                "action": "test_run_tab",
                "page": "Triage Review",
            },
        )
        assert denied.status_code == 200
        assert denied.json()["allowed"] is False


class TestStatusRoutes:
    def test_llm_status_distinguishes_credentials_from_local_egress_block(
        self,
        monkeypatch,
    ):
        for v in [
            "PATIENT_DATA_MODE",
            "AUTH_REQUIRED",
            "TRUSTED_AUTH_PROXY",
            "ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH",
            "APPROVED_CLOUD_LLM_DATA_PROCESSING",
        ]:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "not-a-real-key")
        monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "demo-deployment")
        monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

        r = client.get("/status/llm")
        assert r.status_code == 200
        payload = r.json()
        assert payload["active_profile"] == "local_credentialed_research"
        assert payload["azure_credentials_present"] is True
        assert payload["azure_config_present"] is False
        assert payload["blocked_by_local_credentialed_research"] is True
        assert "blocked" in payload["reason_if_disabled"].lower()

    def test_llm_status_blocks_cloud_for_credentialed_mimic_azure_demo(
        self,
        monkeypatch,
    ):
        for v in [
            "PATIENT_DATA_MODE",
            "LOCAL_CREDENTIALED_RESEARCH",
            "AUTH_REQUIRED",
            "TRUSTED_AUTH_PROXY",
            "REAL_PATIENT_DATA",
            "ALLOW_CLOUD_LLM_WITH_CREDENTIALED_MIMIC",
            "APPROVED_CLOUD_LLM_DATA_PROCESSING",
        ]:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("AZURE_SUPERVISOR_DEMO_MODE", "true")
        monkeypatch.setenv("ALLOW_DEMO_ROLE_SWITCHER", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "demo")
        monkeypatch.setenv("ALLOW_FULL_MIMIC_IN_AZURE_DEMO", "true")
        monkeypatch.setenv("REAL_MIMIC_DEMO_ACKNOWLEDGED", "true")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "not-a-real-key")
        monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "demo-deployment")
        monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

        r = client.get("/status/llm")
        assert r.status_code == 200
        payload = r.json()
        assert payload["active_profile"] == "azure_supervisor_demo"
        assert payload["azure_credentials_present"] is True
        assert payload["credentialed_mimic_active"] is True
        assert payload["blocked_by_credentialed_mimic_cloud_policy"] is True
        assert "credentialed MIMIC data is active" in payload["reason_if_disabled"]

    def test_runtime_status_is_redacted_and_precise(self, monkeypatch, tmp_path):
        for v in [
            "PATIENT_DATA_MODE",
            "LOCAL_CREDENTIALED_RESEARCH",
            "AZURE_SUPERVISOR_DEMO_MODE",
            "ALLOW_FULL_MIMIC_IN_AZURE_DEMO",
            "REAL_MIMIC_DEMO_ACKNOWLEDGED",
        ]:
            monkeypatch.delenv(v, raising=False)
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        monkeypatch.setenv("MIMIC_FULL_MODEL_REPORT_DIR", str(report_dir))
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(tmp_path / "missing-ed"))

        r = client.get("/runtime/status")
        assert r.status_code == 200
        payload = r.json()
        assert payload["mimic_full"]["path"] == "redacted"
        assert str(tmp_path) not in str(payload)
        assert payload["mimic_full"]["state"] == "path configured but not readable"
        assert payload["reports"]["path"] == "redacted"
        assert payload["reports"]["comparison_report_present"] is False


class TestStartupGuard:
    def test_refuses_unsafe_patient_data_config(self, monkeypatch):
        import importlib
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)  # defaults to demo -> unsafe
        import app.main as m
        with pytest.raises(RuntimeError) as e:
            importlib.reload(m)
        assert "unsafe security config" in str(e.value)
        # restore a clean module for other tests
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        importlib.reload(m)
