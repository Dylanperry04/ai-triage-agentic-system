"""Regression coverage for the fifth independent review's concrete claims."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

from app.analytics.audit_dashboard import normalise_audit_records
from app.analytics import itd_general_query
from app.notifications.models import NotificationRecord
from app.notifications.repository import SQLiteNotificationRepository


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def _audit_records():
    runs = [
        {
            "workflow_run_id": f"run-{index}",
            "timestamp_utc": f"2026-08-25T{hour:02d}:00:00+00:00",
            "case_uid": f"case-{index % 2}",
            "final_acuity": 3,
        }
        for index, hour in enumerate((9, 9, 14))
    ]
    reruns = [
        {
            "rerun_id": f"rerun-{index}",
            "timestamp_utc": f"2026-08-25T{10 + index:02d}:00:00+00:00",
            "case_uid": "case-0",
            "new_final_acuity": 3,
            "performed_by_role": "ed_nurse",
            "performed_by_display_name": "Nurse A",
        }
        for index in range(2)
    ]
    reruns.append({
        "rerun_id": "rerun-nurse-b",
        "timestamp_utc": "2026-08-25T12:30:00+00:00",
        "case_uid": "case-1",
        "new_final_acuity": 3,
        "performed_by_role": "ed_nurse",
        "performed_by_display_name": "Nurse B",
    })
    reviews = [
        {
            "review_id": "escalated",
            "created_at_utc": "2026-08-25T10:10:00+00:00",
            "case_uid": "case-0",
            "workflow_run_id": "run-0",
            "reviewer_role": "triage_nurse",
            "reviewer_display_name": "Nurse A",
            "review_status": "ESCALATION_REQUIRED",
            "system_prediction": "3",
        },
        {
            "review_id": "confirmed",
            "created_at_utc": "2026-08-25T10:40:00+00:00",
            "case_uid": "case-0",
            "workflow_run_id": "run-0",
            "reviewer_role": "ed_doctor",
            "reviewer_display_name": "Doctor B",
            "review_status": "ESCALATION_CONFIRMED",
            "system_prediction": "3",
            "review_comment": "Taking ownership.",
        },
        {
            "review_id": "resolved",
            "created_at_utc": "2026-08-25T11:10:00+00:00",
            "case_uid": "case-0",
            "workflow_run_id": "run-0",
            "reviewer_role": "ed_doctor",
            "reviewer_display_name": "Doctor B",
            "review_status": "ESCALATION_RESOLVED",
            "system_prediction": "3",
            "final_clinician_acuity": 2,
            "review_comment": "Persistent hypoxia after repeat observations.",
            "override_reason": "Persistent hypoxia after repeat observations.",
        },
    ]
    access = [{
        "timestamp_utc": "2026-08-25T11:30:00+00:00",
        "user_id": "research-user",
        "display_name": "Research User",
        "roles": ["researcher"],
        "action": "view_audit_log",
        "decision": "DENIED",
    }]
    states = [
        {
            "case_uid": "old-open",
            "updated_at_utc": "2026-01-01T00:00:00+00:00",
            "escalation_status": "requested",
            "case_status": "escalation_requested",
        },
        {
            "case_uid": "overdue",
            "updated_at_utc": "2026-01-01T00:00:00+00:00",
            "overdue_vitals_alert_active": True,
        },
    ]
    return normalise_audit_records(
        access_events=access,
        workflow_runs=runs,
        human_reviews=reviews,
        workflow_reruns=reruns,
        workflow_states=states,
    )


def _ask(question: str):
    result = itd_general_query.answer_general_audit_question(
        question, _audit_records(), now=NOW
    )
    assert result is not None
    return result


def test_failed_assessment_question_does_not_substitute_total_assessments():
    result = _ask("How many model assessments failed today?")
    assert result["supported"] is False
    assert "does not record a distinct assessment-failure" in result["answer"]
    assert "3 model assessments" not in result["answer"]


def test_general_queries_cover_hour_case_list_reason_and_acuity_dimensions():
    busiest = _ask("At what hour were assessments busiest today?")
    assert busiest["results"][0]["groups"][0]["count"] == 2

    reassessments = _ask("Which patient had the most reassessments today?")
    assert reassessments["results"][0]["groups"] == [
        {"label": "Case case-0", "value": "case-0", "count": 2}
    ]

    events = _ask("Show me the last five escalation events today")
    assert len(events["results"][0]["items"]) == 3
    assert events["results"][0]["items"][0]["action"] == "ESCALATION_RESOLVED"

    reasons = _ask("What were the main recorded reasons for overrides?")
    assert "Persistent hypoxia" in reasons["answer"]

    acuity = _ask("Which acuity level is overridden most frequently?")
    assert acuity["results"][0]["groups"][0]["label"] == "Acuity 3"


def test_exact_demo_escalation_question_reports_unique_cases_and_staff():
    result = _ask(
        "How many patients have been escalated today and which staff member escalated the most?"
    )
    assert result["results"][0]["total"] == 1
    assert result["results"][0]["distinct_field"] == "case_uid"
    assert "1 unique patient case matched" in result["answer"]
    assert "Nurse A" in result["answer"]
    assert "Doctor B" not in result["answer"]


def test_local_fallback_never_changes_unknown_statistic_into_a_count():
    result = itd_general_query.answer_general_audit_question(
        "What is the standard deviation of assessment acuity today?",
        _audit_records(),
        now=NOW,
    )
    assert result is None


def test_general_grouping_supports_the_lowest_count_without_sql():
    result = _ask("Who recorded the fewest reassessments today?")
    assert result["results"][0]["groups"] == [
        {"label": "Nurse B", "value": "Nurse B", "count": 1}
    ]
    assert "lowest attributed count was 1" in result["answer"]


def test_current_state_ignores_activity_window_and_combines_two_questions():
    result = _ask("How many escalations remain unresolved and are any vitals overdue today?")
    assert len(result["results"]) == 2
    assert result["results"][0]["value"] == 1
    assert result["results"][1]["value"] == 1


def test_access_actor_is_available_to_general_grouping():
    result = _ask("Who has the most access denials today?")
    assert result["results"][0]["groups"][0]["label"] == "Research User"


def test_unapproved_field_or_operator_invalidates_foundry_plan():
    assert itd_general_query.validate_plan({
        "queries": [{
            "operation": "count", "scope": "events",
            "filters": [{"field": "source_record", "op": "sql", "value": "DROP"}],
        }]
    }) is None


def test_malformed_or_oversized_plans_are_rejected_not_coerced():
    base = {"operation": "count", "scope": "events", "filters": []}
    assert itd_general_query.validate_plan({"queries": [base] * 5}) is None
    assert itd_general_query.validate_plan({
        "queries": [{**base, "distinct_field": "not_a_field"}]
    }) is None
    assert itd_general_query.validate_plan({
        "queries": [{**base, "time_range": {"start_utc": "not-a-date", "end_utc": None}}]
    }) is None
    assert itd_general_query.validate_plan({
        "queries": [{**base, "limit": 5}]
    }) is None


def test_foundry_plan_must_be_semantically_compatible_with_question(monkeypatch):
    monkeypatch.setattr(itd_general_query, "local_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(itd_general_query, "azure_plan", lambda *_args, **_kwargs: {
        "queries": [{
            "operation": "count", "scope": "events",
            "filters": [{"field": "event_category", "op": "eq", "value": "access_denial"}],
            "time_range": {"start_utc": "2020-01-01T00:00:00Z", "end_utc": None},
        }]
    })
    plan, source = itd_general_query.plan_question("How many assessments ran today?", now=NOW)
    assert plan is None
    assert source == "azure_foundry_semantic_mismatch"


def test_foundry_plan_cannot_add_unrequested_event_population(monkeypatch):
    monkeypatch.setattr(itd_general_query, "local_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(itd_general_query, "azure_plan", lambda *_args, **_kwargs: {
        "queries": [{
            "operation": "count", "scope": "events",
            "filters": [{
                "field": "event_category", "op": "in",
                "value": ["assessment", "access_denial"],
            }],
            "time_range": {"start_utc": None, "end_utc": None},
        }]
    })
    plan, source = itd_general_query.plan_question(
        "Quantify the assessments completed today.", now=NOW
    )
    assert plan is None
    assert source == "azure_foundry_semantic_mismatch"


def test_server_date_and_current_state_are_authoritative_for_foundry(monkeypatch):
    monkeypatch.setattr(itd_general_query, "local_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(itd_general_query, "azure_plan", lambda *_args, **_kwargs: {
        "queries": [{
            "operation": "count", "scope": "latest_state",
            "filters": [
                {"field": "record_kind", "op": "eq", "value": "workflow_state"},
                {"field": "escalation_status", "op": "in", "value": ["requested", "pending"]},
            ],
            "distinct_field": "case_uid",
            "time_range": {"start_utc": "2020-01-01T00:00:00Z", "end_utc": "2020-01-02T00:00:00Z"},
        }]
    })
    plan, source = itd_general_query.plan_question(
        "How many escalations are currently open today?", now=NOW
    )
    assert source == "azure_foundry_validated"
    assert plan["queries"][0]["time_range"] == {"start_utc": None, "end_utc": None}


def test_recognised_question_uses_deterministic_plan_before_foundry(monkeypatch):
    monkeypatch.setattr(itd_general_query, "azure_plan", lambda *_args, **_kwargs: {
        "queries": [{
            "operation": "list", "scope": "events", "filters": [
                {"field": "event_category", "op": "eq", "value": "escalation"}
            ], "limit": 999,
        }]
    })
    plan, planner = itd_general_query.plan_question("show escalations today", now=NOW)
    assert planner == "local_validated"
    assert plan["queries"][0]["limit"] == 5
    assert plan["queries"][0]["time_range"]["start_utc"]
    assert itd_general_query.validate_plan({
        "queries": [{"operation": "list", "scope": "events", "filters": [], "limit": 999}]
    }) is None


def test_foundry_receives_schema_and_question_but_never_audit_rows(monkeypatch):
    import json
    import openai
    from app.agents import autogen_team

    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            content = json.dumps({
                "queries": [{
                    "operation": "count",
                    "scope": "events",
                    "filters": [{"field": "event_category", "op": "eq", "value": "assessment"}],
                    "time_range": {"start_utc": None, "end_utc": None},
                }]
            })
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("ITD_ASSISTANT_LLM_PLANNER_ENABLED", "true")
    monkeypatch.setattr(autogen_team, "load_azure_config", lambda: {
        "endpoint": "https://example.invalid", "api_key": "test",
        "api_version": "2026-01-01", "deployment": "gpt-test",
    })
    monkeypatch.setattr(openai, "AzureOpenAI", FakeClient)

    raw = itd_general_query.azure_plan("How many assessments ran today?", now=NOW)
    assert raw["queries"][0]["operation"] == "count"
    prompt = json.dumps(captured["messages"])
    assert "event_category" in prompt
    assert "How many assessments ran today?" in prompt
    assert "case-0" not in prompt
    assert "Persistent hypoxia" not in prompt


def test_system_acuity_scale_is_not_patient_refusal_and_patient_advice_is():
    from app.api.status_routes import (
        _is_patient_specific_itd_question,
        _system_information_answer,
    )

    assert not _is_patient_specific_itd_question("what acuity scale does the model use?")
    assert _is_patient_specific_itd_question("what acuity should this patient get?")
    answer = _system_information_answer("what acuity scale does the model use?", {})
    assert "acuity levels 1–5" in answer
    assert "not an official Manchester" in answer


def test_role_catalog_answers_active_responsibilities():
    from app.api.status_routes import _system_information_answer

    answer = _system_information_answer("what roles exist and what can each role do?", {})
    for label in ("ED Nurse", "Triage Nurse", "ED Doctor", "ITD", "Governance Auditor"):
        assert label in answer
    assert "Retired historical role values" in answer


def test_role_permission_questions_come_from_active_matrix():
    from app.api.status_routes import _system_information_answer

    doctor = _system_information_answer(
        "Does a doctor have permission to enter observations?", {}
    )
    nurse = _system_information_answer("Can the ED nurse record vitals?", {})
    who = _system_information_answer("Who can resolve escalation?", {})
    assert doctor.startswith("No.") and "ED Doctor" in doctor
    assert nurse.startswith("Yes.") and "ED Nurse" in nurse
    assert "ED Doctor" in who and "ITD" in who


def test_wal_transition_is_not_per_request_and_concurrent_writes_retry(tmp_path):
    assert "journal_mode" not in inspect.getsource(SQLiteNotificationRepository._connect)
    repository = SQLiteNotificationRepository(tmp_path / "notifications.sqlite3")

    def operation(index: int) -> bool:
        record = NotificationRecord.create(
            kind="escalation",
            case_uid=f"case-{index}",
            event_key=f"event-{index}",
            target_role="ed_doctor",
            title="Escalation awaiting review",
            body="Review the escalation in ALTER.",
            sms_enabled=False,
        )
        stored, _created = repository.create_notification(record)
        assert repository.mark_read(stored.notification_id, f"user-{index % 5}", stored.created_at)
        return bool(repository.list_notifications(
            roles=["ed_doctor"], user_id=f"user-{index % 5}", limit=200
        ))

    with ThreadPoolExecutor(max_workers=20) as pool:
        assert all(pool.map(operation, range(160)))
    assert repository.count_notifications(roles=["ed_doctor"], user_id="reader") == 160


def _run(run_id: str, stamp: str) -> dict:
    from app.constants import DATASET_SOURCE, MODEL_INPUT_COLUMNS

    snapshot = {
        "age": 40, "month": 8, "hour": 12, "time_bin": "12-17",
        "season": "summer", "presenting_complaint": "UNWELL ADULT",
        "temperature": 98.6, "heartrate": 92, "resprate": 18,
        "o2sat": 97, "sbp": 125, "dbp": 75, "pain": 3,
    }
    return {
        "workflow_run_id": run_id, "case_uid": f"case-{run_id}",
        "timestamp_utc": stamp, "input_snapshot": snapshot,
        "source_dataset": DATASET_SOURCE,
        "input_schema": list(MODEL_INPUT_COLUMNS),
        "final_acuity": 3, "model_version": "demo", "model_sha256": "abc",
    }


def _review(run: dict, status: str, final: int | None = None) -> dict:
    return {
        "review_id": f"review-{status}", "workflow_run_id": run["workflow_run_id"],
        "case_uid": run["case_uid"], "created_at_utc": "2026-10-01T00:05:00+00:00",
        "review_status": status, "reviewer_role": "ed_doctor",
        "system_prediction": "3", "final_clinician_acuity": final,
        "review_comment": "Final review complete.",
    }


def test_scheduled_reconciliation_revisits_an_older_existing_month(monkeypatch, tmp_path):
    from app.retraining import monthly_export

    august = _run("august-late", "2026-08-31T22:50:00+00:00")
    unresolved = _review(august, "ESCALATION_REQUIRED")
    monkeypatch.setattr(monthly_export, "_notify_ready", lambda manifest: f"notice-{manifest['reporting_month']}-{manifest['revision']}")
    first = monthly_export.generate_monthly_export(
        "2026-08", now=datetime(2026, 9, 1, 1, tzinfo=timezone.utc),
        workflow_runs=[august], human_reviews=[unresolved], directory=tmp_path,
    )
    assert first["eligible_cases"] == 0

    resolved = _review(august, "ESCALATION_RESOLVED", final=2)
    monkeypatch.setattr(
        monthly_export, "_load_source_records",
        lambda _month: ([august], [unresolved, resolved]),
    )
    result = monthly_export.reconcile_completed_month_exports(
        datetime(2026, 10, 1, 1, tzinfo=timezone.utc), directory=tmp_path,
    )
    assert set(result["reconciled_months"]) == {"2026-09", "2026-08"}
    revised = monthly_export.read_manifest("2026-08", tmp_path)
    assert revised["revision"] == 2
    assert revised["eligible_cases"] == 1


def test_current_preview_requires_the_complete_safe_demo_profile(monkeypatch):
    from app.api.retraining_routes import _demo_preview_enabled

    for name in (
        "PATIENT_DATA_MODE", "LOCAL_CREDENTIALED_RESEARCH", "AUTH_REQUIRED",
        "TRUSTED_AUTH_PROXY", "REAL_PATIENT_DATA", "AZURE_SUPERVISOR_DEMO_MODE",
        "ALLOW_DEMO_ROLE_SWITCHER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_PROVIDER", "demo")
    monkeypatch.setenv("AZURE_SUPERVISOR_DEMO_MODE", "true")
    assert _demo_preview_enabled() is False
    monkeypatch.setenv("ALLOW_DEMO_ROLE_SWITCHER", "true")
    assert _demo_preview_enabled() is True
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    assert _demo_preview_enabled() is False


def test_development_manifest_contains_retired_ui_test_dependency():
    from pathlib import Path

    content = Path("requirements-dev.txt").read_text(encoding="utf-8")
    assert "streamlit==1.54.0" in content
