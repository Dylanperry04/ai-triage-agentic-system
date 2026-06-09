from app.schemas.internal import TriageTimeInput
from app.schemas.validation import DataValidationResult
from app.schemas.summary import CaseSummaryResult


def _format_value(label: str, value) -> str:
    if value is None or value == "":
        return f"{label}: missing"
    return f"{label}: {value}"


def run_case_summary_agent(
    triage_input: TriageTimeInput,
    data_validation: DataValidationResult,
) -> CaseSummaryResult:
    vitals_summary = [
        _format_value("Temperature", triage_input.temperature),
        _format_value("Heart rate", triage_input.heartrate),
        _format_value("Respiratory rate", triage_input.resprate),
        _format_value("Oxygen saturation", triage_input.o2sat),
        _format_value("Systolic BP", triage_input.sbp),
        _format_value("Diastolic BP", triage_input.dbp),
        _format_value("Pain", triage_input.pain),
    ]

    missing_or_limited_data = (
        data_validation.missing_required_fields
        + data_validation.non_informative_fields
    )

    arrival_context = (
        f"Arrival transport: {triage_input.arrival_transport}"
        if triage_input.arrival_transport
        else "Arrival transport: missing"
    )

    if data_validation.requires_human_data_review:
        summary_status = "SUMMARY_WITH_DATA_LIMITATIONS"
    else:
        summary_status = "SUMMARY_READY_FOR_HUMAN_REVIEW"

    summary_text = (
        f"ED stay {triage_input.stay_id} presents with chief complaint "
        f"'{triage_input.chiefcomplaint}'. "
        f"{arrival_context}. "
        f"Initial triage vitals available: "
        f"temperature={triage_input.temperature}, "
        f"heart_rate={triage_input.heartrate}, "
        f"respiratory_rate={triage_input.resprate}, "
        f"oxygen_saturation={triage_input.o2sat}, "
        f"systolic_bp={triage_input.sbp}, "
        f"diastolic_bp={triage_input.dbp}, "
        f"pain={triage_input.pain}. "
        f"Data validation status: {data_validation.validation_status}."
    )

    return CaseSummaryResult(
        summary_status=summary_status,
        chief_complaint=triage_input.chiefcomplaint,
        arrival_context=arrival_context,
        initial_vitals_summary=vitals_summary,
        missing_or_limited_data=missing_or_limited_data,
        human_review_required=True,
        summary_text=summary_text,
    )