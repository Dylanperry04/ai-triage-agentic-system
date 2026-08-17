"""Adversarial tests for the SMS demo's crash and concurrency boundaries."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import re

import pytest

from app.notifications.config import NotificationSettings
from app.notifications.models import NotificationRecord, ScheduleRecord, utc_iso, utc_now
from app.notifications.repository import (
    AzureTableNotificationRepository,
    SQLiteNotificationRepository,
)
from app.notifications.service import create_notification_for_event, materialize_schedule
from app.notifications.worker import SmsSubmissionResult, dispatch_notification


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
        "sms_activated_at_utc": "2026-08-14T12:00:00Z",
        "sms_demo_case_uid_allowlist": ("canary-1",),
        "dispatch_min_age_seconds": 0,
        "worker_stale_seconds": 180,
        "build_id": "test-build",
    }
    values.update(overrides)
    return NotificationSettings(**values)


class _Provider:
    def __init__(self, result: SmsSubmissionResult | None = None):
        self.calls = []
        self.result = result or SmsSubmissionResult(True, "acs-safe-1", 202)

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _eligible_record(*, case_uid: str = "canary-1", event: str = "2026-08-14T12:01:00Z"):
    return NotificationRecord.create(
        kind="escalation", case_uid=case_uid, event_key=event,
        target_role="clinical_supervisor", title="Escalation awaiting review",
        body="This case needs review.", created_at=event,
        sms_enabled=True, sms_eligible=True,
    )


def test_activation_watermark_and_case_allowlist_fail_closed(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    before, _ = create_notification_for_event(
        repository=repository, settings=settings, kind="escalation",
        case_uid="canary-1", event_key="2026-08-14T11:59:59Z",
        target_role="clinical_supervisor", created_at="2026-08-14T11:59:59Z",
    )
    wrong_case, _ = create_notification_for_event(
        repository=repository, settings=settings, kind="escalation",
        case_uid="not-allowlisted", event_key="2026-08-14T12:01:00Z",
        target_role="clinical_supervisor", created_at="2026-08-14T12:01:00Z",
    )
    canary, _ = create_notification_for_event(
        repository=repository, settings=settings, kind="escalation",
        case_uid="canary-1", event_key="2026-08-14T12:01:00Z",
        target_role="clinical_supervisor", created_at="2026-08-14T12:01:00Z",
    )

    assert (before.sms_state, before.sms_ineligible_reason) == (
        "disabled", "pre_activation_event"
    )
    assert (wrong_case.sms_state, wrong_case.sms_ineligible_reason) == (
        "disabled", "demo_case_not_allowlisted"
    )
    assert [row[1]["notification_id"] for row in repository.list_unpublished(limit=20)] == [
        canary.notification_id
    ]


def test_101_historical_events_create_no_sms_eligible_work(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    for index in range(101):
        record, created = create_notification_for_event(
            repository=repository, settings=settings, kind="escalation",
            case_uid=f"historical-{index}", event_key="2026-08-13T12:00:00Z",
            target_role="clinical_supervisor", created_at="2026-08-13T12:00:00Z",
        )
        assert created
        assert not record.sms_eligible
    assert repository.list_unpublished(limit=1000) == []


def test_canary_daily_cap_blocks_attempt_two(tmp_path):
    settings = _settings(
        tmp_path, daily_limit=1, sms_demo_case_uid_allowlist=("*",),
        sms_activated_at_utc="2020-01-01T00:00:00Z",
    )
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    records = [
        NotificationRecord.create(
            kind="escalation", case_uid=f"canary-{index}",
            event_key="2026-08-14T12:01:00Z",
            target_role="clinical_supervisor", title="Escalation awaiting review",
            body="This case needs review.", created_at="2026-08-14T12:01:00Z",
            sms_enabled=True, sms_eligible=True,
        )
        for index in (1, 2)
    ]
    for record in records:
        repository.create_notification(record)
    provider = _Provider()
    outcomes = [
        dispatch_notification(
            record.notification_id, expected_generation=1, settings=settings,
            repository=repository, provider=provider,
        ) for record in records
    ]
    assert outcomes == ["submitted", "cap_blocked"]
    assert len(provider.calls) == 1


def test_obsolete_queued_work_is_cancelled_before_provider_call(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = _eligible_record()
    repository.create_notification(record)
    repository.cancel_notification(record.notification_id, reason="event_replaced", now=utc_iso())
    provider = _Provider()

    assert dispatch_notification(
        record.notification_id, expected_generation=record.outbox_generation,
        settings=settings, repository=repository, provider=provider,
    ) == "cancelled"
    assert provider.calls == []


def test_old_publication_completion_cannot_mark_retry_generation_published(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = _eligible_record()
    repository.create_notification(record)
    assert repository.claim_publication(
        "notification", record.notification_id, "1", "old-publisher", utc_iso(), 120
    )
    retry_provider = _Provider(SmsSubmissionResult(False, "", 429))
    assert dispatch_notification(
        record.notification_id, expected_generation=1, settings=settings,
        repository=repository, provider=retry_provider,
    ) == "retryable"
    assert repository.get_notification(record.notification_id).outbox_generation == 2
    assert not repository.mark_published(
        "notification", record.notification_id, "1", "old-publisher", utc_iso()
    )
    assert repository.get_notification(record.notification_id).queue_published_at == ""


def test_old_schedule_worker_cannot_consume_or_cancel_replacement(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    old, _ = repository.upsert_schedule(ScheduleRecord.create(
        case_uid="canary-1", reference_at=utc_iso(utc_now() - timedelta(hours=8)),
        due_minutes=210, target_role="triage_nurse", sms_eligible=True,
    ))
    replacement, _ = repository.upsert_schedule(ScheduleRecord.create(
        case_uid="canary-1", reference_at=utc_iso(utc_now() - timedelta(minutes=5)),
        due_minutes=210, target_role="triage_nurse", sms_eligible=True,
    ))

    notification, outcome = materialize_schedule(
        old.schedule_id, old.version, old.outbox_generation,
        settings=settings, repository=repository,
    )
    current = repository.get_schedule(old.schedule_id)
    assert (notification, outcome) == (None, "stale_version")
    assert current.active is True
    assert current.version == replacement.version
    assert current.outbox_generation == replacement.outbox_generation


def test_expired_pre_submission_claim_gets_new_generation(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = _eligible_record()
    repository.create_notification(record)
    old_time = utc_iso(utc_now() - timedelta(minutes=10))
    assert repository.claim_dispatch(record.notification_id, 1, "lost", old_time, 60) == "claimed"
    assert repository.recover_expired_claims(utc_iso()) == 1
    recovered = repository.get_notification(record.notification_id)
    assert recovered.sms_state == "queued"
    assert recovered.outbox_generation == 2
    assert recovered.claim_owner == ""


@pytest.mark.parametrize("repository_kind", ["sqlite", "azure_table"])
def test_old_worker_cannot_complete_a_newer_dispatch_generation(tmp_path, repository_kind):
    settings = _settings(tmp_path)
    if repository_kind == "sqlite":
        repository = SQLiteNotificationRepository(settings.sqlite_path)
    else:
        repository = AzureTableNotificationRepository.__new__(AzureTableNotificationRepository)
        repository.client = _MemoryTableClient()
    record = replace(
        _eligible_record(), outbox_generation=2, sms_state="sending",
        claim_owner="same-owner", claim_until=utc_iso(utc_now() + timedelta(minutes=5)),
        budget_day="2026-08-14",
    )
    repository.create_notification(record)
    with pytest.raises(RuntimeError, match="dispatch state changed"):
        repository.mark_submitted(
            record.notification_id, 1, "same-owner", message_id="acs-stale-1",
            masked="+***1111", now=utc_iso(),
        )
    current = repository.get_notification(record.notification_id)
    assert current.outbox_generation == 2
    assert current.sms_state == "sending"
    assert current.acs_message_id == ""


def test_notification_pagination_exposes_item_31_and_exact_total(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    for index in range(35):
        repository.create_notification(NotificationRecord.create(
            kind="escalation", case_uid=f"case-{index}",
            event_key=f"event-{index}", target_role="clinical_supervisor",
            title="Escalation awaiting review", body="This case needs review.",
            created_at=utc_iso(utc_now() + timedelta(seconds=index)), sms_enabled=False,
        ))
    first = repository.list_notifications(
        roles=["clinical_supervisor"], user_id="reader", limit=30, offset=0,
    )
    second = repository.list_notifications(
        roles=["clinical_supervisor"], user_id="reader", limit=30, offset=30,
    )
    assert len(first) == 30
    assert len(second) == 5
    assert repository.count_notifications(
        roles=["clinical_supervisor"], user_id="reader"
    ) == 35
    assert not ({row["notification_id"] for row in first} & {
        row["notification_id"] for row in second
    })


def test_equivalent_timestamp_spellings_create_one_notification(tmp_path):
    settings = _settings(
        tmp_path, sms_activated_at_utc="2020-01-01T00:00:00Z",
        sms_demo_case_uid_allowlist=("*",),
    )
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    ids = []
    for timestamp in (
        "2026-08-14T12:00:00Z",
        "2026-08-14T12:00:00.000000+00:00",
        "2026-08-14T13:00:00+01:00",
    ):
        record, _ = create_notification_for_event(
            repository=repository, settings=settings, kind="escalation",
            case_uid="canary-1", event_key=timestamp,
            target_role="clinical_supervisor", created_at=timestamp,
        )
        ids.append(record.notification_id)
    assert len(set(ids)) == 1


@pytest.mark.parametrize("repository_kind", ["sqlite", "azure_table"])
def test_delivery_before_submission_is_eventually_applied(tmp_path, repository_kind):
    settings = _settings(tmp_path)
    if repository_kind == "sqlite":
        repository = SQLiteNotificationRepository(settings.sqlite_path)
    else:
        repository = AzureTableNotificationRepository.__new__(AzureTableNotificationRepository)
        repository.client = _MemoryTableClient()
    record = _eligible_record()
    repository.create_notification(record)
    now = utc_iso()
    assert repository.claim_dispatch(record.notification_id, 1, "worker", now, 300) == "claimed"
    assert repository.reserve_attempt(
        record.notification_id, 1, "worker", "2026-08-14", 100
    ) == "reserved"
    assert repository.mark_sending(
        record.notification_id, 1, "worker", now,
        settings.sms_rollout_policy_version,
    )
    assert repository.record_delivery(
        event_id="delivery-before-correlation", message_id="acs-early-1",
        status="Delivered", observed_at=now, detail_code="carrier_delivered",
    )
    assert repository.get_notification(record.notification_id).delivery_status == ""
    repository.mark_submitted(
        record.notification_id, 1, "worker", message_id="acs-early-1",
        masked="+***1111", now=now,
    )
    current = repository.get_notification(record.notification_id)
    assert current.sms_state == "delivered"
    assert current.delivery_status == "delivered"
    assert repository.repair_delivery_state(limit=100)["delivery_events_applied"] == 0


def test_azure_correlation_write_failure_is_repaired(tmp_path):
    settings = _settings(tmp_path)
    repository = AzureTableNotificationRepository.__new__(AzureTableNotificationRepository)
    repository.client = _MemoryTableClient()
    record = _eligible_record()
    repository.create_notification(record)
    now = utc_iso()
    assert repository.claim_dispatch(record.notification_id, 1, "worker", now, 300) == "claimed"
    assert repository.reserve_attempt(record.notification_id, 1, "worker", "2026-08-14", 100) == "reserved"
    assert repository.mark_sending(
        record.notification_id, 1, "worker", now,
        settings.sms_rollout_policy_version,
    )
    repository.record_delivery(
        event_id="early-event", message_id="acs-repair-1", status="Delivered",
        observed_at=now, detail_code="carrier_delivered",
    )
    repository.client.fail_create_partition_once = "acs_correlation"
    with pytest.raises(_TableError) as failure:
        repository.mark_submitted(
            record.notification_id, 1, "worker", message_id="acs-repair-1",
            masked="+***1111", now=now,
        )
    assert failure.value.status_code == 500
    assert repository.get_notification(record.notification_id).sms_state == "submitted"
    repaired = repository.repair_delivery_state(limit=100)
    assert repaired == {"correlations_repaired": 1, "delivery_events_applied": 1}
    assert repository.get_notification(record.notification_id).sms_state == "delivered"


def test_azure_delivery_application_failure_repairs_on_redelivery(tmp_path):
    repository = AzureTableNotificationRepository.__new__(AzureTableNotificationRepository)
    repository.client = _MemoryTableClient()
    record = replace(
        _eligible_record(), sms_state="submitted", acs_message_id="acs-redelivery-1"
    )
    repository.create_notification(record)
    repository.client.create_entity(repository._entity(
        "acs_correlation", "acs-redelivery-1",
        {"notification_id": record.notification_id, "created_at": utc_iso()},
    ))
    repository.client.fail_update_partition_once = "delivery_event"
    with pytest.raises(_TableError) as failure:
        repository.record_delivery(
            event_id="redelivered-event", message_id="acs-redelivery-1",
            status="Delivered", observed_at=utc_iso(), detail_code="carrier_delivered",
        )
    assert failure.value.status_code == 500
    assert repository.get_notification(record.notification_id).sms_state == "delivered"
    assert not repository.record_delivery(
        event_id="redelivered-event", message_id="acs-redelivery-1",
        status="Delivered", observed_at=utc_iso(), detail_code="carrier_delivered",
    )
    assert repository.repair_delivery_state(limit=100)["delivery_events_applied"] == 0


class _TableError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"table status {status_code}")
        self.status_code = status_code


class _Entity(dict):
    def __init__(self, value, etag):
        super().__init__(value)
        self.metadata = {"etag": etag}


class _MemoryTableClient:
    """Small ETag-aware emulator used to run the Azure repository contract locally."""

    def __init__(self):
        self.rows = {}
        self.revision = 0
        self.fail_create_partition_once = None
        self.fail_update_partition_once = None

    def _etag(self):
        self.revision += 1
        return f'W/"{self.revision}"'

    def create_entity(self, entity):
        if self.fail_create_partition_once == entity["PartitionKey"]:
            self.fail_create_partition_once = None
            raise _TableError(500)
        key = (entity["PartitionKey"], entity["RowKey"])
        if key in self.rows:
            raise _TableError(409)
        self.rows[key] = (dict(entity), self._etag())

    def get_entity(self, *, partition_key, row_key):
        try:
            value, etag = self.rows[(partition_key, row_key)]
        except KeyError as exc:
            raise _TableError(404) from exc
        return _Entity(dict(value), etag)

    def update_entity(self, *, entity, mode, etag, match_condition):
        del mode, match_condition
        if self.fail_update_partition_once == entity["PartitionKey"]:
            self.fail_update_partition_once = None
            raise _TableError(500)
        key = (entity["PartitionKey"], entity["RowKey"])
        if key not in self.rows:
            raise _TableError(404)
        if self.rows[key][1] != etag:
            raise _TableError(412)
        self.rows[key] = (dict(entity), self._etag())

    def upsert_entity(self, entity):
        key = (entity["PartitionKey"], entity["RowKey"])
        self.rows[key] = (dict(entity), self._etag())

    def query_entities(self, query_filter):
        partition_match = re.search(r"PartitionKey eq '([^']+)'", query_filter)
        partition = partition_match.group(1) if partition_match else None
        output = []
        for (row_partition, _), (value, etag) in self.rows.items():
            if partition and row_partition != partition:
                continue
            if "sms_state eq 'sending'" in query_filter and value.get("sms_state") != "sending":
                continue
            if "sms_state eq 'claimed'" in query_filter and value.get("sms_state") != "claimed":
                continue
            if "applied eq false" in query_filter and bool(value.get("applied")):
                continue
            output.append(_Entity(dict(value), etag))
        return output
