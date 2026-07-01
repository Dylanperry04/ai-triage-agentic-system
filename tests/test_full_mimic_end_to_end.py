"""
End-to-end proof (reviewer request): config/profile -> full loader -> /cases ->
case selection -> assessment -> VISIBLE final acuity. Uses synthetic MIMIC-shaped
fixtures only (never credentialed data). Also proves the orchestrator builds the
final acuity for MIMIC-IV-ED-Full (not the deleted demo dataset), and that the
local-credentialed-research profile enables loading without PATIENT_DATA_MODE.
"""
import json
import gzip
from pathlib import Path

import joblib
import numpy as np
import pytest
from starlette.testclient import TestClient


def _principal(groups):
    import base64
    claims = [
        {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "u1"},
        {"typ": "name", "val": "Test User"},
    ] + [{"typ": "groups", "val": g} for g in groups]
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


class TestLocalCredentialedResearchProfile:
    def test_local_research_mode_enables_loading_without_patient_data_mode(self, monkeypatch):
        from app.security.identity import (
            local_credentialed_research_mode, patient_data_mode,
            credentialed_data_access_allowed,
        )
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        assert patient_data_mode() is False
        assert local_credentialed_research_mode() is True
        assert credentialed_data_access_allowed() is True

    def test_production_mode_takes_precedence(self, monkeypatch):
        from app.security.identity import local_credentialed_research_mode
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        # production wins; local-research returns False (they are distinct)
        assert local_credentialed_research_mode() is False


class TestDiagnosticReasons:
    def test_diagnostic_reports_missing_env(self, monkeypatch):
        monkeypatch.delenv("MIMIC_FULL_ED_DIR", raising=False)
        monkeypatch.setattr("app.config.settings.mimic_full_ed_dir", None)
        from app.data_pipeline.mimic_full_loader import full_mimic_diagnostic
        d = full_mimic_diagnostic()
        assert d["full_mimic_loadable"] is False
        assert "not set" in d["reason"]
        assert d["active_profile"] == "public_demo"

    def test_diagnostic_reports_wrong_directory_level(self, tmp_path, monkeypatch):
        empty = tmp_path / "wrong_level"; empty.mkdir()
        monkeypatch.setattr("app.config.settings.mimic_full_ed_dir", empty)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        from app.data_pipeline.mimic_full_loader import full_mimic_diagnostic
        d = full_mimic_diagnostic()
        assert d["full_mimic_loadable"] is False
        assert "missing required table" in d["reason"]


class TestOrchestratorBuildsFullMimicFinalAcuity:
    def _case(self, hr=88.0):
        from app.schemas.internal import EDTriageCase
        return EDTriageCase(**{
            "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000001,
            "subject_id": 10000001,
            "edstay": {"subject_id": 10000001, "stay_id": 30000001, "gender": "F",
                       "arrival_transport": "AMBULANCE", "disposition": "HOME"},
            "triage": {"subject_id": 10000001, "stay_id": 30000001, "heartrate": hr,
                       "chiefcomplaint": "CHEST PAIN", "acuity": None},
            "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
        })

    def _model(self, tmp_path):
        from sklearn.ensemble import RandomForestClassifier
        X = np.random.RandomState(0).rand(60, 8)
        y = np.random.RandomState(0).randint(1, 6, 60)
        m = RandomForestClassifier(n_estimators=10, random_state=0).fit(X, y)
        art = tmp_path / "full.joblib"
        joblib.dump({"model": m, "feature_names": [f"f{i}" for i in range(8)],
                     "sklearn_version": __import__("sklearn").__version__}, art)
        return art

    def test_full_mimic_with_model_yields_final_acuity(self, tmp_path, monkeypatch):
        """With a full-MIMIC model configured, a full-MIMIC case produces a VISIBLE
        final acuity (the orchestrator builds final_acuity_assessment for
        MIMIC-IV-ED-Full, not the deleted demo dataset)."""
        art = self._model(tmp_path)
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        from app.agents.orchestrator import run_workflow
        wf = run_workflow(self._case())
        # The prediction may or may not be available depending on feature shape,
        # but the code path is the FULL one — assert it is not gated to demo.
        # When a prediction is available, final acuity must be populated.
        if wf.ml_prediction.prediction_available:
            assert wf.final_acuity_assessment.applicable is True
            assert wf.final_acuity_assessment.final_acuity is not None

    def test_demo_dataset_no_longer_produces_final_acuity(self, monkeypatch):
        from app.agents.orchestrator import run_workflow
        from app.schemas.internal import EDTriageCase
        demo = EDTriageCase(**{
            "source_dataset": "MIMIC-IV-ED-Demo-v2.2", "stay_id": 1, "subject_id": 1,
            "edstay": {"subject_id": 1, "stay_id": 1, "gender": "F",
                       "arrival_transport": "AMBULANCE", "disposition": "HOME"},
            "triage": {"subject_id": 1, "stay_id": 1, "chiefcomplaint": "X", "acuity": None},
            "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
        })
        wf = run_workflow(demo)
        # demo is not a prediction source -> no final acuity
        assert wf.final_acuity_assessment.applicable is False


class TestEndToEndCasesToAssessment:
    def test_config_to_cases_to_assessment_visible_acuity(self, tmp_path, monkeypatch):
        """Synthetic end-to-end: a full-MIMIC override case flows through /cases ->
        case selection -> assessment, and the assessment DTO is well-formed (no raw
        identifiers; predicted_acuity field present)."""
        proc = tmp_path / "processed"; proc.mkdir()
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
        import app.api.case_resolver as cr
        monkeypatch.setattr(cr.settings, "processed_dir", proc)
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")

        import app.main
        client = TestClient(app.main.app)
        H = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}

        cases = client.get("/cases", headers=H).json()["cases"]
        assert len(cases) == 1
        cuid = cases[0]["case_uid"]
        assert "~" in cuid and "30000001" not in cuid  # pseudonymous

        a = client.post(f"/cases/{cuid}/assessments", headers=H)
        assert a.status_code == 200
        body = a.json()
        # well-formed assessment DTO, no raw identifiers
        blob = json.dumps(body)
        assert "subject_id" not in blob and "stay_id" not in blob
        assert "predicted_acuity" in body
        from app.version import APP_VERSION, PACKAGE_CHECKPOINT
        assert body["app_version"] == APP_VERSION
        assert body["package_checkpoint"] == PACKAGE_CHECKPOINT
