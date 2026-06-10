from app.governance.policy_engine import (
    EXPECTED_CLINICAL_USE,
    EXPECTED_GOVERNANCE_VERDICT,
    EXPECTED_RULES_STATUS,
    check_governance_payload,
    check_health_payload,
    check_review_queue_payload,
    has_failures,
)


def test_health_policy_passes_when_clinical_use_blocked_and_rules_disabled():
    payload = {
        "status": "ok",
        "clinical_use": EXPECTED_CLINICAL_USE,
        "rules_status": EXPECTED_RULES_STATUS,
    }

    results = check_health_payload(payload)

    assert not has_failures(results)


def test_health_policy_fails_if_clinical_use_enabled():
    payload = {
        "status": "ok",
        "clinical_use": "clinical_use_enabled",
        "rules_status": EXPECTED_RULES_STATUS,
    }

    results = check_health_payload(payload)

    assert has_failures(results)


def test_health_policy_fails_if_manchester_rules_enabled_too_early():
    payload = {
        "status": "ok",
        "clinical_use": EXPECTED_CLINICAL_USE,
        "rules_status": "MANCHESTER_CLASSIFICATION_ENABLED",
    }

    results = check_health_payload(payload)

    assert has_failures(results)


def test_governance_policy_passes_when_not_ready_and_blocking_issues_exist():
    payload = {
        "governance_verdict": EXPECTED_GOVERNANCE_VERDICT,
        "blocking_issues": [
            "No clinician-approved Manchester triage ruleset configured."
        ],
    }

    results = check_governance_payload(payload)

    assert not has_failures(results)


def test_governance_policy_fails_if_not_ready_verdict_removed():
    payload = {
        "governance_verdict": "READY_FOR_CLINICAL_USE",
        "blocking_issues": [],
    }

    results = check_governance_payload(payload)

    assert has_failures(results)


def test_review_queue_policy_passes_with_integer_counts():
    payload = {
        "total_missing_cases": 14,
        "reviewed_count": 3,
        "needs_review_count": 11,
    }

    results = check_review_queue_payload(payload)

    assert not has_failures(results)