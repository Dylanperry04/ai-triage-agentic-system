import pytest
from pydantic import ValidationError
from app.rules.mts_catalog import CHARTS, VERSION, catalog_hash, catalog_payload, propose_flowchart
from app.rules.mts_assessment import evaluate_mts_checklist
from app.schemas.mts import MTSChecklist


def form(chart="chest_pain", **updates):
    return {"catalog_version": VERSION, "catalog_sha256": catalog_hash(),
            "flowchart_id": chart, "flowchart_confirmed": True, **updates}


def absent(chart):
    result = {r["id"]: {"state": "assessed", "value": False} for r in CHARTS[chart]["rules"] if r["kind"] != "measurement_predicate"}
    if "on_supplemental_oxygen" in CHARTS[chart]["measurement_fields"]:
        result.update(o2sat={"state": "assessed", "value": 98}, on_supplemental_oxygen={"state": "assessed", "value": False})
    if "pefr_expected" in CHARTS[chart]["measurement_fields"]:
        result.update(pefr_measured={"state": "assessed", "value": 500}, pefr_expected={"state": "assessed", "value": 500})
    return result


def test_unrecorded_findings_never_produce_blue_or_imply_absence():
    result = evaluate_mts_checklist(MTSChecklist(**form()), "Chest pain")
    assert result["priority"] is None and result["unresolved_discriminators"]
    assert all(r["outcome"] == "unknown" for r in result["executed_rules"])
    assert evaluate_mts_checklist(None, "Chest pain")["status"] == "awaiting_nurse_checklist"


def test_higher_unknown_blocks_lower_category_but_red_can_short_circuit():
    finding = {"cardiac_pain": {"state": "assessed", "value": True}}
    result = evaluate_mts_checklist(MTSChecklist(**form(observations=finding)), "Chest pain")
    assert result["candidate_priority"] == 2 and result["priority"] is None
    finding["airway_compromise"] = {"state": "assessed", "value": True}
    result = evaluate_mts_checklist(MTSChecklist(**form(observations=finding)), "Chest pain")
    assert result["priority"] == 1
    assert result["decisive_discriminators"] == ["airway_compromise"]


def test_book_chest_pain_and_major_trauma_termination_are_distinct():
    observations = absent("chest_pain")
    observations["cardiac_pain"] = {"state": "assessed", "value": True}
    result = evaluate_mts_checklist(MTSChecklist(**form(observations=observations)), "Chest pain")
    assert result["priority"] == 2
    assert result["selected_flowchart"]["source_page"] == 94
    assert evaluate_mts_checklist(MTSChecklist(**form(observations=absent("chest_pain"))), "Chest pain")["priority"] == 5
    result = evaluate_mts_checklist(MTSChecklist(**form("major_trauma", observations=absent("major_trauma"))), "Major trauma")
    assert result["priority"] is None and result["status"] == "select_another_flowchart"


def test_unknown_earlier_finding_in_same_band_leaves_only_the_urgency_floor():
    observations = absent("chest_pain")
    observations.pop("aortic_pain")
    observations["cardiac_pain"] = {"state": "assessed", "value": True}
    result = evaluate_mts_checklist(MTSChecklist(**form(observations=observations)), "Chest pain")
    assert result["candidate_priority"] == 2
    assert result["status"] == "incomplete_earlier_ordered_observations"
    assert result["priority"] is None and not result["assessment_complete"]
    assert result["unresolved_discriminators"] == ["aortic_pain"]
    assert result["earliest_known_match"] == "cardiac_pain"
    assert result["determining_discriminator"] is None
    assert not result["decisive_discriminators"]

    observations["aortic_pain"] = {"state": "assessed", "value": True}
    result = evaluate_mts_checklist(MTSChecklist(**form(observations=observations)), "Chest pain")
    assert result["candidate_priority"] == result["priority"] == 2
    assert result["assessment_complete"]
    assert result["determining_discriminator"] == "aortic_pain"


@pytest.mark.parametrize("change", [
    {"flowchart_confirmed": False}, {"catalog_sha256": "old"}, {"flowchart_id": "not_a_chart"},
    {"observations": {"cardiac_pain": {"state": "assessed", "value": 1}}},
    {"observations": {"cardiac_pain": {"state": "source_recorded", "value": True}}},
    {"observations": {"o2sat": {"state": "assessed", "value": 101}}},
    {"observations": {"o2sat": {"state": "unavailable", "value": 95}}},
    {"observations": {"shock": {"state": "not_applicable"}}},
    {"observations": {"unknown_observation": {"state": "assessed", "value": True}}},
])
def test_invalid_and_ambiguous_observations_are_rejected(change):
    with pytest.raises(ValidationError):
        MTSChecklist(**form(**change))


def test_mapping_does_not_guess_negation_or_ambiguous_injuries():
    assert propose_flowchart(" Chest   Pain ")["flowchart_id"] == "chest_pain"
    assert propose_flowchart("No chest pain; abdominal discomfort")["flowchart_id"] is None
    assert propose_flowchart("Trauma")["flowchart_id"] is None
    assert propose_flowchart("Limb injury")["flowchart_id"] == "limb_problems"


def test_catalog_distinguishes_shared_inputs_from_chart_specific_context():
    catalog = catalog_payload()
    assert len(catalog["model_measurement_fields"]) == 7
    chest = catalog["flowcharts"]["chest_pain"]
    respiratory = catalog["flowcharts"]["shortness_of_breath_adults"]
    assert chest["measurement_context_fields"] == ["on_supplemental_oxygen"]
    assert set(respiratory["measurement_context_fields"]) == {
        "on_supplemental_oxygen", "pefr_measured", "pefr_expected"
    }


@pytest.mark.parametrize("saturation,oxygen,very_low,low", [
    (91.99, False, True, True), (92, False, False, True),
    (94.99, True, True, False), (95, True, False, False),
    (94.99, False, False, True), (95, False, False, False),
])
def test_source_defined_oxygen_thresholds_require_assessed_delivery_context(saturation, oxygen, very_low, low):
    observations = {"o2sat": {"state": "assessed", "value": saturation},
                    "on_supplemental_oxygen": {"state": "assessed", "value": oxygen}}
    result = evaluate_mts_checklist(MTSChecklist(**form(observations=observations)), "Chest pain")
    assert ("very_low_spo2" in result["matched_discriminators"]) is very_low
    assert ("low_spo2" in result["matched_discriminators"]) is low
    observations.pop("on_supplemental_oxygen")
    result = evaluate_mts_checklist(MTSChecklist(**form(observations=observations)), "Chest pain")
    assert "very_low_spo2" not in result["matched_discriminators"]


@pytest.mark.parametrize("temperature,matched", [(37.5, []), (37.51, ["warm"]),
    (38.5, ["hot", "warm"]), (41, ["very_hot", "hot", "warm"])])
def test_temperature_boundaries_follow_definition_and_source_rows_are_not_nursing_assessments(temperature, matched):
    observations = {"temperature": {"state": "assessed", "value": temperature}}
    args = form("abdominal_pain_adults", observations=observations)
    result = evaluate_mts_checklist(MTSChecklist(**args), "Abdominal pain")
    assert set(result["matched_discriminators"]) == set(matched)
    observations["temperature"]["state"] = "source_recorded"
    result = evaluate_mts_checklist(MTSChecklist(**args), "Abdominal pain")
    assert result["matched_discriminators"] == []


@pytest.mark.parametrize("measured,very_low,low", [(329, True, True), (330, False, True), (500, False, False)])
def test_pefr_needs_a_genuine_expected_or_personal_best_measurement(measured, very_low, low):
    observations = {"pefr_measured": {"state": "assessed", "value": measured},
                    "pefr_expected": {"state": "assessed", "value": 1000}}
    args = form("shortness_of_breath_adults", observations=observations)
    result = evaluate_mts_checklist(MTSChecklist(**args), "Shortness of breath")
    assert ("very_low_pefr" in result["matched_discriminators"]) is very_low
    assert ("low_pefr" in result["matched_discriminators"]) is low


def test_ordered_determining_discriminator_and_conflicts_are_not_completed():
    observations = absent('chest_pain')
    observations.update(airway_compromise={'state': 'assessed', 'value': True}, shock={'state': 'assessed', 'value': True})
    result = evaluate_mts_checklist(MTSChecklist(**form(observations=observations)), 'Chest pain')
    assert result['matched_discriminators'] == ['airway_compromise', 'shock']
    assert result['decisive_discriminators'] == ['airway_compromise']
    observations = absent('abdominal_pain_adults')
    observations.update(shock={'state': 'assessed', 'value': True}, temperature={'state': 'assessed', 'value': 41}, very_hot={'state': 'assessed', 'value': False})
    result = evaluate_mts_checklist(MTSChecklist(**form('abdominal_pain_adults', observations=observations)), 'Abdominal pain')
    assert result['priority'] is None and result['assessment_complete'] is False
    assert result['candidate_priority'] == 1
    assert 'very_hot' in result['conflicting_observations']
