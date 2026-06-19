"""
Leakage guard.

Ensures no retrospective or outcome fields contaminate the triage-time
input. This is a deterministic safety check — it does not use an LLM.

If this check fails, the workflow must be halted. Label leakage would make
trained models appear to perform better than they actually would in
deployment, creating a dangerous false sense of clinical safety.
"""
from app.schemas.internal import TriageTimeInput
from app.schemas.mimic_ed import RETROSPECTIVE_OR_LEAKAGE_COLUMNS


def validate_triage_time_input(triage_input: TriageTimeInput) -> bool:
    """
    Returns True if the triage input contains no known leakage fields.
    Raises ValueError if leakage is detected.

    This function is called by the Safety Review Agent on every case.
    """
    payload = triage_input.model_dump()
    leaked = sorted(set(payload.keys()).intersection(set(RETROSPECTIVE_OR_LEAKAGE_COLUMNS)))
    if leaked:
        raise ValueError(
            f"LEAKAGE DETECTED: retrospective fields present in triage input: {leaked}. "
            "This case must be halted immediately."
        )
    return True
