from app.schemas.internal import TriageTimeInput
from app.schemas.workflow import ManchesterDecision


class ManchesterRulesNotConfigured(RuntimeError):
    pass


def run_manchester_engine(triage_input: TriageTimeInput) -> ManchesterDecision:
    """
    Clinical safety stance:

    The official Manchester discriminator rules are not part of the verified
    MIMIC-IV-ED schema. This function must not fabricate triage rules.

    Until a clinician-approved ruleset is supplied, this returns a non-decision
    requiring human review.
    """
    return ManchesterDecision(
        classification_status="NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED",
        category=None,
        priority=None,
        max_wait_minutes=None,
        reason_codes=[
            "MANCHESTER_RULESET_NOT_SUPPLIED",
            "HUMAN_CLINICIAN_REVIEW_REQUIRED",
        ],
        requires_clinician_review=True,
    )
