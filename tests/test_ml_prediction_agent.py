"""
Tests for the ML Research Prediction Agent (app/agents/ml_prediction_agent.py).

The central test here is TestSourceDatasetGate, a regression guard for a
real safety gap found during a later review pass: run_ml_prediction()
previously had no awareness of source_dataset at all and applied the
KTAS-trained model to any case it was given, including real MIMIC demo
cases -- producing a number that looked like a real prediction
(predicted_ktas_class rendered in bold under "Research model output" in
the assessment card) with no per-case disclosure that the model was
never trained on or validated against that case's actual dataset. The
existing model_note only ever stated the generic "KTAS is not Manchester"
caveat, and that note was not even rendered in the UI path that shows a
successful prediction in the first place.

Fixed by checking source_dataset first, before the registry is even
loaded, and returning prediction_available=False with an explicit,
dataset-specific note for any non-KTAS case. This file had no test
coverage at all before this fix -- exactly how the gap went unnoticed.
"""
from __future__ import annotations

import json

import pytest

from app.agents.ml_prediction_agent import run_ml_prediction
from app.config import settings
from app.schemas.internal import TriageTimeInput


def _make_input(**kwargs) -> TriageTimeInput:
    defaults = {
        "subject_id": 1, "stay_id": 1,
        "source_dataset": "Kaggle-KTAS",
        "chiefcomplaint": "chest pain",
        "temperature": 36.7, "temperature_unit": "C", "heartrate": 80.0,
        "resprate": 16.0, "o2sat": 98.0,
        "sbp": 120.0, "dbp": 80.0, "pain": "5",
    }
    defaults.update(kwargs)
    return TriageTimeInput(**defaults)


@pytest.fixture
def real_registry_present():
    """
    Skips a test if the real, trained model registry has not been built
    in this environment (e.g. a fresh checkout before
    scripts/run_ktas_pipeline.py has ever run) -- these tests verify
    real behaviour against the real, trained model, not a mock, so they
    need the real registry to exist.
    """
    if not settings.model_registry_path.exists():
        pytest.skip(
            "No trained model registry found -- run "
            "scripts/run_ktas_pipeline.py first."
        )


class TestSourceDatasetGate:
    """
    The core regression guard for this file. Every test here checks the
    gate directly against real source_dataset values, not a mock.
    """

    def test_ktas_case_still_gets_a_real_prediction(self, real_registry_present):
        t = _make_input(source_dataset="Kaggle-KTAS")
        result = run_ml_prediction(t)
        assert result.prediction_available is True
        assert result.predicted_ktas_class is not None
        assert 1 <= result.predicted_ktas_class <= 5

    def test_mimic_case_gets_an_acuity_prediction_not_a_ktas_one(self, real_registry_present):
        t = _make_input(source_dataset="MIMIC-IV-ED-Demo-v2.2")
        result = run_ml_prediction(t)
        assert result.prediction_available is True
        assert result.prediction_scale == "MIMIC_ACUITY_MAPPED_TO_MTS"
        assert result.predicted_mimic_acuity is not None
        assert 1 <= result.predicted_mimic_acuity <= 5
        # The predicted acuity must be mapped to MTS-style display fields.
        assert result.mapped_mts_category is not None
        assert result.mapped_mts_priority is not None
        assert result.mapped_mts_max_wait_minutes is not None
        # And it must NOT carry KTAS fields.
        assert result.predicted_ktas_class is None
        assert result.ktas_class_probabilities == {}

    def test_mimic_mapping_matches_the_central_mapping_table(self, real_registry_present):
        from app.rules.acuity_mts_mapping import map_acuity_to_mts
        t = _make_input(source_dataset="MIMIC-IV-ED-Demo-v2.2")
        result = run_ml_prediction(t)
        expected = map_acuity_to_mts(result.predicted_mimic_acuity)
        assert result.mapped_mts_category == expected["category"]
        assert result.mapped_mts_priority == expected["priority"]
        assert result.mapped_mts_max_wait_minutes == expected["max_wait_minutes"]
        assert result.mapped_mts_colour == expected["colour"]

    def test_unknown_non_ktas_non_mimic_dataset_gets_no_prediction(
        self, real_registry_present
    ):
        """
        Any dataset that is neither Kaggle-KTAS nor MIMIC-IV-ED Demo (e.g. the
        future full credentialed MIMIC, not yet in scope) must be withheld --
        the dispatch is an allowlist, so a future adapter cannot accidentally
        bypass it.
        """
        t = _make_input(source_dataset="Some-Future-Dataset-Nobody-Has-Built-Yet")
        result = run_ml_prediction(t)
        assert result.prediction_available is False
        assert "Some-Future-Dataset-Nobody-Has-Built-Yet" in result.model_note

    def test_datasets_are_never_mixed(self, real_registry_present):
        """KTAS result never carries MIMIC fields and vice versa."""
        k = run_ml_prediction(_make_input(source_dataset="Kaggle-KTAS"))
        m = run_ml_prediction(_make_input(source_dataset="MIMIC-IV-ED-Demo-v2.2"))
        assert k.predicted_ktas_class is not None and k.mapped_mts_category is None
        assert m.mapped_mts_category is not None and m.predicted_ktas_class is None


class TestNoRegistryOrModel:
    """
    Coverage for the pre-existing (and previously untested) branches that
    run when no trained model exists at all -- distinct from the
    source_dataset gate, but worth covering now that this file exists.
    """

    def test_no_registry_file_returns_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "model_registry_path", tmp_path / "does_not_exist.json")
        t = _make_input(source_dataset="Kaggle-KTAS")
        result = run_ml_prediction(t)
        assert result.prediction_available is False
        assert "No trained KTAS model registry found" in result.model_note

    def test_registry_present_but_model_file_missing(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "best_ktas_model": {
                        "name": "RandomForest",
                        "version": "test.1.0",
                        "path": "does_not_exist.pkl",
                    }
                }
            )
        )
        monkeypatch.setattr(settings, "model_registry_path", registry_path)
        monkeypatch.setattr(settings, "models_dir", tmp_path)

        t = _make_input(source_dataset="Kaggle-KTAS")
        result = run_ml_prediction(t)
        assert result.prediction_available is False
        assert "KTAS model file not found" in result.model_note
