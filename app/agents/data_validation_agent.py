from app.schemas.internal import TriageTimeInput
from app.schemas.validation import DataValidationResult


REQUIRED_TRIAGE_FIELDS = [
    "chiefcomplaint",
    "temperature",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "pain",
]


NON_INFORMATIVE_CHIEF_COMPLAINT_VALUES = {
    "UNKNOWN-CC",
    "UNKNOWN",
    "",
}


def run_data_validation_agent(triage_input: TriageTimeInput) -> DataValidationResult:
    payload = triage_input.model_dump()

    missing_required_fields = [
        field
        for field in REQUIRED_TRIAGE_FIELDS
        if payload.get(field) is None or payload.get(field) == ""
    ]

    non_informative_fields = []

    chiefcomplaint = payload.get("chiefcomplaint")
    if chiefcomplaint is None or str(chiefcomplaint).strip().upper() in NON_INFORMATIVE_CHIEF_COMPLAINT_VALUES:
        non_informative_fields.append("chiefcomplaint")

    requires_human_data_review = bool(missing_required_fields or non_informative_fields)

    if requires_human_data_review:
        validation_status = "NEEDS_HUMAN_DATA_REVIEW"
    else:
        validation_status = "TRIAGE_INPUT_DATA_COMPLETE"

    notes = [
        "Validation is limited to data completeness and usability.",
        "No clinical triage category is assigned by this agent.",
        "No retrospective fields are used.",
    ]

    return DataValidationResult(
        validation_status=validation_status,
        missing_required_fields=missing_required_fields,
        non_informative_fields=non_informative_fields,
        requires_human_data_review=requires_human_data_review,
        notes=notes,
    )