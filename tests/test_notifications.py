"""Notification/SMS reliability, privacy, concurrency, and state-machine tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
import threading
import base64
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from app.notifications.config import NotificationSettings
from app.notifications.models import (
    NotificationRecord,
    ScheduleRecord,
    assert_one_segment_gsm7,
    mask_e164,
    sms_template,
    utc_iso,
    utc_now,
)
from app.notifications.repository import AzureTableNotificationRepository, SQLiteNotificationRepository
from app.notifications.service import create_notification_for_event, materialize_schedule, sync_workflow_state
from app.notifications.worker import (
    SmsSubmissionResult,
    dispatch_notification,
    parse_work_message,
    process_delivery_report,
)


def _principal(groups, user="notification-user"):
    claims = [
        {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": user},
        {"typ": "name", "val": "Notification Tester"},
    ] + [{"typ": "groups", "val": group} for group in groups]
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


def _settings(tmp_path, **overrides):
    values = dict(
        backend="azure_table",
        sqlite_path=tmp_path / "notifications.sqlite3",
        table_endpoint="https://example.table.core.windows.net",
        table_name="alternotifications",
        servicebus_fqdn="example.servicebus.windows.net",
        dispatch_queue="alter-sms-dispatch",
        managed_identity_client_id="",
        sms_enabled=True,
        sms_publish_enabled=True,
        sms_sender="ALTER",
        sms_recipient_mode="demo_allowlist",
        # Deliberately non-owner test data; the supplied personal number must
        # never appear in source, logs, fixtures, queue payloads, or docs.
        demo_recipient_e164="+353851111111",
        acs_endpoint="https://example.communication.azure.com",
        daily_limit=100,
        retention_days=90,
        retry_max_attempts=3,
        retry_base_seconds=10,
        sms_activated_at_utc="2020-01-01T00:00:00Z",
        sms_demo_case_uid_allowlist=("*",),
        dispatch_min_age_seconds=0,
    )
    values.update(overrides)
    return NotificationSettings(**values)


def _record(settings, *, number=1, kind="escalation"):
    return NotificationRecord.create(
        kind=kind,
        case_uid=f"case-{number}",
        event_key=f"event-{number}",
        target_role="clinical_supervisor" if kind == "escalation" else "triage_nurse",
        title="Escalation awaiting review" if kind == "escalation" else "Vitals recheck due",
        body="This case needs review.",
        sms_enabled=settings.sms_publish_enabled,
        retention_days=settings.retention_days,
    )


class SuccessfulProvider:
    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def send(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
            number = len(self.calls)
        return SmsSubmissionResult(True, f"acs-{number}", 202)


class ResultProvider:
    def __init__(self, status):
        self.status = status

    def send(self, **kwargs):
        del kwargs
        return SmsSubmissionResult(False, "", self.status)


class AmbiguousProvider:
    def send(self, **kwargs):
        del kwargs
        raise TimeoutError("message body and recipient must never be logged")


class InvalidAcceptedIdProvider:
    def send(self, **kwargs):
        del kwargs
        return SmsSubmissionResult(True, "contains spaces", 202)


def test_notification_identity_is_deterministic_and_create_is_idempotent(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = _record(settings)
    first, created_first = repository.create_notification(record)
    second, created_second = repository.create_notification(record)
    assert created_first is True
    assert created_second is False
    assert first.notification_id == second.notification_id


def test_workflow_reconciliation_creates_one_escalation_and_deactivates_when_closed(tmp_path):
    settings = _settings(tmp_path, sms_enabled=False, sms_publish_enabled=False)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    state = {
        "case_uid": "case-1",
        "escalation_status": "requested",
        "escalation_requested_at": "2026-08-13T10:00:00+00:00",
        "escalation_target_role": "clinical_supervisor",
    }
    first = sync_workflow_state(state, settings=settings, repository=repository, publish=False)
    second = sync_workflow_state(state, settings=settings, repository=repository, publish=False)
    assert first["notifications_created"] == 1
    assert second["notifications_created"] == 0
    assert len(repository.list_notifications(roles=["clinical_supervisor"], user_id="u", limit=30)) == 1

    sync_workflow_state(
        {**state, "case_status": "case_closed", "escalation_status": "closed"},
        settings=settings,
        repository=repository,
        publish=False,
    )
    assert repository.list_notifications(roles=["clinical_supervisor"], user_id="u", limit=30) == []


def test_new_vitals_version_invalidates_stale_scheduled_message(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    old = ScheduleRecord.create(
        case_uid="case-1",
        reference_at=utc_iso(utc_now() - timedelta(hours=8)),
        due_minutes=210,
        target_role="triage_nurse",
    )
    repository.upsert_schedule(old)
    new = ScheduleRecord.create(
        case_uid="case-1",
        reference_at=utc_iso(utc_now() - timedelta(minutes=5)),
        due_minutes=210,
        target_role="triage_nurse",
    )
    repository.upsert_schedule(new)
    record, result = materialize_schedule(
        old.schedule_id, old.version, old.outbox_generation,
        settings=settings, repository=repository
    )
    assert record is None
    assert result == "stale_version"


def test_due_schedule_materialises_exactly_one_notification(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    schedule = ScheduleRecord.create(
        case_uid="case-1",
        reference_at=utc_iso(utc_now() - timedelta(hours=8)),
        due_minutes=210,
        target_role="triage_nurse",
    )
    repository.upsert_schedule(schedule)
    first, first_result = materialize_schedule(
        schedule.schedule_id, schedule.version, schedule.outbox_generation,
        settings=settings, repository=repository
    )
    second, second_result = materialize_schedule(
        schedule.schedule_id, schedule.version, schedule.outbox_generation,
        settings=settings, repository=repository
    )
    assert first_result == "created"
    assert second_result == "existing"
    assert second.notification_id == first.notification_id
    assert repository.get_schedule(schedule.schedule_id).active is False


def test_function_materialised_due_alert_survives_unrelated_workflow_update(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    reference = utc_iso(utc_now() - timedelta(hours=8))
    schedule = ScheduleRecord.create(
        case_uid="case-1",
        reference_at=reference,
        due_minutes=210,
        target_role="triage_nurse",
    )
    repository.upsert_schedule(schedule)
    record, result = materialize_schedule(
        schedule.schedule_id, schedule.version, schedule.outbox_generation,
        settings=settings, repository=repository
    )
    assert result == "created"

    # A later workflow update may not yet carry the legacy active-alert flag.
    sync_workflow_state(
        {"case_uid": "case-1", "last_vitals_updated_at": reference},
        settings=settings,
        repository=repository,
        publish=False,
    )
    visible = repository.list_notifications(
        roles=["triage_nurse"], user_id="reader", limit=30
    )
    assert [item["notification_id"] for item in visible] == [record.notification_id]
    assert repository.get_schedule(schedule.schedule_id).active is False


def test_new_vitals_hide_old_alert_but_ack_does_not_cancel_queued_sms(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    reference = utc_iso(utc_now() - timedelta(hours=8))
    old, _ = create_notification_for_event(
        repository=repository,
        settings=settings,
        kind="overdue_vitals",
        case_uid="case-1",
        event_key=reference,
        target_role="triage_nurse",
        created_at=utc_iso(),
    )
    fresh_reference = utc_iso()
    sync_workflow_state(
        {"case_uid": "case-1", "last_vitals_updated_at": fresh_reference},
        settings=settings,
        repository=repository,
        publish=False,
    )
    assert repository.list_notifications(
        roles=["triage_nurse"], user_id="reader", limit=30
    ) == []
    assert repository.get_notification(old.notification_id).sms_state == "cancelled"

    current, _ = create_notification_for_event(
        repository=repository,
        settings=settings,
        kind="overdue_vitals",
        case_uid="case-1",
        event_key=fresh_reference,
        target_role="triage_nurse",
        created_at=utc_iso(),
    )
    sync_workflow_state(
        {
            "case_uid": "case-1",
            "last_vitals_updated_at": fresh_reference,
            "overdue_vitals_acknowledged_reference_at": fresh_reference,
        },
        settings=settings,
        repository=repository,
        publish=False,
    )
    assert repository.get_notification(current.notification_id).active is False
    assert repository.get_notification(current.notification_id).sms_state == "queued"


def test_hard_daily_cap_never_submits_attempt_101_under_concurrency(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    provider = SuccessfulProvider()
    records = [_record(settings, number=index) for index in range(1, 111)]
    for record in records:
        repository.create_notification(record)

    def send(record):
        return dispatch_notification(
            record.notification_id,
            settings=settings,
            repository=repository,
            provider=provider,
        )

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(send, records))
    assert results.count("submitted") == 100
    assert results.count("cap_blocked") == 10
    assert len(provider.calls) == 100
    assert all(call["sender"] == "ALTER" for call in provider.calls)


def test_queue_redelivery_cannot_submit_same_notification_twice(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    provider = SuccessfulProvider()
    record = _record(settings)
    repository.create_notification(record)
    assert dispatch_notification(record.notification_id, settings=settings, repository=repository, provider=provider) == "submitted"
    assert dispatch_notification(record.notification_id, settings=settings, repository=repository, provider=provider) == "submitted"
    assert len(provider.calls) == 1


def test_known_429_is_retryable_but_transport_timeout_is_ambiguous(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    retry_record = _record(settings, number=1)
    ambiguous_record = _record(settings, number=2)
    repository.create_notification(retry_record)
    repository.create_notification(ambiguous_record)
    assert dispatch_notification(
        retry_record.notification_id, settings=settings, repository=repository, provider=ResultProvider(429)
    ) == "retryable"
    retry_state = repository.get_notification(retry_record.notification_id)
    assert retry_state.sms_state == "retryable"
    assert retry_state.queue_published_at == ""
    assert dispatch_notification(
        ambiguous_record.notification_id, settings=settings, repository=repository, provider=AmbiguousProvider()
    ) == "ambiguous"
    assert repository.get_notification(ambiguous_record.notification_id).sms_state == "ambiguous"


def test_invalid_accepted_message_id_is_ambiguous_and_never_retried(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = _record(settings)
    repository.create_notification(record)
    assert dispatch_notification(
        record.notification_id,
        settings=settings,
        repository=repository,
        provider=InvalidAcceptedIdProvider(),
    ) == "ambiguous"
    assert repository.get_notification(record.notification_id).error_code == "acs_invalid_message_id"


def test_stale_sending_lease_fails_closed_as_ambiguous(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = _record(settings)
    repository.create_notification(record)
    past = utc_iso(utc_now() - timedelta(minutes=10))
    assert repository.claim_dispatch(
        record.notification_id, record.outbox_generation, "lost-worker", past, 60
    ) == "claimed"
    assert repository.reserve_attempt(
        record.notification_id, record.outbox_generation,
        "lost-worker",
        utc_now().strftime("%Y-%m-%d"),
        settings.daily_limit,
    ) == "reserved"
    assert repository.mark_sending(
        record.notification_id, record.outbox_generation, "lost-worker", past,
        settings.sms_rollout_policy_version,
    ) is True
    assert repository.recover_stale_sending(utc_iso()) == 1
    recovered = repository.get_notification(record.notification_id)
    assert recovered.sms_state == "ambiguous"
    assert recovered.error_code == "worker_lost_during_submission"


def test_recipient_preflight_failure_does_not_consume_daily_budget(tmp_path):
    settings = _settings(tmp_path, demo_recipient_e164="not-a-phone")
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = _record(settings)
    repository.create_notification(record)
    provider = SuccessfulProvider()
    assert dispatch_notification(
        record.notification_id,
        settings=settings,
        repository=repository,
        provider=provider,
    ) == "failed_permanent"
    failed = repository.get_notification(record.notification_id)
    assert failed.attempt_count == 0
    assert provider.calls == []


def test_sms_templates_are_gsm7_one_segment_and_contain_no_clinical_identifiers():
    forbidden = ("patient", "case", "mrn", "acuity", "diagnosis", "sepsis", "http://", "https://")
    for kind in ("overdue_vitals", "escalation", "clinical_alert"):
        message = sms_template(kind)
        assert_one_segment_gsm7(message)
        assert len(message) <= 160
        assert not any(value in message.lower() for value in forbidden)


def test_work_messages_contain_only_schema_and_immutable_ids():
    raw = json.dumps({
        "schema_version": 1,
        "work_type": "dispatch_notification",
        "notification_id": "ntf-v1-abc123",
        "outbox_generation": 1,
    })
    assert parse_work_message(raw) == {
        "schema_version": 1,
        "work_type": "dispatch_notification",
        "notification_id": "ntf-v1-abc123",
        "outbox_generation": 1,
    }
    with pytest.raises(ValueError):
        parse_work_message("x" * 9000)
    with pytest.raises(ValueError, match="outbox_generation"):
        parse_work_message(json.dumps({
            "schema_version": 1,
            "work_type": "dispatch_notification",
            "notification_id": "ntf-v1-abc123",
        }))


def test_delivery_reports_are_deduplicated_and_cannot_downgrade_delivered(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    provider = SuccessfulProvider()
    record = _record(settings)
    repository.create_notification(record)
    dispatch_notification(record.notification_id, settings=settings, repository=repository, provider=provider)
    message_id = repository.get_notification(record.notification_id).acs_message_id
    delivered = [{
        "id": "event-delivered",
        "eventType": "Microsoft.Communication.SMSDeliveryReportReceived",
        "eventTime": "2026-08-13T10:01:00+00:00",
        "data": {
            "messageId": message_id,
            "deliveryStatus": "Delivered",
            "receivedTimestamp": "2026-08-13T10:01:00+00:00",
            "deliveryAttempts": [{"segmentsSucceeded": 1, "segmentsFailed": 0}],
            "to": "+353000000000",
        },
    }]
    assert process_delivery_report(json.dumps(delivered), repository=repository)["accepted"] == 1
    assert process_delivery_report(json.dumps(delivered), repository=repository)["duplicates"] == 1
    failed_later = [{
        "id": "event-failed-later",
        "eventType": "Microsoft.Communication.SMSDeliveryReportReceived",
        "eventTime": "2026-08-13T10:02:00+00:00",
        "data": {
            "messageId": message_id,
            "deliveryStatus": "Failed",
            "receivedTimestamp": "2026-08-13T10:02:00+00:00",
            "deliveryAttempts": [{"segmentsSucceeded": 0, "segmentsFailed": 1}],
        },
    }]
    process_delivery_report(json.dumps(failed_later), repository=repository)
    current = repository.get_notification(record.notification_id)
    assert current.sms_state == "delivered"
    assert current.delivery_status == "delivered"


def test_mark_read_is_per_user_and_persists(tmp_path):
    settings = _settings(tmp_path, sms_enabled=False, sms_publish_enabled=False)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = _record(settings)
    repository.create_notification(record)
    assert repository.mark_read(record.notification_id, "reader-a", utc_iso()) is True
    a = repository.list_notifications(roles=[record.target_role], user_id="reader-a", limit=30)[0]
    b = repository.list_notifications(roles=[record.target_role], user_id="reader-b", limit=30)[0]
    assert a["is_read"] == 1
    assert b["is_read"] == 0


def test_acknowledgement_hides_notification_without_cancelling_sms(tmp_path):
    settings = _settings(tmp_path)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    record = _record(settings, kind="overdue_vitals")
    repository.create_notification(record)
    assert repository.acknowledge_notification(
        record.notification_id, "reader-a", utc_iso()
    ) is True
    current = repository.get_notification(record.notification_id)
    assert current.active is False
    assert current.sms_state == "queued"
    unpublished = repository.list_unpublished(limit=10)
    assert [(kind, value["notification_id"]) for kind, value in unpublished] == [
        ("notification", record.notification_id)
    ]


def test_retention_removes_expired_operational_state(tmp_path):
    settings = _settings(tmp_path, sms_enabled=False, sms_publish_enabled=False)
    repository = SQLiteNotificationRepository(settings.sqlite_path)
    created_at = utc_iso(utc_now() - timedelta(days=91))
    expired = NotificationRecord.create(
        kind="clinical_alert",
        case_uid="case-expired",
        event_key="event-expired",
        target_role="triage_nurse",
        title="Clinical alert",
        body="A clinical alert needs review.",
        created_at=created_at,
        sms_enabled=False,
        retention_days=90,
    )
    repository.create_notification(expired)
    deleted = repository.purge_expired(utc_iso(), retention_days=90)
    assert deleted["notifications"] == 1
    assert repository.get_notification(expired.notification_id) is None
    with pytest.raises(ValueError):
        repository.purge_expired(utc_iso(), retention_days=0)


def test_azure_table_repository_preserves_etag_metadata_for_safe_updates():
    class Entity(dict):
        metadata = {"etag": 'W/"safe-etag"'}

    class Client:
        def get_entity(self, **kwargs):
            assert kwargs == {"partition_key": "notification", "row_key": "row-1"}
            return Entity(PartitionKey="notification", RowKey="row-1", value="ok")

    repository = AzureTableNotificationRepository.__new__(AzureTableNotificationRepository)
    repository.client = Client()
    entity = repository._get("notification", "row-1")
    assert entity["etag"] == 'W/"safe-etag"'
    with pytest.raises(RuntimeError, match="explicit Azure Table ETag"):
        repository._replace(entity, "*")


def test_mask_never_exposes_more_than_last_four_digits():
    assert mask_e164("+353851111111") == "+***1111"


def test_notification_api_filters_by_role_and_persists_read_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
    monkeypatch.setenv("NOTIFICATION_BACKEND", "sqlite")
    monkeypatch.setenv("NOTIFICATION_SQLITE_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("SMS_ENABLED", "false")
    monkeypatch.setenv("SMS_PUBLISH_ENABLED", "false")
    from app.notifications.repository import get_notification_repository, reset_notification_repository_for_tests

    reset_notification_repository_for_tests()
    settings = NotificationSettings.from_env()
    repository = get_notification_repository(settings)
    ed = NotificationRecord.create(
        kind="escalation", case_uid="case-api-1", event_key="event-ed",
        target_role="ed_doctor", title="Escalation awaiting review",
        body="This case needs a senior decision.", sms_enabled=False,
    )
    nurse = NotificationRecord.create(
        kind="overdue_vitals", case_uid="case-api-2", event_key="event-nurse",
        target_role="triage_nurse", title="Vitals recheck due",
        body="Observations need review.", sms_enabled=False,
    )
    repository.create_notification(ed)
    repository.create_notification(nurse)

    from app.main import app

    headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
    with TestClient(app) as client:
        response = client.get("/notifications", headers=headers)
        assert response.status_code == 200
        rows = response.json()["notifications"]
        assert [row["notification_id"] for row in rows] == [ed.notification_id]
        assert rows[0]["read"] is False
        marked = client.post(f"/notifications/{ed.notification_id}/read", headers=headers)
        assert marked.status_code == 200
        reloaded = client.get("/notifications", headers=headers).json()["notifications"]
        assert reloaded[0]["read"] is True

        nurse_headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["triage-nurses"], "nurse-user")}
        monkeypatch.setattr(
            "app.api.case_routes.acknowledge_overdue_vitals_event",
            lambda case_uid, expected_reference, durable_target_role, ctx: {
                "case_uid": case_uid, "status": "acknowledged", "idempotent": False,
            },
        )
        acknowledged = client.post(
            f"/notifications/{nurse.notification_id}/acknowledge", headers=nurse_headers
        )
        assert acknowledged.status_code == 200
        assert client.get("/notifications", headers=nurse_headers).json()["notifications"] == []
        assert repository.get_notification(nurse.notification_id).sms_state == "disabled"

    reset_notification_repository_for_tests()


def test_durable_due_notification_acknowledges_before_legacy_sweep(monkeypatch):
    from app.api import case_routes
    from app.security.identity import AuthContext
    from app.notifications.models import canonical_time_key

    reference = utc_iso(utc_now() - timedelta(hours=8))
    resolved = SimpleNamespace(
        case_uid="case-durable-due", source_dataset="uhl",
        case={"edstay": {"last_vitals_updated_at": reference}},
    )
    persisted = []
    monkeypatch.setattr(case_routes, "_resolve_or_404", lambda case_uid: resolved)
    monkeypatch.setattr(
        case_routes, "_latest_workflow_state",
        lambda case_uid: {"case_uid": case_uid, "overdue_vitals_alert_active": False},
    )
    monkeypatch.setattr(
        case_routes, "_append_workflow_state",
        lambda state: persisted.append(state) or state,
    )
    result = case_routes.acknowledge_overdue_vitals_event(
        resolved.case_uid, expected_reference=canonical_time_key(reference),
        durable_target_role="triage_nurse",
        ctx=AuthContext(
            authenticated=True, user_id="nurse", roles=["triage_nurse"], source="test"
        ),
    )
    assert result["status"] == "acknowledged"
    assert result["idempotent"] is False
    assert persisted[0]["overdue_vitals_alert_active"] is False
    assert canonical_time_key(
        persisted[0]["overdue_vitals_acknowledged_reference_at"]
    ) == canonical_time_key(reference)



def test_servicesms_requires_messaging_connect_key_when_live(monkeypatch):
    monkeypatch.setenv("NOTIFICATION_BACKEND", "azure_table")
    monkeypatch.setenv("NOTIFICATION_TABLE_ENDPOINT", "https://example.table.core.windows.net")
    monkeypatch.setenv("SERVICEBUS_FQDN", "example.servicebus.windows.net")
    monkeypatch.setenv("SMS_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SMS_ENABLED", "true")
    monkeypatch.setenv("SMS_SENDER", "ServiceSMS")
    monkeypatch.setenv("ACS_ENDPOINT", "https://example.communication.azure.com")
    monkeypatch.setenv("DEMO_SMS_RECIPIENT", "+353851111111")
    monkeypatch.setenv("SMS_ACTIVATED_AT_UTC", "2026-08-18T09:00:00Z")
    monkeypatch.setenv("SMS_DEMO_CASE_UID_ALLOWLIST", "case-1")
    monkeypatch.setenv("MESSAGING_CONNECT_PARTNER", "infobip")
    monkeypatch.delenv("MESSAGING_CONNECT_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MESSAGING_CONNECT_API_KEY"):
        NotificationSettings.from_env()

    monkeypatch.setenv("MESSAGING_CONNECT_API_KEY", "synthetic-test-key")
    settings = NotificationSettings.from_env()
    assert settings.sms_sender == "ServiceSMS"
    assert settings.messaging_connect_partner == "infobip"
    assert settings.messaging_connect_api_version == "2025-05-29-preview"


def test_messaging_connect_result_parser_accepts_preview_shape():
    from app.notifications.worker import AzureCommunicationSmsProvider

    result = AzureCommunicationSmsProvider._result_from_payload(
        {
            "value": [{
                "to": "+353851111111",
                "successful": True,
                "messageId": "acs-preview-123",
                "httpStatusCode": 202,
            }]
        },
        202,
    )
    assert result.successful is True
    assert result.message_id == "acs-preview-123"
    assert result.http_status_code == 202
