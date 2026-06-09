from app.schemas.internal import TriageTimeInput
from app.schemas.workflow import SafetyReviewResult
from app.rules.leakage_guard import validate_triage_time_input


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


def run_safety_review(triage_input: TriageTimeInput) -> SafetyReviewResult:
    data_quality_flags: list[str] = []
    notes: list[str] = []

    leakage_ok = validate_triage_time_input(triage_input)

    payload = triage_input.model_dump()
    for field in REQUIRED_TRIAGE_FIELDS:
        if payload.get(field) is None:
            data_quality_flags.append(f"MISSING_TRIAGE_FIELD:{field}")

    notes.append(
        "No clinical escalation rules are applied here; this is data-quality and leakage review only."
    )

    return SafetyReviewResult(
        data_quality_flags=data_quality_flags,
        leakage_guard_passed=leakage_ok,
        notes=notes,
    )
