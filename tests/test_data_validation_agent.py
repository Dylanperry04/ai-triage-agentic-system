from app.schemas.internal import TriageTimeInput
from app.agents.data_validation_agent import run_data_validation_agent


def test_complete_triage_input_passes_validation():
    triage_input = TriageTimeInput(
        subject_id=1,
        stay_id=2,
        chiefcomplaint="Chest pain",
        temperature=98.6,
        heartrate=90,
        resprate=18,
        o2sat=98,
        sbp=120,
        dbp=80,
        pain="5",
    )

    result = run_data_validation_agent(triage_input)

    assert result.validation_status == "TRIAGE_INPUT_DATA_COMPLETE"
    assert result.requires_human_data_review is False
    assert result.missing_required_fields == []


def test_missing_vitals_requires_human_review():
    triage_input = TriageTimeInput(
        subject_id=1,
        stay_id=2,
        chiefcomplaint="Chest pain",
        pain="5",
    )

    result = run_data_validation_agent(triage_input)

    assert result.validation_status == "NEEDS_HUMAN_DATA_REVIEW"
    assert result.requires_human_data_review is True
    assert "temperature" in result.missing_required_fields
    assert "heartrate" in result.missing_required_fields
    assert "o2sat" in result.missing_required_fields


def test_unknown_chief_complaint_requires_human_review():
    triage_input = TriageTimeInput(
        subject_id=1,
        stay_id=2,
        chiefcomplaint="UNKNOWN-CC",
        temperature=98.6,
        heartrate=90,
        resprate=18,
        o2sat=98,
        sbp=120,
        dbp=80,
        pain="0",
    )

    result = run_data_validation_agent(triage_input)

    assert result.validation_status == "NEEDS_HUMAN_DATA_REVIEW"
    assert "chiefcomplaint" in result.non_informative_fields