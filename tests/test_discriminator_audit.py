"""Regression evidence for discriminator display and the October code audit."""
import pytest
from pydantic import ValidationError

from app.agents.followup_comparison_agent import _compare_vital, compare_follow_up
from app.agents.ml_prediction_agent import _select_uhl_serving_acuity
from app.agents.orchestrator import run_workflow
from app.agents.safety_review_agent import run_safety_review
from app.api.safe_dto import safe_assessment_response, safe_multiagent_evidence, assert_no_raw_identifiers
from app.constants import DATASET_SOURCE
from app.rules.acuity_mts_mapping import map_acuity_to_mts
from app.rules.acuity_override import apply_acuity_override
from app.rules.discriminators import evaluate_vital_discriminators
from app.rules.manchester_engine import run_manchester_engine
from app.rules.provisional_mts_ruleset import register_provisional_ruleset
from app.schemas.followup import FollowUpLinkRequest
from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource, TriageTimeInput
from app.schemas.workflow import MLPredictionResult
from app.schemas.workflow_run import build_workflow_run_record


def triage(**changes):
    return TriageTimeInput(**(dict(subject_id=1, stay_id=1, age=40,
        intime="2026-10-25T12:00:00Z", chiefcomplaint="OTHER", temperature=37,
        temperature_unit="C", heartrate=80, resprate=16, o2sat=98,
        sbp=120, dbp=80, pain="2") | changes))


def workflow(monkeypatch, *, acuity=4, **changes):
    prediction = MLPredictionResult(
        prediction_available=acuity is not None, predicted_mimic_acuity=acuity,
        argmax_acuity=4 if acuity is not None else None,
        decision_rule_type="modal_with_urgent_class_threshold",
        decision_rule_threshold=0.25,
        decision_rule_changed_prediction=acuity not in (4, None),
    )
    monkeypatch.setattr("app.agents.orchestrator.run_ml_prediction", lambda _: prediction)
    ti = triage(**changes)
    case = EDTriageCase(subject_id=1, stay_id=1,
        edstay=EDStaySource(subject_id=1, stay_id=1, intime=ti.intime),
        triage=TriageSource(**ti.model_dump()))
    return run_workflow(case)


@pytest.mark.parametrize("field,boundary,below,at,above", [
    ("o2sat", 85, 1, 2, 2), ("o2sat", 90, 2, 4, 4),
    ("heartrate", 35, 1, 2, 2), ("heartrate", 40, 2, 4, 4),
    ("heartrate", 130, 4, 4, 2), ("heartrate", 150, 2, 2, 1),
    ("resprate", 6, 1, 2, 2), ("resprate", 8, 2, 4, 4),
    ("resprate", 29, 4, 4, 2), ("resprate", 35, 2, 2, 1),
    ("sbp", 80, 1, 2, 2), ("sbp", 90, 2, 4, 4),
    ("sbp", 220, 4, 4, 2), ("temperature", 32, 1, 2, 2),
    ("temperature", 35, 2, 4, 4), ("temperature", 41, 4, 2, 2),
    ("temperature", 41.5, 2, 1, 1),
])
def test_override_boundaries_and_display_evidence_agree(field, boundary, below, at, above):
    for value, expected in [(boundary - .01, below), (boundary, at), (boundary + .01, above)]:
        result = apply_acuity_override(4, triage(**{field: value}))
        assert result["final_acuity"] == expected
        matched = [item for item in result["evaluated_discriminators"] if item["status"] == "matched"]
        assert min([4] + [item["priority"] for item in matched]) == expected
        assert all(item["observed_value"] == value for item in matched)


def test_celsius_fahrenheit_same_evidence():
    celsius = evaluate_vital_discriminators(triage(temperature=41.5))
    fahrenheit = evaluate_vital_discriminators(triage(temperature=106.7, temperature_unit="F"))
    assert [(x["code"], x["status"]) for x in celsius] == [(x["code"], x["status"]) for x in fahrenheit]


@pytest.mark.parametrize("acuity", [1, 2, 3, 4, 5])
@pytest.mark.parametrize("observations,floor", [
    ({}, None), ({"heartrate": 140}, 2), ({"heartrate": 30}, 1),
    ({"resprate": 36}, 1), ({"resprate": 5}, 1),
    ({"o2sat": 84}, 1), ({"sbp": 79}, 1), ({"sbp": 221}, 2),
    ({"temperature": 41.2}, 2), ({"temperature": 31}, 1),
])
def test_safety_layer_never_downgrades_or_creates_acuity_five(acuity, observations, floor):
    result = apply_acuity_override(acuity, triage(**observations))
    expected = min(acuity, floor) if floor is not None else acuity
    assert result["final_acuity"] == expected
    assert result["final_acuity"] <= acuity
    assert result["override_applied"] is (expected < acuity)
    if expected == 5:
        assert acuity == 5 and floor is None


def test_decisive_matches_all_matches_and_missing_are_distinct(monkeypatch):
    result = workflow(monkeypatch, o2sat=80, heartrate=140, sbp=None)
    evidence = result.discriminator_evidence
    assert [x["code"] for x in evidence["decisive"]] == ["EXTREME_HYPOXIA_SPO2_BELOW_85"]
    assert {x["code"] for x in evidence["matched"]} == {
        "EXTREME_HYPOXIA_SPO2_BELOW_85", "CRITICAL_HYPOXIA_SPO2_BELOW_90", "CRITICAL_HEART_RATE_ABOVE_130"}
    assert evidence["missing_observations"] == ["sbp"]
    assert all(x["status"] == "not_assessed" for x in evidence["evaluated"] if x["field"] == "sbp")
    assert evidence["is_official_mts"] is False
    response = safe_assessment_response("test~token", DATASET_SOURCE, result.model_dump(mode="json"))
    assert response["discriminator_evidence"] == evidence
    explainer = safe_multiagent_evidence("test~token", DATASET_SOURCE, result.model_dump(mode="json"))
    assert explainer["discriminator_evidence"] == evidence
    assert all("Rules reason code:" not in driver for driver in explainer["category_reasoning"]["primary_drivers"])
    assert explainer["category_reasoning"]["parallel_safety_context"]["reason_codes"] == result.decision.reason_codes
    assert_no_raw_identifiers(response)
    record = build_workflow_run_record(result, "test-run", "2026-10-25T12:00:00Z")
    assert record.discriminator_evidence == evidence
    assert record.explanation_evidence == {**explainer, "case_uid": record.case_uid}
    assert record.llm_status == "NOT_REQUESTED"
    assert record.mapping_rule_version == result.final_acuity_assessment.mapping_rule_version


@pytest.mark.parametrize("acuity", [1, None])
def test_flags_do_not_claim_to_change_an_already_urgent_or_unavailable_model(monkeypatch, acuity):
    result = workflow(monkeypatch, acuity=acuity, o2sat=80)
    evidence = result.discriminator_evidence
    assert evidence["matched"] and not evidence["decisive"]
    assert evidence["override_applied"] is False
    if acuity is None:
        assert result.final_acuity_assessment.final_acuity is None


def test_normal_rules_dont_invent_model_discriminators(monkeypatch):
    result = workflow(monkeypatch, acuity=3)
    evidence = result.discriminator_evidence
    assert evidence["matched"] == evidence["decisive"] == []
    assert evidence["model_policy"]["changed_prediction"] is True
    assert evidence["model_policy"]["threshold"] == .25
    assert evidence["pathway"]["affects_displayed_acuity"] is False


@pytest.mark.parametrize("registered", [False, True])
def test_unrecognised_complaint_does_not_discard_concern(registered):
    if registered:
        register_provisional_ruleset()
    result = run_manchester_engine(triage(o2sat=92))
    assert "CONCERN_SPO2_90_TO_94" in result.reason_codes
    if registered:
        assert result.priority == 2
    else:
        assert result.classification_status == "PHYSIOLOGY_CONCERN_FLAGGED"


def test_concern_is_retained_even_when_pathway_already_urgent():
    register_provisional_ruleset()
    result = run_manchester_engine(triage(chiefcomplaint="chest pain", pain="8", o2sat=92))
    assert result.priority == 2
    assert "CONCERN_SPO2_90_TO_94" in result.reason_codes


def test_concern_survives_missing_pain():
    register_provisional_ruleset()
    result = run_manchester_engine(triage(chiefcomplaint="pain", pain=None, o2sat=92))
    assert result.classification_status == "PHYSIOLOGY_CONCERN_FLAGGED"
    assert "CONCERN_SPO2_90_TO_94" in result.reason_codes


def test_safety_review_includes_existing_high_systolic_threshold():
    assert run_safety_review(triage(sbp=221)).is_safe_to_present is False
    assert run_safety_review(triage(sbp=220)).is_safe_to_present is True


@pytest.mark.parametrize("pain", ["inf", "-inf", "nan", "not recorded"])
def test_unusable_pain_does_not_crash(pain):
    register_provisional_ruleset()
    assert run_manchester_engine(triage(pain=pain)).requires_clinician_review
    assert run_safety_review(triage(pain=pain)) is not None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("schema", [TriageTimeInput, TriageSource])
def test_nonfinite_observations_are_rejected(value, schema):
    with pytest.raises(ValidationError):
        schema(subject_id=1, stay_id=1, heartrate=value)


def test_temperature_units_validated_before_safety_evaluation():
    assert triage(temperature_unit=" fahrenheit ").temperature_unit == "F"
    with pytest.raises(ValidationError):
        triage(temperature_unit="kelvin")


@pytest.mark.parametrize("value", [True, 3.7, float("inf"), float("nan"), 0, 6])
def test_display_mapping_never_truncates_invalid_acuity(value):
    assert map_acuity_to_mts(value) is None


@pytest.mark.parametrize("probabilities", [
    {"1": .1}, {"1": float("nan"), "2": .2, "3": .2, "4": .2, "5": .2},
    {"1": -.1, "2": .2, "3": .3, "4": .3, "5": .3},
    {"1": 1., "2": 1., "3": 1., "4": 1., "5": 1.},
])
def test_invalid_probability_vectors_fail_closed(probabilities):
    with pytest.raises(ValueError):
        _select_uhl_serving_acuity(probabilities)


def test_uhl_followup_reports_model_only_escalation(monkeypatch):
    previous = workflow(monkeypatch, acuity=4)
    new = workflow(monkeypatch, acuity=2)
    result = compare_follow_up(FollowUpLinkRequest(previous_stay_id=1, new_stay_id=2, linked_by="tester"), previous, new)
    assert result.escalation_detected is True
    assert result.category_movement == "ESCALATION"
    assert "acuity changed from 4 to 2" in result.escalation_note


def test_newly_available_critical_vital_is_not_routine():
    delta = _compare_vital("o2sat", "%", None, 80)
    assert delta.direction == "NEWLY_AVAILABLE" and delta.clinically_notable
    assert not _compare_vital("o2sat", "%", None, 98).clinically_notable


def test_unavailable_model_does_not_display_separate_pathway_as_final(monkeypatch):
    register_provisional_ruleset()
    result = workflow(monkeypatch, acuity=None, chiefcomplaint="chest pain")
    response = safe_assessment_response("test~token", DATASET_SOURCE, result.model_dump(mode="json"))
    assert response["predicted_acuity"] is None
    assert response["final_category"] is None
    assert response["predicted_category"] is None
    assert response["rules_category"] is not None
