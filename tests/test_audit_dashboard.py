from app.analytics.audit_dashboard import (
    AuditFilters,
    aggregate_audit_dashboard,
    dashboard_payload,
    filter_audit_records,
    normalise_audit_records,
)


def _records():
    return normalise_audit_records(
        workflow_runs=[
            {
                "timestamp_utc": "2026-07-01T10:00:00+00:00",
                "case_uid": "case-a",
                "source_dataset": "MIMIC-IV-ED-Full-v2.2",
                "final_acuity": 2,
                "final_category": "Very Urgent",
                "workflow_action": "ESCALATE",
            }
        ],
        human_reviews=[
            {
                "created_at_utc": "2026-07-02T11:00:00+00:00",
                "case_uid": "case-b",
                "source_dataset": "MIMIC-IV-ED-Full-v2.2",
                "reviewer_role": "clinical_supervisor",
                "review_status": "ACCEPTED_AS_PRESENTED",
                "system_prediction": 3,
            },
            {
                "created_at_utc": "2026-07-03T11:00:00+00:00",
                "case_uid": "case-c",
                "source_dataset": "MIMIC-IV-ED-Full-v2.2",
                "reviewer_role": "ed_doctor",
                "review_status": "REQUEST_MORE_INFORMATION",
                "system_prediction": 4,
            },
        ],
        workflow_states=[
            {
                "updated_at_utc": "2026-07-04T11:00:00+00:00",
                "case_uid": "case-d",
                "source_dataset": "MIMIC-IV-ED-Full-v2.2",
                "reviewer_role": "triage_nurse",
                "last_action": "ESCALATION_REQUIRED",
                "review_status": "escalation_pending",
                "case_status": "escalation_requested",
                "escalation_status": "requested",
                "overdue_vitals_alert_active": True,
            },
            {
                "updated_at_utc": "2026-07-05T11:00:00+00:00",
                "case_uid": "case-e",
                "source_dataset": "MIMIC-IV-ED-Full-v2.2",
                "reviewer_role": "ed_doctor",
                "last_action": "DISCHARGED",
                "review_status": "discharged",
                "case_status": "discharged",
                "escalation_status": "closed",
            },
        ],
    )


def test_filter_by_time_case_triage_role_and_decision():
    records = _records()
    assert len(filter_audit_records(records, AuditFilters(start_utc="2026-07-02T00:00:00+00:00"))) == 4
    assert len(filter_audit_records(records, AuditFilters(patient_or_case="case-b"))) == 1
    assert len(filter_audit_records(records, AuditFilters(triage_level="2"))) == 1
    assert len(filter_audit_records(records, AuditFilters(reviewer_role="supervisor"))) == 1
    assert len(filter_audit_records(records, AuditFilters(decision_type="REQUEST_MORE"))) == 1


def test_aggregation_counts_match_filtered_entries():
    records = _records()
    filtered = filter_audit_records(records, AuditFilters(source_dataset="Full"))
    agg = aggregate_audit_dashboard(filtered)
    assert agg["summary"]["total_entries"] == len(filtered)
    assert agg["summary"]["total_reviews"] == 2
    assert agg["summary"]["accepted_cases"] == 1
    assert agg["summary"]["request_more_info_actions"] == 1
    assert agg["summary"]["escalations"] == 3
    assert agg["summary"]["escalation_event_count"] == 3
    assert agg["summary"]["open_escalations"] == 1
    assert agg["summary"]["closed_escalations"] == 1
    assert agg["summary"]["discharged_cases"] == 1
    assert agg["summary"]["overdue_vitals_alerts"] == 1
    assert {"label": "case-b", "count": 1} in agg["by_case_uid"]


def test_workflow_summary_uses_latest_unique_case_state():
    records = normalise_audit_records(
        workflow_states=[
            {
                "updated_at_utc": "2026-07-04T11:00:00+00:00",
                "case_uid": "case-z",
                "case_status": "escalation_requested",
                "escalation_status": "requested",
                "overdue_vitals_alert_active": True,
            },
            {
                "updated_at_utc": "2026-07-04T12:00:00+00:00",
                "case_uid": "case-z",
                "case_status": "discharged",
                "escalation_status": "closed",
                "overdue_vitals_alert_active": False,
            },
        ]
    )
    agg = aggregate_audit_dashboard(records)
    assert agg["summary"]["unique_cases_with_workflow_state"] == 1
    assert agg["summary"]["open_escalations"] == 0
    assert agg["summary"]["closed_escalations"] == 1
    assert agg["summary"]["discharged_cases"] == 1
    assert agg["summary"]["overdue_vitals_alerts"] == 0


def test_accepted_case_summary_deduplicates_current_and_history_state():
    records = normalise_audit_records(
        human_reviews=[
            {
                "created_at_utc": "2026-07-04T10:00:00+00:00",
                "case_uid": "case-accepted",
                "review_status": "ACCEPTED_AS_PRESENTED",
                "reviewer_role": "clinical_supervisor",
            }
        ],
        workflow_states=[
            {
                "updated_at_utc": "2026-07-04T10:00:01+00:00",
                "case_uid": "case-accepted",
                "case_status": "accepted",
                "review_status": "accepted_as_presented",
            },
            {
                "updated_at_utc": "2026-07-04T10:00:01+00:00",
                "case_uid": "case-accepted",
                "case_status": "accepted",
                "review_status": "accepted_as_presented",
            },
        ],
    )

    agg = aggregate_audit_dashboard(records)
    assert agg["summary"]["total_reviews"] == 1
    assert agg["summary"]["accepted_cases"] == 1
    assert agg["summary"]["active_cases"] == 1


def test_escalation_dashboard_tracks_actors_drivers_and_confirmation_time():
    records = normalise_audit_records(
        workflow_states=[
            {
                "updated_at_utc": "2026-07-04T12:00:00+00:00",
                "case_uid": "case-y",
                "triage_level": "2",
                "case_status": "escalated",
                "escalation_status": "confirmed",
                "escalation_target_role": "clinical_supervisor",
                "escalation_requested_by_role": "triage_nurse",
                "escalation_confirmed_by_role": "ed_doctor",
                "escalation_requested_at": "2026-07-04T11:00:00+00:00",
                "escalation_confirmed_at": "2026-07-04T11:30:00+00:00",
                "escalation_reason": "manual concern",
                "escalation_evidence": {"changed_vitals": [{"field": "heartrate"}]},
            }
        ]
    )
    agg = aggregate_audit_dashboard(records)

    assert agg["summary"]["confirmed_escalations"] == 1
    assert agg["summary"]["average_escalation_request_to_confirmation_minutes"] == 30.0
    row = agg["escalation_worklist"][0]
    assert row["case_uid"] == "case-y"
    assert row["requested_by_role"] == "triage_nurse"
    assert row["confirmed_by_role"] == "ed_doctor"
    assert row["target_role"] == "clinical_supervisor"
    assert row["minutes_awaiting_confirmation"] == 30.0
    assert row["drivers"] == ["changed_vitals"]


def test_dashboard_handles_empty_logs():
    payload = dashboard_payload([], AuditFilters(), limit=100)
    assert payload["count"] == 0
    assert payload["aggregations"]["summary"]["total_entries"] == 0
    assert payload["entries"] == []
