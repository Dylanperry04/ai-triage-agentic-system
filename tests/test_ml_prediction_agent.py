"""
Tests for the ML Research Prediction Agent (app/agents/ml_prediction_agent.py).

ARCHITECTURE (v13-final): the ONLY live prediction path is full MIMIC-IV-ED
(credentialed), routed to the full-MIMIC model via MIMIC_FULL_MODEL_PATH and
failing closed when that model is absent/incompatible. The retired demo/KTAS
datasets are no longer prediction sources; any non-full-MIMIC dataset withholds a
prediction (no extrapolation). These tests use synthetic inputs only and never a
real trained model (the full model lives only on the credentialed environment).
"""
from __future__ import annotations

import pytest

from app.agents.ml_prediction_agent import run_ml_prediction
from app.schemas.internal import TriageTimeInput


def _make_input(**kwargs) -> TriageTimeInput:
    defaults = {
        "subject_id": 1, "stay_id": 1,
        "source_dataset": "MIMIC-IV-ED-Full-v2.2",
        "chiefcomplaint": "chest pain",
        "temperature": 36.7, "temperature_unit": "C", "heartrate": 80.0,
        "resprate": 16.0, "o2sat": 98.0,
        "sbp": 120.0, "dbp": 80.0, "pain": "5",
    }
    defaults.update(kwargs)
    return TriageTimeInput(**defaults)


@pytest.fixture(autouse=True)
def _no_full_model(monkeypatch):
    # Ensure no full-MIMIC model is configured in the test environment, so the
    # full-MIMIC path exercises its fail-closed behaviour deterministically.
    monkeypatch.delenv("MIMIC_FULL_MODEL_PATH", raising=False)
    yield


class TestOnlyFullMimicIsAPredictionSource:
    def test_full_mimic_without_model_fails_closed(self):
        r = run_ml_prediction(_make_input(source_dataset="MIMIC-IV-ED-Full-v2.2"))
        assert r.prediction_available is False
        assert "unavailable" in r.model_name
        # never a number when the model is absent
        assert r.predicted_mimic_acuity is None
        assert r.predicted_ktas_class is None

    def test_retired_demo_dataset_gets_no_prediction(self):
        r = run_ml_prediction(_make_input(source_dataset="MIMIC-IV-ED-Demo-v2.2"))
        assert r.prediction_available is False
        assert r.model_name == "no_model_for_dataset"
        assert r.predicted_mimic_acuity is None
        assert r.predicted_ktas_class is None

    def test_retired_ktas_dataset_gets_no_prediction(self):
        r = run_ml_prediction(_make_input(source_dataset="Kaggle-KTAS"))
        assert r.prediction_available is False
        assert r.model_name == "no_model_for_dataset"
        assert r.predicted_ktas_class is None

    def test_unknown_dataset_gets_no_prediction(self):
        r = run_ml_prediction(_make_input(source_dataset="Something-Else"))
        assert r.prediction_available is False
        assert r.model_name == "no_model_for_dataset"

    def test_note_directs_to_full_mimic_only(self):
        r = run_ml_prediction(_make_input(source_dataset="Kaggle-KTAS"))
        assert "full MIMIC-IV-ED" in r.model_note
        assert "clinician review required" in r.model_note.lower()


class TestFullMimicModelRouterFailClosed:
    def test_incompatible_artifact_refused(self, tmp_path, monkeypatch):
        import joblib
        art = tmp_path / "m.joblib"
        joblib.dump({"model": object(), "sklearn_version": "0.1"}, art)
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        r = run_ml_prediction(_make_input(source_dataset="MIMIC-IV-ED-Full-v2.2"))
        assert r.prediction_available is False
        assert "incompatible" in r.model_name

    def test_missing_artifact_path_fails_closed(self, monkeypatch):
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", "/nonexistent/model.joblib")
        r = run_ml_prediction(_make_input(source_dataset="MIMIC-IV-ED-Full-v2.2"))
        assert r.prediction_available is False
        assert "unavailable" in r.model_name

    def test_demo_model_never_substituted_for_full_mimic(self, monkeypatch):
        # Even if a (demo-shaped) artifact is configured but incompatible/empty,
        # the router must never silently fall back to it for full-MIMIC cases.
        monkeypatch.delenv("MIMIC_FULL_MODEL_PATH", raising=False)
        r = run_ml_prediction(_make_input(source_dataset="MIMIC-IV-ED-Full-v2.2"))
        assert r.prediction_available is False
        assert r.predicted_mimic_acuity is None
