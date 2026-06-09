from app.schemas.internal import TriageTimeInput
from app.rules.manchester_engine import run_manchester_engine


def test_no_manchester_rules_are_fabricated():
    decision = run_manchester_engine(TriageTimeInput(subject_id=1, stay_id=2))
    assert decision.classification_status == "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
    assert decision.category is None
    assert decision.requires_clinician_review is True
