from app.schemas.internal import TriageTimeInput
from app.rules.leakage_guard import validate_triage_time_input, assert_triage_input_has_no_known_leakage


def test_valid_triage_input_passes():
    triage_input = TriageTimeInput(
        subject_id=1,
        stay_id=2,
        chiefcomplaint="CHEST PAIN",
        heartrate=90,
    )
    assert validate_triage_time_input(triage_input) is True


def test_leakage_key_fails():
    bad_payload = {
        "subject_id": 1,
        "stay_id": 2,
        "chiefcomplaint": "CHEST PAIN",
        "disposition": "ADMITTED",
    }
    try:
        assert_triage_input_has_no_known_leakage(bad_payload)
    except ValueError as exc:
        assert "disposition" in str(exc)
    else:
        raise AssertionError("Expected leakage guard to fail")
