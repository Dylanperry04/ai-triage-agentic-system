from app.api.safe_dto import safe_followup_response


def test_followup_response_includes_manchester_equivalents_and_summary():
    out = safe_followup_response(
        "case_abc",
        4,
        3,
        changed_fields=["heartrate", "o2sat"],
        changed_vitals=[
            {"field": "heartrate", "previous": 88, "new": 130},
            {"field": "o2sat", "previous": 98, "new": 89},
        ],
    )
    assert out["previous_acuity"] == 4
    assert out["previous_manchester_equivalent"]["category"] == "Standard (Green)"
    assert out["previous_manchester_equivalent"]["colour"] == "green"
    assert out["new_acuity"] == 3
    assert out["new_manchester_equivalent"]["category"] == "Urgent (Yellow)"
    assert out["new_manchester_equivalent"]["colour"] == "yellow"
    assert out["change"] == "escalation"
    assert out["change_direction"] == "escalation"
    assert out["change_summary"] == (
        "Escalating from Acuity 4 / Standard (Green) to "
        "Acuity 3 / Urgent (Yellow)."
    )
    assert out["changed_fields"] == ["heartrate", "o2sat"]
    assert out["changed_vitals"][0]["field"] == "heartrate"
    assert out["clinician_review_required"] is True
    assert out["not_for_clinical_use"] is True
