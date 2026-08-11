from __future__ import annotations

import hashlib

from app.agents.ml_prediction_agent import _select_uhl_serving_acuity, run_ml_prediction
from app.api import case_resolver
from app.api.status_routes import _uhl_model_performance_payload
from app.config import settings
from app.constants import DATASET_SHA256, DATASET_SOURCE, MODEL_SHA256
from app.schemas.internal import EDTriageCase


def test_pinned_uhl_assets_are_the_active_source():
    assert settings.default_dataset == "uhl"
    assert hashlib.sha256(settings.uhl_data_path.read_bytes()).hexdigest() == DATASET_SHA256
    assert hashlib.sha256(settings.uhl_model_path.read_bytes()).hexdigest() == MODEL_SHA256


def test_uhl_repository_serves_22_4_case_contract_and_resolves_directly():
    rows = case_resolver.list_cases("uhl", limit=1)
    assert len(rows) == 1
    row = rows[0]
    assert row.source_dataset == DATASET_SOURCE
    assert row.case_uid.startswith(f"{DATASET_SOURCE}~")
    assert case_resolver.resolve(row.case_uid) is not None
    case = EDTriageCase(**row.case)
    assert case.triage is not None
    assert case.triage.chiefcomplaint
    assert case.edstay.intime


def test_uhl_model_predicts_through_original_22_4_agent_seam():
    row = case_resolver.list_cases("uhl", limit=1)[0]
    triage_input = EDTriageCase(**row.case).to_triage_time_input()
    result = run_ml_prediction(triage_input)
    assert result.prediction_available is True
    assert result.predicted_mimic_acuity in {1, 2, 3, 4, 5}
    assert result.model_name == "catboost"
    assert result.decision_rule_type == "modal_with_urgent_class_threshold"
    assert result.decision_rule_threshold == 0.25
    assert set(result.mimic_acuity_probabilities) == {"1", "2", "3", "4", "5"}


def test_uhl_serving_rule_uses_modal_unless_more_urgent_class_reaches_25_percent():
    screenshot_probabilities = {
        "1": 0.024,
        "2": 0.121,
        "3": 0.310,
        "4": 0.420,
        "5": 0.125,
    }
    assert _select_uhl_serving_acuity(screenshot_probabilities) == 3
    assert _select_uhl_serving_acuity(
        {"1": 0.10, "2": 0.24, "3": 0.20, "4": 0.40, "5": 0.06}
    ) == 4
    assert _select_uhl_serving_acuity(
        {"1": 0.25, "2": 0.26, "3": 0.10, "4": 0.30, "5": 0.09}
    ) == 1


def test_uhl_reports_feed_existing_model_visualisation_contract():
    payload = _uhl_model_performance_payload()
    assert payload["status"] == "available"
    assert payload["dataset"] == DATASET_SOURCE
    assert payload["model_readiness_valid"] is True
    card = payload["artefacts"]["model_card"]
    assert card["model_kind"] == "catboost"
    assert card["headline_metrics"]["high_acuity_recall"] > 0.95
    assert payload["artefacts"]["confusion_matrix"]["confusion_matrix"]
