"""
Case Summary Agent.

Builds a structured, non-diagnostic text summary of triage-time evidence only.
Does not call an LLM and does not assign a clinical category.
"""
from app.schemas.internal import TriageTimeInput
from app.schemas.validation import DataValidationResult
from app.schemas.summary import CaseSummaryResult


def _fmt(label: str, value) -> str:
    if value is None or value == "":
        return f"{label}: MISSING"
    return f"{label}: {value}"


def run_case_summary_agent(
    triage_input: TriageTimeInput,
    data_validation: DataValidationResult,
) -> CaseSummaryResult:
    temp_label = f"Temperature (°{triage_input.temperature_unit or 'unknown'})"
    vitals_summary = [
        _fmt(temp_label, triage_input.temperature),
        _fmt("Heart rate (bpm)", triage_input.heartrate),
        _fmt("Respiratory rate (breaths/min)", triage_input.resprate),
        _fmt("Oxygen saturation (%)", triage_input.o2sat),
        _fmt("Systolic BP (mmHg)", triage_input.sbp),
        _fmt("Diastolic BP (mmHg)", triage_input.dbp),
        _fmt("Pain score (0-10)", triage_input.nrs_pain if triage_input.nrs_pain is not None else triage_input.pain),
    ]

    missing_or_limited = data_validation.missing_required_fields + data_validation.non_informative_fields
    arrival_context = (
        f"Arrival transport: {triage_input.arrival_transport}"
        if triage_input.arrival_transport else "Arrival transport: MISSING"
    )
    summary_status = (
        "SUMMARY_WITH_DATA_LIMITATIONS" if data_validation.requires_human_data_review
        else "SUMMARY_READY_FOR_HUMAN_REVIEW"
    )
    missing_str = f" MISSING FIELDS: {', '.join(missing_or_limited)}." if missing_or_limited else ""

    summary_text = (
        f"ED stay {triage_input.stay_id} — "
        f"Chief complaint: '{triage_input.chiefcomplaint or 'NOT PROVIDED'}'. "
        f"{arrival_context}. "
        f"Age: {triage_input.age if triage_input.age is not None else 'not recorded'}. "
        f"Gender: {triage_input.gender or 'not recorded'}. "
        f"Mental state: {triage_input.mental_label or triage_input.mental_code or 'not recorded'}. "
        f"Triage-time vitals — Temp: {triage_input.temperature}°{triage_input.temperature_unit}, "
        f"HR: {triage_input.heartrate} bpm, "
        f"RR: {triage_input.resprate} breaths/min, "
        f"SpO2: {triage_input.o2sat}%, "
        f"BP: {triage_input.sbp}/{triage_input.dbp} mmHg, "
        f"Pain: {triage_input.nrs_pain if triage_input.nrs_pain is not None else triage_input.pain}/10."
        f"{missing_str} Data validation: {data_validation.validation_status}. "
        f"This is source-dataset evidence from {triage_input.source_dataset or 'an unspecified dataset'}, "
        "not Manchester triage evidence."
    )

    return CaseSummaryResult(
        summary_status=summary_status,
        chief_complaint=triage_input.chiefcomplaint,
        arrival_context=arrival_context,
        initial_vitals_summary=vitals_summary,
        missing_or_limited_data=missing_or_limited,
        human_review_required=True,
        summary_text=summary_text,
    )
