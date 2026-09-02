from __future__ import annotations

import asyncio
import csv
import io
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _run_record():
    return {
        "record_kind": "workflow_run", "timestamp_utc": "2026-09-01T09:00:00+00:00",
        "timestamp_epoch": 1.0, "date": "2026-09-01", "case_uid": "uhl~case-a",
        "source_dataset": "uhl", "reviewer_role": "triage_nurse",
        "action_type": "workflow_run", "decision_type": "assessment_run",
        "triage_level": "3", "acuity": 3, "summary": "Workflow run: acuity 3",
        "workflow_run_id": "run-1", "display_identifier": "UHL Case 1",
        "source_record": {
            "workflow_run_id": "run-1", "model_version": "model-1",
            "model_sha256": "abc123", "custom_provenance": "kept",
            "input_snapshot": {
                "age": 60, "month": 9, "hour": 9, "time_bin": "morning",
                "season": "autumn", "presenting_complaint": "Unwell adult",
                "temperature": 98.6, "heartrate": 90, "resprate": 18,
                "o2sat": 97, "sbp": 125, "dbp": 80, "pain": 2,
            },
        },
    }


def _rerun_record():
    return {
        "record_kind": "workflow_rerun", "timestamp_utc": "2026-09-01T10:00:00+00:00",
        "timestamp_epoch": 2.0, "date": "2026-09-01", "case_uid": "uhl~case-a",
        "source_dataset": "uhl", "reviewer_role": "ed_nurse",
        "action_type": "followup_reassessment", "decision_type": "ESCALATION",
        "triage_level": "2", "acuity": 2, "summary": "Reassessment: ESCALATION",
        "changed_fields": ["heartrate", "o2sat"],
        "changed_vitals": [
            {"field": "heartrate", "previous": 90, "new": 120},
            {"field": "o2sat", "previous": 97, "new": 91},
        ],
        "source_record": {
            "rerun_id": "rerun-1", "reason": "=recorded change",
            "changed_fields": ["heartrate", "o2sat"],
            "changed_vitals": [
                {"field": "heartrate", "previous": 90, "new": 120},
                {"field": "o2sat", "previous": 97, "new": 91},
            ],
        },
    }


def test_complete_journey_export_carries_vitals_and_preserves_every_safe_field():
    from app.analytics.audit_journey_export import (
        audit_journey_csv_bytes, build_audit_journey_rows,
    )

    review = {
        "record_kind": "human_review", "timestamp_utc": "2026-09-01T10:05:00+00:00",
        "timestamp_epoch": 3.0, "date": "2026-09-01", "case_uid": "uhl~case-a",
        "source_dataset": "uhl", "reviewer_role": "triage_nurse",
        "action_type": "escalation_required", "decision_type": "ESCALATION_REQUIRED",
        "summary": "Review: ESCALATION_REQUIRED", "workflow_run_id": "run-2",
        "review_comment": "Escalated after repeat observations",
        "source_record": {
            "review_id": "review-1", "action_id": "action-1",
            "workflow_run_id": "run-2", "review_status": "ESCALATION_REQUIRED",
            "review_comment": "Escalated after repeat observations",
            "another_new_safe_field": {"nested": "also kept"},
        },
    }
    rows, columns = build_audit_journey_rows([review, _rerun_record(), _run_record()])

    assert [row["record_kind"] for row in rows] == [
        "workflow_run", "workflow_rerun", "human_review",
    ]
    assert [row["journey_sequence"] for row in rows] == [1, 2, 3]
    assert all(row["journey_event_count"] == 3 for row in rows)
    assert rows[0]["heartrate"] == 90
    assert rows[1]["heartrate_previous"] == 90
    assert rows[1]["heartrate_new"] == 120
    assert rows[1]["heartrate_delta"] == 30.0
    assert rows[1]["o2sat_delta"] == -6.0
    assert rows[2]["heartrate"] == 120
    assert rows[2]["o2sat"] == 91
    assert "source.custom_provenance" in columns
    assert "source.another_new_safe_field.nested" in columns
    assert rows[2]["source.another_new_safe_field.nested"] == "also kept"

    decoded = audit_journey_csv_bytes([_run_record(), _rerun_record(), review]).decode(
        "utf-8-sig"
    )
    parsed = list(csv.DictReader(io.StringIO(decoded)))
    assert parsed[1]["reason"] == "'=recorded change"
    assert parsed[2]["source.another_new_safe_field.nested"] == "also kept"


def test_audit_journey_route_is_not_dashboard_page_limited(monkeypatch):
    from app.api import case_resolver, status_routes

    records = [_run_record(), _rerun_record()]
    observed = {}

    def load(**kwargs):
        observed.update(kwargs)
        return [dict(row) for row in records]

    monkeypatch.setattr(status_routes, "_normalised_audit_records", load)
    monkeypatch.setattr(case_resolver, "resolve", lambda _uid: None)
    response = status_routes.audit_journey_csv()
    body = response.body.decode("utf-8-sig")

    assert response.status_code == 200
    assert response.headers["x-audit-export-rows"] == "2"
    assert response.headers["x-audit-export-complete"] == "true"
    assert "audit-complete-patient-journeys-" in response.headers["content-disposition"]
    assert observed == {"limit_per_stream": 100000, "require_complete": True}
    assert "heartrate_previous" in body
    assert "source.input_snapshot.temperature" in body


def test_persisted_action_id_retry_is_idempotent(monkeypatch, tmp_path):
    from app.api import case_routes, case_resolver
    from app.config import settings
    from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource
    from app.security.identity import AuthContext
    from app.storage.case_state_repository import append_case_state, latest_case_state
    from app.notifications import service as notification_service

    monkeypatch.setenv("PATIENT_DATA_MODE", "false")
    monkeypatch.setattr(settings, "processed_dir", tmp_path)
    case = EDTriageCase(
        source_dataset="uhl_synthetic_triage_v1", stay_id=1, subject_id=1,
        edstay=EDStaySource(subject_id=1, stay_id=1),
        triage=TriageSource(
            subject_id=1, stay_id=1, chiefcomplaint="Unwell adult", age=50,
            heartrate=90, resprate=18, o2sat=97, sbp=125, dbp=80,
            temperature=98.6, pain="2",
        ),
    ).model_dump(mode="json")
    resolved = SimpleNamespace(
        case_uid="uhl~retry-case", stay_id=1,
        source_dataset="uhl_synthetic_triage_v1", case=case,
    )
    monkeypatch.setattr(case_routes, "_resolve_or_404", lambda _uid: resolved)
    monkeypatch.setattr(case_resolver, "resolve", lambda _uid: resolved)
    monkeypatch.setattr(notification_service, "sync_workflow_state", lambda *_a, **_k: None)
    state_path = tmp_path / "case_workflow_state.jsonl"
    append_case_state(state_path, {
        "case_uid": resolved.case_uid, "latest_workflow_run_id": "run-retry",
        "latest_system_acuity": 2, "latest_system_prediction": "2",
        "state_revision": 1,
    })
    ctx = AuthContext(
        authenticated=True, user_id="triage-a", display_name="Triage A",
        roles=["triage_nurse"], source="test",
    )
    body = case_routes.ReviewBody(
        action_id="retry-action-12345678",
        review_status="ACCEPTED_AS_PRESENTED",
        workflow_run_id="run-retry", system_prediction="2",
    )

    first = case_routes.submit_review(resolved.case_uid, body, ctx)
    persisted = latest_case_state(state_path, resolved.case_uid)
    second = case_routes.submit_review(resolved.case_uid, body, ctx)

    assert first["status"] == "recorded"
    assert persisted["last_action_id"] == "retry-action-12345678"
    assert len(persisted["last_action_payload_hash"]) == 64
    assert "REDACTED" not in persisted["last_action_payload_hash"]
    assert second["status"] == "already_recorded"
    assert second["review_id"] == first["review_id"]

    changed = body.model_copy(update={"review_comment": "different action"})
    with pytest.raises(HTTPException) as conflict:
        case_routes.submit_review(resolved.case_uid, changed, ctx)
    assert conflict.value.status_code == 409


def test_background_thread_finishes_before_cancelled_task_exits():
    from app.main import _to_thread_and_finish_on_cancel

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_work():
        started.set()
        release.wait(timeout=2)
        finished.set()

    async def scenario():
        task = asyncio.create_task(_to_thread_and_finish_on_cancel(blocking_work))
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        threading.Timer(0.03, release.set).start()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(scenario())
