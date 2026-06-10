from app.governance.audit_summary import summarise_agent_audit_records


def test_empty_audit_summary_has_no_records():
    summary = summarise_agent_audit_records([])

    assert summary["audit_records_found"] is False
    assert summary["record_count"] == 0
    assert summary["clinical_use_allowed_count"] == 0
    assert summary["automated_manchester_triage_allowed_count"] == 0


def test_audit_summary_counts_records_and_guardrails():
    records = [
        {
            "run_id": "run-1",
            "timestamp_utc": "2026-06-10T13:00:00+00:00",
            "case_id": 30115213,
            "agent_name": "Dataset Evidence Agent",
            "governance_verdict": "NOT_READY_FOR_CLINICAL_USE",
            "human_review_required": True,
            "clinical_use_allowed": False,
            "automated_manchester_triage_allowed": False,
            "safety_flags": [],
        },
        {
            "run_id": "run-1",
            "timestamp_utc": "2026-06-10T13:01:00+00:00",
            "case_id": 30115213,
            "agent_name": "Governance Agent",
            "governance_verdict": "NOT_READY_FOR_CLINICAL_USE",
            "human_review_required": True,
            "clinical_use_allowed": False,
            "automated_manchester_triage_allowed": False,
            "safety_flags": [
                "No clinician-approved Manchester triage ruleset configured."
            ],
        },
    ]

    summary = summarise_agent_audit_records(records)

    assert summary["audit_records_found"] is True
    assert summary["record_count"] == 2
    assert summary["unique_run_count"] == 1
    assert summary["latest_run_id"] == "run-1"
    assert summary["human_review_required_count"] == 2
    assert summary["clinical_use_allowed_count"] == 0
    assert summary["automated_manchester_triage_allowed_count"] == 0
    assert summary["governance_verdicts"]["NOT_READY_FOR_CLINICAL_USE"] == 2