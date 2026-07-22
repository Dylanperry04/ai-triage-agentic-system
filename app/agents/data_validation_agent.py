"""
Data Validation Agent.

Checks whether required triage-time fields are present and informative. It does
not assign KTAS, Manchester, or any clinical category.
"""
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
]

NON_INFORMATIVE_CHIEF_COMPLAINT_VALUES = {
    "UNKNOWN-CC", "UNKNOWN", "", "N/A", "NA", "NONE", ".", "??",
    "NOT OBTAINED", "PATIENT UNABLE TO PROVIDE",
}


def run_data_validation_agent(triage_input: TriageTimeInput) -> DataValidationResult:
    payload = triage_input.model_dump()
    missing_required_fields = [
        field for field in REQUIRED_TRIAGE_FIELDS
        if payload.get(field) is None or payload.get(field) == ""
    ]

    # Pain handling: if the dataset explicitly says Pain=No, absence of NRS is not
    # treated as missing pain severity. If Pain=Yes, NRS/pain must be present.
    if triage_input.pain_present == 1 and triage_input.nrs_pain is None and not triage_input.pain:
        missing_required_fields.append("nrs_pain")
    elif triage_input.pain_present is None and triage_input.nrs_pain is None and not triage_input.pain:
        missing_required_fields.append("pain")

    non_informative_fields = []
    chiefcomplaint = payload.get("chiefcomplaint")
    if chiefcomplaint is None or str(chiefcomplaint).strip().upper() in NON_INFORMATIVE_CHIEF_COMPLAINT_VALUES:
        if "chiefcomplaint" not in missing_required_fields:
            non_informative_fields.append("chiefcomplaint")

    requires_human_data_review = bool(missing_required_fields or non_informative_fields)
    validation_status = "NEEDS_HUMAN_DATA_REVIEW" if requires_human_data_review else "TRIAGE_INPUT_DATA_COMPLETE"

    notes = [
        "Validation is limited to data completeness and usability.",
        "No triage category is assigned by this agent.",
        "KTAS labels, mistriage, diagnosis, disposition, and length of stay are not triage-time inputs.",
        "A human data review is required when data is missing or non-informative.",
    ]

    return DataValidationResult(
        validation_status=validation_status,
        missing_required_fields=missing_required_fields,
        non_informative_fields=non_informative_fields,
        requires_human_data_review=requires_human_data_review,
        notes=notes,
    )
