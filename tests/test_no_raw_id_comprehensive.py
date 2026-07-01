"""Comprehensive recursive no-raw-identifier test across ALL canonical endpoints,
plus the v13-final hardening (CORS comma-list, full-MIMIC model router,
FASTAPI_BASE_URL requirement, audit durable read). Public-demo data only."""
import base64
import json

import pytest
from starlette.testclient import TestClient

import app.main

# Rebuilt fresh in the autouse fixture so a prior test that reloads app.main does
# not leave this pointing at a stale app object.
client = TestClient(app.main.app)

RAW_ID_KEYS = ("subject_id", "stay_id", "hadm_id", "mrn")


def _principal(groups):
    claims = [
        {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "u1"},
        {"typ": "name", "val": "T"},
    ] + [{"typ": "groups", "val": g} for g in groups]
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


def _assert_no_raw_ids(obj, where=""):
    """Recursively assert no raw-id key appears anywhere in the response."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k not in RAW_ID_KEYS, f"raw id {chr(39)}{k}{chr(39)} leaked in {where}"
            _assert_no_raw_ids(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_no_raw_ids(v, f"{where}[{i}]")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for v in ("PATIENT_DATA_MODE", "AUTH_REQUIRED", "TRUSTED_AUTH_PROXY",
              "SECRETS_PROVIDER", "AUDIT_SINK", "AUTH_PROVIDER", "DEMO_ROLE",
              "FASTAPI_BASE_URL", "CORS_ALLOWED_ORIGINS", "MIMIC_FULL_MODEL_PATH"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
    # Seed a self-contained processed dir so KTAS cases resolve without depending
    # on a pre-generated pipeline artefact. The resolver and UI both read
    # frontend_cases_override.jsonl first. MIMIC demo still loads from bundled CSVs.
    import json as _json
    from app.config import settings
    proc = tmp_path / "processed"
    proc.mkdir()
    ktas_case = {
        "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000007, "subject_id": 900007,
        "edstay": {"subject_id": 900007, "hadm_id": None, "stay_id": 7,
                   "intime": None, "outtime": None, "gender": "F", "race": None,
                   "arrival_transport": "WALK IN", "disposition": "HOME"},
        "triage": {"subject_id": 900007, "stay_id": 7, "temperature": 98.6,
                   "heartrate": 88.0, "resprate": 18.0, "o2sat": 98.0, "sbp": 120.0,
                   "dbp": 80.0, "pain": "3", "chiefcomplaint": "CHEST PAIN",
                   "acuity": 2},
        "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
    }
    (proc / "frontend_cases_override.jsonl").write_text(_json.dumps(ktas_case))
    # Patch processed_dir on the live settings object the resolver holds, and on
    # the app.config.settings reference, without reloading any module (reloading
    # app.config orphans the settings object the resolver imported at load time).
    monkeypatch.delenv("ALLOW_LEGACY_RAW_ID_ROUTES", raising=False)
    from app.config import settings as _live_settings
    monkeypatch.setattr(_live_settings, "processed_dir", proc)
    import app.api.case_resolver as _cr
    monkeypatch.setattr(_cr.settings, "processed_dir", proc)
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(proc))
    global client
    client = TestClient(app.main.app)
    yield


class TestNoRawIdAcrossEndpoints:
    def _h(self, groups):
        return {"X-MS-CLIENT-PRINCIPAL": _principal(groups)}

    
    def test_list_cases_no_raw_ids(self):
        for role in (["ed-doctors"], ["researchers"], ["clinical-supervisors"]):
            r = client.get("/cases", headers=self._h(role))
            assert r.status_code == 200
            _assert_no_raw_ids(r.json(), "/cases")

    
    def test_get_case_no_raw_ids(self):
        cuid = client.get("/cases",
                          headers=self._h(["ed-doctors"])).json()["cases"][0]["case_uid"]
        r = client.get(f"/cases/{cuid}", headers=self._h(["ed-doctors"]))
        _assert_no_raw_ids(r.json(), "get_case")

    
    def test_assessment_no_raw_ids(self):
        cuid = client.get("/cases",
                          headers=self._h(["ed-doctors"])).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/assessments", headers=self._h(["ed-doctors"]))
        assert r.status_code == 200
        _assert_no_raw_ids(r.json(), "assessment")

    def test_followup_no_raw_ids(self):
        cuid = client.get("/cases",
                          headers=self._h(["ed-doctors"])).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/followups", headers=self._h(["ed-doctors"]),
                        json={"updated_vitals": {"heartrate": 180}})
        assert r.status_code == 200
        _assert_no_raw_ids(r.json(), "followup")

    def test_followup_multiagent_explanation_no_raw_ids(self, monkeypatch):
        async def _fake_team(case_uid, evidence, question=None):
            assert "followup_comparison" in evidence
            assert evidence["followup_comparison"]["changed_fields"] == ["heartrate"]
            return {
                "status": "PASS",
                "agent_turns": [{"agent": "ExplanationAgent", "text": "follow-up ok"}],
                "final_explanation": "Follow-up comparison explanation only.",
                "safety_failures": [],
            }

        monkeypatch.setattr(
            "app.agents.autogen_multi_agent_team.run_case_uid_team_explanation",
            _fake_team,
        )
        cuid = client.get("/cases",
                          headers=self._h(["ed-doctors"])).json()["cases"][0]["case_uid"]
        r = client.post(
            f"/cases/{cuid}/followups/multiagent-explanations",
            headers=self._h(["ed-doctors"]),
            json={"updated_vitals": {"heartrate": 180}, "question": "Why changed?"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["multiagent"] is True
        _assert_no_raw_ids(body, "followup_multiagent")

    def test_review_response_no_raw_ids(self):
        cuid = client.get("/cases",
                          headers=self._h(["ed-doctors"])).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews", headers=self._h(["ed-doctors"]),
                        json={"review_status": "ACCEPTED_AS_PRESENTED", "review_comment": "x"})
        assert r.status_code == 200
        _assert_no_raw_ids(r.json(), "review")


class TestFullMimicModelRouter:
    def test_full_mimic_no_model_fails_closed(self):
        from app.agents.ml_prediction_agent import run_ml_prediction
        from app.schemas.workflow import TriageTimeInput
        ti = TriageTimeInput(subject_id=1, stay_id=1,
                             source_dataset="MIMIC-IV-ED-Full-v2.2", chiefcomplaint="x")
        r = run_ml_prediction(ti)
        assert r.prediction_available is False
        assert "unavailable" in r.model_name

    def test_unknown_dataset_no_model(self):
        from app.agents.ml_prediction_agent import run_ml_prediction
        from app.schemas.workflow import TriageTimeInput
        ti = TriageTimeInput(subject_id=1, stay_id=1,
                             source_dataset="Mystery", chiefcomplaint="x")
        r = run_ml_prediction(ti)
        assert r.prediction_available is False

    def test_incompatible_artifact_refused(self, tmp_path, monkeypatch):
        import joblib
        from app.agents.ml_prediction_agent import run_ml_prediction
        from app.schemas.workflow import TriageTimeInput
        art = tmp_path / "m.joblib"
        joblib.dump({"model": object(), "sklearn_version": "0.1"}, art)
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        ti = TriageTimeInput(subject_id=1, stay_id=1,
                             source_dataset="MIMIC-IV-ED-Full-v2.2", chiefcomplaint="x")
        r = run_ml_prediction(ti)
        assert r.prediction_available is False
        assert "incompatible" in r.model_name


class TestCorsAndBackendRequirements:
    def test_cors_comma_list_wildcard_flagged(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*,https://example.com")
        from app.security.security_status import unsafe_combinations
        assert any("CORS" in p for p in unsafe_combinations())

    def test_cors_clean_list_ok(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://a.com,https://b.com")
        from app.security.security_status import unsafe_combinations
        assert not any("CORS" in p for p in unsafe_combinations())

    def test_fastapi_base_url_required_in_patient_mode(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.delenv("FASTAPI_BASE_URL", raising=False)
        monkeypatch.delenv("ALLOW_IN_PROCESS_BACKEND_FOR_PATIENT_DATA", raising=False)
        from frontend import api_client
        with pytest.raises(api_client.BackendError) as e:
            api_client.health()
        assert e.value.status_code == 503

    def test_in_process_override_allows_patient_mode(self, monkeypatch):
        from app.security import secrets_provider as _sp

        class _FakeKeyVault:
            def get_secret(self, name):
                assert name == "PSEUDONYM_SECRET"
                return "test-keyvault-salt"

        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("ALLOW_IN_PROCESS_BACKEND_FOR_PATIENT_DATA", "true")
        monkeypatch.setenv("AUDIT_SINK", "durable")
        monkeypatch.setenv("SECRETS_PROVIDER", "keyvault")
        monkeypatch.delenv("PSEUDONYM_SECRET", raising=False)
        monkeypatch.setattr(_sp, "get_secrets_provider", lambda: _FakeKeyVault())
        # health is unauthenticated and should work via in-process override
        from frontend import api_client
        h = api_client.health()
        assert h["clinical_use"] == "not_for_clinical_use"
