from app.schemas.internal import TriageTimeInput
from app.schemas.mimic_ed import RETROSPECTIVE_OR_LEAKAGE_COLUMNS


def assert_triage_input_has_no_known_leakage(payload: dict) -> None:
    leaked = sorted(set(payload.keys()).intersection(RETROSPECTIVE_OR_LEAKAGE_COLUMNS))
    if leaked:
        raise ValueError(f"Known leakage fields present in triage input: {leaked}")


def validate_triage_time_input(triage_input: TriageTimeInput) -> bool:
    payload = triage_input.model_dump()
    assert_triage_input_has_no_known_leakage(payload)
    return True
