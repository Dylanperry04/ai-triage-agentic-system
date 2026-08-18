"""Regression coverage for independently reported v25.4 notification risks."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import threading

import pytest
from fastapi import HTTPException

from app.api import notification_routes
from app.notifications.config import NotificationSettings
from app.notifications.models import NotificationRecord, ScheduleRecord, utc_iso, utc_now
from app.notifications.repository import (
    AzureTableNotificationRepository,
    SQLiteNotificationRepository,
)
from app.notifications.service import materialize_schedule, sync_workflow_state
from app.notifications.worker import process_work_message
from app.security.identity import AuthContext
from tests.test_notification_adversarial import _MemoryTableClient


def _settings(tmp_path, **overrides) -> NotificationSettings:
    values = {
        "backend": "azure_table",
        "sqlite_path": tmp_path / "notifications.sqlite3",
        "table_endpoint": "https://example.table.core.windows.net",
        "table_name": "alternotifications",
        "servicebus_fqdn": "example.servicebus.windows.net",
        "dispatch_queue": "alter-sms-dispatch",
        "managed_identity_client_id": "",
        "sms_enabled": True,
        "sms_publish_enabled": True,
        "sms_sender": "ALTER",
        "sms_recipient_mode": "demo_allowlist",
        "demo_recipient_e164": "+353851111111",
        "acs_endpoint": "https://example.communication.azure.com",
        "daily_limit": 100,
        "retention_days": 90,
        "retry_max_attempts": 3,
        "retry_base_seconds": 10,
        "sms_activated_at_utc": "2020-01-01T00:00:00Z",
        "sms_demo_case_uid_allowlist": ("*",),
        "dispatch_min_age_seconds": 0,
        "worker_stale_seconds": 180,
        "build_id": "review-regression",
    }
    values.update(overrides)
    return NotificationSettings(**values)


def _repository(tmp_path, kind):
    if kind == "sqlite":
        return SQLiteNotificationRepository(tmp_path / "notifications.sqlite3")
    repository = AzureTableNotificationRepository.__new__(
        AzureTableNotificationRepository
    )
    repository.client = _MemoryTableClient()
    return repository


def test_information_request_creates_durable_triage_notification(tmp_path):
    """Information requests now create one durable notification for triage staff."""
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)

    result = sync_workflow_state(
        {
            "case_uid": "case-info-baseline",
            "case_status": "request_more_info",
            "review_status": "information_requested",
            "requested_fields": ["Repeat vital signs", "ECG"],
            "request_timestamp": "2026-08-14T12:30:00Z",
            "requesting_role": "ed_doctor",
            "notification_target_role": "triage_nurse",
        },
        case={"display_name": "UHL Case 006767"},
        settings=settings,
        repository=repository,
        publish=False,
    )

    assert result["notifications_created"] == 1

    visible = repository.list_notifications(
        roles=["triage_nurse"], user_id="reader", limit=30
    )
    assert len(visible) == 1
    assert visible[0]["kind"] == "information_request"

    unpublished = repository.list_unpublished(limit=30)
    assert len(unpublished) == 1


@pytest.mark.parametrize(
    ("case_status", "suppressed"),
    [
        ("discharged", False),
        ("case_closed", False),
        ("closed", False),
        ("transferred", False),
        ("completed", False),
        ("active", True),
    ],
)
def test_terminal_or_suppressed_state_cannot_recreate_stale_alerts(
    tmp_path, case_status, suppressed
):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)

    result = sync_workflow_state(
        {
            "case_uid": f"case-terminal-{case_status}-{suppressed}",
            "case_status": case_status,
            "notifications_suppressed": suppressed,
            "overdue_vitals_alert_active": True,
            "overdue_vitals_reference_at": "2026-08-14T09:00:00Z",
            "overdue_vitals_alert_created_at": "2026-08-14T12:30:00Z",
            "escalation_status": "requested",
            "escalation_requested_at": "2026-08-14T12:20:00Z",
            "escalation_target_role": "clinical_supervisor",
        },
        settings=settings,
        repository=repository,
        publish=False,
    )

    assert result["notifications_created"] == 0
    assert repository.list_notifications(
        roles=["triage_nurse", "clinical_supervisor"],
        user_id="reader",
        limit=30,
    ) == []
    assert repository.list_unpublished(limit=30) == []


@pytest.mark.parametrize("repository_kind", ["sqlite", "azure_table"])
def test_disabled_worker_creates_unpublished_successor_generation(
    tmp_path, repository_kind
):
    enabled = _settings(tmp_path)
    disabled = replace(enabled, sms_enabled=False)
    repository = _repository(tmp_path, repository_kind)
    record = replace(
        NotificationRecord.create(
            kind="escalation",
            case_uid=f"case-disabled-{repository_kind}",
            event_key="2026-08-14T12:40:00Z",
            target_role="clinical_supervisor",
            title="Escalation awaiting review",
            body="This case needs review.",
            created_at="2026-08-14T12:40:00Z",
            sms_enabled=True,
            sms_eligible=True,
        ),
        queue_published_at=utc_iso(),
    )
    repository.create_notification(record)
    payload = json.dumps(
        {
            "schema_version": 1,
            "work_type": "dispatch_notification",
            "notification_id": record.notification_id,
            "outbox_generation": record.outbox_generation,
        }
    )

    assert process_work_message(
        payload, settings=disabled, repository=repository
    ) == "deferred"
    current = repository.get_notification(record.notification_id)
    assert current is not None
    assert current.sms_state == "queued"
    assert current.outbox_generation == record.outbox_generation + 1
    assert current.queue_published_at == ""


def _install_route_repository(monkeypatch, settings, repository):
    monkeypatch.setattr(
        notification_routes.NotificationSettings,
        "from_env",
        classmethod(lambda cls: settings),
    )
    monkeypatch.setattr(
        notification_routes,
        "get_notification_repository",
        lambda _settings: repository,
    )


@pytest.mark.parametrize("endpoint", ["read", "acknowledge"])
def test_direct_mutations_reject_different_user_with_same_role(
    tmp_path, monkeypatch, endpoint
):
    settings = _settings(tmp_path, sms_enabled=False, sms_publish_enabled=False)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = NotificationRecord.create(
        kind="overdue_vitals",
        case_uid="case-person-bound",
        event_key="2026-08-14T12:50:00Z",
        target_role="ed_doctor",
        target_user_id="a-different-stable-reader-id",
        title="Vitals recheck due",
        body="Open the case to acknowledge.",
        created_at="2026-08-14T12:50:00Z",
        sms_enabled=False,
    )
    repository.create_notification(record)
    _install_route_repository(monkeypatch, settings, repository)
    other_doctor = AuthContext(
        authenticated=True,
        user_id="different-user",
        roles=["ed_doctor"],
        source="test",
    )

    with pytest.raises(HTTPException) as denied:
        if endpoint == "read":
            notification_routes.mark_notification_read(
                record.notification_id, other_doctor
            )
        else:
            notification_routes.acknowledge_notification(
                record.notification_id, other_doctor
            )
    assert denied.value.status_code == 403


@pytest.mark.parametrize("repository_kind", ["sqlite", "azure_table"])
def test_acknowledgement_audit_fields_survive_workflow_first_deactivation(
    tmp_path, repository_kind
):
    repository = _repository(tmp_path, repository_kind)
    record = NotificationRecord.create(
        kind="overdue_vitals",
        case_uid=f"case-ack-{repository_kind}",
        event_key="2026-08-14T10:00:00Z",
        target_role="triage_nurse",
        title="Vitals recheck due",
        body="Open the case to acknowledge.",
        created_at="2026-08-14T13:30:00Z",
        sms_enabled=True,
        sms_eligible=True,
    )
    repository.create_notification(record)
    repository.deactivate_notifications(
        record.case_uid,
        "overdue_vitals",
        "2026-08-14T13:31:00Z",
        cancel_sms=False,
    )

    assert repository.acknowledge_notification(
        record.notification_id,
        "assigned-nurse",
        "2026-08-14T13:31:01Z",
    )
    assert repository.acknowledge_notification(
        record.notification_id,
        "later-reader",
        "2026-08-14T13:32:00Z",
    )
    current = repository.get_notification(record.notification_id)
    assert current is not None
    assert current.active is False
    assert current.sms_state == "queued"
    assert current.acknowledged_at == "2026-08-14T13:31:01Z"
    assert current.acknowledged_by == "assigned-nurse"


class _SameGenerationRaceRepository:
    """Force the creator to lose CAS after the duplicate activates the winner."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.creator_inserted = threading.Event()
        self.noncreator_inserted = threading.Event()
        self.winner_activated = threading.Event()
        self.local = threading.local()

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def create_notification(self, record):
        value, created = self.delegate.create_notification(record)
        self.local.created = created
        if created:
            self.creator_inserted.set()
            assert self.noncreator_inserted.wait(timeout=5)
        else:
            assert self.creator_inserted.wait(timeout=5)
            self.noncreator_inserted.set()
        return value, created

    def consume_schedule_if_version(self, *args, **kwargs):
        if self.local.created:
            assert self.winner_activated.wait(timeout=5)
        return self.delegate.consume_schedule_if_version(*args, **kwargs)

    def activate_notification(self, *args, **kwargs):
        value = self.delegate.activate_notification(*args, **kwargs)
        if not self.local.created:
            self.winner_activated.set()
        return value


@pytest.mark.parametrize("repository_kind", ["sqlite", "azure_table"])
def test_same_generation_schedule_racer_preserves_winner(
    tmp_path, repository_kind
):
    settings = _settings(tmp_path)
    delegate = _repository(tmp_path, repository_kind)
    schedule, _ = delegate.upsert_schedule(
        ScheduleRecord.create(
            case_uid=f"case-race-{repository_kind}",
            reference_at=utc_iso(utc_now() - timedelta(hours=8)),
            due_minutes=210,
            target_role="triage_nurse",
            sms_eligible=True,
        )
    )
    repository = _SameGenerationRaceRepository(delegate)
    results = {}
    errors = []

    def run(name):
        try:
            results[name] = materialize_schedule(
                schedule.schedule_id,
                schedule.version,
                schedule.outbox_generation,
                settings=settings,
                repository=repository,
            )
        except Exception as exc:  # captured for deterministic thread reporting
            errors.append(exc)

    creator = threading.Thread(target=run, args=("creator",))
    creator.start()
    assert repository.creator_inserted.wait(timeout=5)
    noncreator = threading.Thread(target=run, args=("noncreator",))
    noncreator.start()
    creator.join(timeout=5)
    noncreator.join(timeout=5)

    assert not errors
    assert not creator.is_alive() and not noncreator.is_alive()
    assert {outcome for _, outcome in results.values()} <= {"created", "existing"}
    winner = results["noncreator"][0]
    assert winner is not None
    final = delegate.get_notification(winner.notification_id)
    assert final is not None
    assert final.active is True
    assert final.sms_state == "queued"
