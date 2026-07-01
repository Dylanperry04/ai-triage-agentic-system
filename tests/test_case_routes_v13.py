"""v13 canonical case_uid-keyed API + resolver + security status.
Synthetic/public-demo data only. Verifies pseudonymous routing, RBAC, no raw-id
leakage, and the security-status builder."""
import base64
import json

import pytest
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def _principal(groups):
    claims = [
        {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "u1"},
        {"typ": "name", "val": "T"},
    ] + [{"typ": "groups", "val": g} for g in groups]
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for v in ("PATIENT_DATA_MODE", "AUTH_REQUIRED", "TRUSTED_AUTH_PROXY",
              "SECRETS_PROVIDER", "AUDIT_SINK", "AUTH_PROVIDER", "DEMO_ROLE",
              "FASTAPI_BASE_URL", "CORS_ALLOWED_ORIGINS"):
        monkeypatch.delenv(v, raising=False)
    # Seed a synthetic MIMIC-IV-ED-Full case for the (full-MIMIC-only) resolver.
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
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(proc))
    yield


class TestCaseUidRouting:
    def test_list_cases_uids_are_pseudonymous(self):
        r = client.get("/cases")
        assert r.status_code == 200
        cases = r.json()["cases"]
        assert cases, "expected some KTAS cases"
        for c in cases[:20]:
            assert "~" in c["case_uid"]          # pseudonymous format
            assert ":" not in c["case_uid"]      # not the old raw format

    def test_get_and_assessment_round_trip_no_raw_id(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        assert client.get(f"/cases/{cuid}", headers=headers).status_code == 200
        a = client.post(f"/cases/{cuid}/assessments", headers=headers)
        assert a.status_code == 200
        assert a.json().get("case_uid") == cuid
        assert "stay_id" not in a.json()         # raw id never returned

    def test_multiagent_explanation_is_case_uid_keyed_and_no_raw_id(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        async def _fake_team(case_uid, evidence, user_question=None):
            blob = json.dumps(evidence)
            assert case_uid == cuid
            assert evidence["case_uid"] == cuid
            assert "stay_id" not in blob
            assert "subject_id" not in blob
            return {
                "status": "PASS",
                "agent_turns": [
                    {"agent": "IntakeAgent", "text": "Facts from verified evidence."},
                    {"agent": "ValidationAgent", "text": "No missing fields."},
                    {"agent": "SafetyReviewAgent", "text": "Human clinical review required."},
                    {"agent": "ExplanationAgent", "text": "Summary. Clinician review required."},
                ],
                "final_explanation": "Summary. Clinician review required.",
                "safety_failures": [],
            }

        monkeypatch.setattr(
            "app.agents.autogen_multi_agent_team.run_case_uid_team_explanation",
            _fake_team,
        )
        r = client.post(
            f"/cases/{cuid}/multiagent-explanations",
            headers=headers,
            json={"question": "Summarise the case"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["case_uid"] == cuid
        assert body["multiagent"] is True
        assert body["status"] == "PASS"
        assert "stay_id" not in json.dumps(body)
        from app.version import APP_VERSION, PACKAGE_CHECKPOINT
        assert body["app_version"] == APP_VERSION
        assert body["package_checkpoint"] == PACKAGE_CHECKPOINT

    def test_unknown_case_uid_404(self):
        assert client.get("/cases/kaggle-ktas~deadbeefdead").status_code == 404


class TestRbacOnCaseRoutes:
    def test_researcher_no_clinical_content(self):
        # demo stub is researcher; list returns cases but without clinical body
        first = client.get("/cases").json()["cases"][0]
        assert "case" not in first              # no clinical content for researcher

    def test_clinician_gets_clinical_content(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        r = client.get("/cases",
                       headers={"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])})
        assert r.status_code == 200
        first = r.json()["cases"][0]
        assert "triage" in first              # clinical content present (safe DTO)
        # and still no raw identifiers anywhere
        import json as _json
        assert "subject_id" not in _json.dumps(first)
        assert "stay_id" not in _json.dumps(first)

    def test_researcher_cannot_submit_review(self, monkeypatch):
        cuid = client.get("/cases").json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews",
                        json={"review_status": "ACCEPTED_AS_PRESENTED", "review_comment": "x"})
        assert r.status_code == 403            # researcher lacks submit_review

    def test_override_requires_reason(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews",
                        headers=headers,
                        json={"review_status": "OVERRIDDEN", "review_comment": "x",
                              "clinician_override": "Red"})
        assert r.status_code == 422            # override needs override_reason

    def test_invalid_review_status_rejected(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews",
                        headers=headers,
                        json={"review_status": "HACKED", "review_comment": "x"})
        assert r.status_code == 422

    def test_unbounded_review_comment_rejected(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews",
                        headers=headers,
                        json={"review_status": "REVIEWED",
                              "review_comment": "x" * 5000})
        assert r.status_code == 422


class TestSecurityStatus:
    def test_demo_status_is_safe(self):
        from app.security.security_status import build_security_status
        s = build_security_status()
        assert s["current_mode"] == "public_demo"
        assert s["is_safe"] is True
        assert s["demo_role_switcher_enabled"] is True
        # never leak a path/secret
        assert "full_mimic_path" not in s
        assert "pseudonym_secret" not in s

    def test_patient_mode_flags_unsafe(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        from app.security.security_status import build_security_status
        s = build_security_status()
        assert s["current_mode"] == "secured_research"
        assert s["is_safe"] is False
        assert any("AUTH_REQUIRED" in p for p in s["unsafe_combinations"])

    def test_cors_wildcard_flagged(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
        from app.security.security_status import unsafe_combinations
        assert any("CORS" in p for p in unsafe_combinations())

    def test_security_status_endpoint_rbac(self, monkeypatch):
        # researcher (demo stub) lacks view_security_status
        assert client.get("/security/status").status_code == 403
        # security_admin can view
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        r = client.get("/security/status",
                       headers={"X-MS-CLIENT-PRINCIPAL": _principal(["security-admins"])})
        assert r.status_code == 200
        assert r.json()["current_mode"] == "public_demo"


class TestResolverConsistency:
    def test_uid_built_in_ui_matches_resolver(self):
        from app.schemas.workflow_run import make_case_uid
        from app.api import case_resolver
        cases = case_resolver.list_cases()
        rc = cases[0]
        ui_uid = make_case_uid(rc.source_dataset, rc.stay_id)
        assert ui_uid == rc.case_uid
        assert case_resolver.resolve(ui_uid).stay_id == rc.stay_id
