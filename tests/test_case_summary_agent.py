from app.schemas.internal import TriageTimeInput
from app.agents.data_validation_agent import run_data_validation_agent
from app.agents.case_summary_agent import run_case_summary_agent


def test_case_summary_uses_triage_input_only():
    triage_input = TriageTimeInput(
        subject_id=1,
        stay_id=2,
        arrival_transport="AMBULANCE",
        chiefcomplaint="Chest pain",
        temperature=98.6,
        heartrate=90,
        resprate=18,
        o2sat=98,
        sbp=120,
        dbp=80,
        pain="5",
    )

    validation = run_data_validation_agent(triage_input)
    summary = run_case_summary_agent(triage_input, validation)

    assert summary.summary_status == "SUMMARY_READY_FOR_HUMAN_REVIEW"
    assert summary.chief_complaint == "Chest pain"
    assert "Chest pain" in summary.summary_text
    assert "does not assign a Manchester category" in summary.safety_note


def test_case_summary_reports_missing_data_limitations():
    triage_input = TriageTimeInput(
        subject_id=1,
        stay_id=2,
        chiefcomplaint="UNKNOWN-CC",
    )

    validation = run_data_validation_agent(triage_input)
    summary = run_case_summary_agent(triage_input, validation)

    assert summary.summary_status == "SUMMARY_WITH_DATA_LIMITATIONS"
    assert summary.missing_or_limited_data
    assert summary.human_review_required is True