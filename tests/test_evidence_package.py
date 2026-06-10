from app.governance.evidence_package import (
    EXPECTED_CLINICAL_USE,
    EXPECTED_GOVERNANCE_VERDICT,
    EXPECTED_RULES_STATUS,
    evaluate_evidence_status,
)


def test_evidence_status_passes_for_current_research_prototype_controls():
    health_payload = {
        "clinical_use": EXPECTED_CLINICAL_USE,
        "rules_status": EXPECTED_RULES_STATUS,
    }

    governance_payload = {
        "governance_verdict": EXPECTED_GOVERNANCE_VERDICT,
    }

    review_queue_payload = {
        "total_missing_cases": 14,
        "needs_review_count": 11,
    }

    policy_report = {
        "summary": {
            "pass": 26,
            "warn": 0,
            "fail": 0,
            "total": 26,
        }
    }

    audit_summary_report = {
        "summary": {
            "audit_records_found": True,
            "record_count": 4,
            "clinical_use_allowed_count": 0,
            "automated_manchester_triage_allowed_count": 0,
        }
    }

    status = evaluate_evidence_status(
        health_payload=health_payload,
        governance_payload=governance_payload,
        review_queue_payload=review_queue_payload,
        policy_report=policy_report,
        audit_summary_report=audit_summary_report,
    )

    assert status["overall_evidence_package_status"] == "READY_FOR_RESEARCH_GOVERNANCE_REVIEW"
    assert status["clinical_use_allowed"] is False
    assert status["automated_manchester_triage_allowed"] is False
    assert status["manchester_category_assigned"] is False
    assert status["all_required_controls_passed"] is True


def test_evidence_status_fails_if_clinical_use_guardrail_removed():
    status = evaluate_evidence_status(
        health_payload={
            "clinical_use": "clinical_use_enabled",
            "rules_status": EXPECTED_RULES_STATUS,
        },
        governance_payload={
            "governance_verdict": EXPECTED_GOVERNANCE_VERDICT,
        },
        review_queue_payload={
            "total_missing_cases": 14,
            "needs_review_count": 11,
        },
        policy_report={
            "summary": {
                "fail": 0,
            }
        },
        audit_summary_report={
            "summary": {
                "audit_records_found": True,
                "record_count": 4,
                "clinical_use_allowed_count": 0,
                "automated_manchester_triage_allowed_count": 0,
            }
        },
    )

    assert status["overall_evidence_package_status"] == "INCOMPLETE_OR_UNSAFE_EVIDENCE_PACKAGE"
    assert status["required_controls"]["clinical_use_guardrail_active"] is False
    assert status["clinical_use_allowed"] is False


def test_evidence_status_fails_if_manchester_block_removed():
    status = evaluate_evidence_status(
        health_payload={
            "clinical_use": EXPECTED_CLINICAL_USE,
            "rules_status": "MANCHESTER_CLASSIFICATION_ENABLED",
        },
        governance_payload={
            "governance_verdict": EXPECTED_GOVERNANCE_VERDICT,
        },
        review_queue_payload={
            "total_missing_cases": 14,
            "needs_review_count": 11,
        },
        policy_report={
            "summary": {
                "fail": 0,
            }
        },
        audit_summary_report={
            "summary": {
                "audit_records_found": True,
                "record_count": 4,
                "clinical_use_allowed_count": 0,
                "automated_manchester_triage_allowed_count": 0,
            }
        },
    )

    assert status["overall_evidence_package_status"] == "INCOMPLETE_OR_UNSAFE_EVIDENCE_PACKAGE"
    assert status["required_controls"]["automated_manchester_triage_blocked"] is False
    assert status["automated_manchester_triage_allowed"] is False