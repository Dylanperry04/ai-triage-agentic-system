"""Regression coverage for independently reproduced v25.5 rollout defects."""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.notifications.models import NotificationRecord
from app.notifications.worker import SmsSubmissionResult, dispatch_notification
from tests.test_notification_adversarial import _MemoryTableClient, _settings
from app.notifications.repository import (
    AzureTableNotificationRepository,
    SQLiteNotificationRepository,
)


class _Provider:
    def __init__(self):
        self.calls: list[dict] = []

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return SmsSubmissionResult(True, "acs-policy-test", 202)


def _repository(tmp_path, kind):
    if kind == "sqlite":
        return SQLiteNotificationRepository(tmp_path / "notifications.sqlite3")
    repository = AzureTableNotificationRepository.__new__(
        AzureTableNotificationRepository
    )
    repository.client = _MemoryTableClient()
    return repository


def _queued_record(case_uid: str = "former-canary") -> NotificationRecord:
    return NotificationRecord.create(
        kind="escalation",
        case_uid=case_uid,
        event_key="2026-08-14T12:01:00Z",
        target_role="clinical_supervisor",
        title="Escalation awaiting review",
        body="This case needs review.",
        created_at="2026-08-14T12:01:00Z",
        sms_enabled=True,
        sms_eligible=True,
    )


@pytest.mark.parametrize("repository_kind", ["sqlite", "azure_table"])
def test_current_allowlist_revocation_cancels_previously_eligible_work(
    tmp_path, repository_kind
):
    repository = _repository(tmp_path, repository_kind)
    record = _queued_record()
    repository.create_notification(record)
    current_policy = _settings(
        tmp_path,
        sms_demo_case_uid_allowlist=("new-canary",),
    )
    provider = _Provider()

    outcome = dispatch_notification(
        record.notification_id,
        expected_generation=record.outbox_generation,
        settings=current_policy,
        repository=repository,
        provider=provider,
        owner="policy-worker",
    )

    assert outcome == "policy_revoked"
    assert provider.calls == []
    current = repository.get_notification(record.notification_id)
    assert current is not None
    assert current.active is True
    assert current.sms_state == "cancelled"
    assert current.cancel_reason == "rollout_policy_revoked"
    assert current.attempt_count == 0
    assert current.claim_owner == ""


class _PolicyRevokedImmediatelyBeforeSend:
    """Write a newer durable policy after the owned sending transition."""

    def __init__(self, delegate, revoked_policy):
        self._delegate = delegate
        self._revoked_policy = revoked_policy

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def mark_sending(self, *args, **kwargs):
        changed = self._delegate.mark_sending(*args, **kwargs)
        if changed:
            self._delegate.upsert_rollout_policy(self._revoked_policy)
        return changed


@pytest.mark.parametrize("repository_kind", ["sqlite", "azure_table"])
def test_policy_is_rechecked_immediately_before_chargeable_send(
    tmp_path, repository_kind
):
    repository = _repository(tmp_path, repository_kind)
    record = _queued_record("canary-1")
    repository.create_notification(record)
    settings = _settings(
        tmp_path,
        sms_rollout_policy_version="20260817T120000000000Z-canary",
    )
    revoked_policy = replace(
        settings.rollout_policy(),
        version="20260817T120001000000Z-revoked",
        sms_demo_case_uid_allowlist=("replacement-canary",),
    )
    repository = _PolicyRevokedImmediatelyBeforeSend(repository, revoked_policy)
    provider = _Provider()

    outcome = dispatch_notification(
        record.notification_id,
        expected_generation=record.outbox_generation,
        settings=settings,
        repository=repository,
        provider=provider,
        owner="policy-worker",
    )

    assert outcome == "policy_revoked"
    assert provider.calls == []
    current = repository.get_notification(record.notification_id)
    assert current is not None
    assert current.sms_state == "cancelled"
    assert current.cancel_reason == "rollout_policy_revoked"
    assert current.rollout_policy_version == settings.sms_rollout_policy_version
    # The hard daily cap is conservative: a reservation is never refunded after
    # the record reaches sending, even though policy prevented the ACS call.
    assert current.attempt_count == 1


@pytest.mark.parametrize("repository_kind", ["sqlite", "azure_table"])
def test_stale_process_cannot_overwrite_a_newer_durable_rollout_policy(
    tmp_path, repository_kind
):
    repository = _repository(tmp_path, repository_kind)
    settings = _settings(
        tmp_path,
        sms_rollout_policy_version="20260817T120000000000Z-old",
    )
    newer = replace(
        settings.rollout_policy(),
        version="20260817T120001000000Z-new",
        sms_demo_case_uid_allowlist=("replacement-canary",),
    )
    repository.upsert_rollout_policy(newer)

    active = repository.upsert_rollout_policy(settings.rollout_policy())

    assert active.version == newer.version
    assert active.sms_demo_case_uid_allowlist == ("replacement-canary",)


@pytest.mark.parametrize("repository_kind", ["sqlite", "azure_table"])
def test_canary_pre_enable_gate_requires_an_empty_eligible_backlog(
    tmp_path, repository_kind
):
    repository = _repository(tmp_path, repository_kind)
    assert repository.pre_enable_report(
        allowed_case_uids={"canary-1"}
    )["safe_to_enable"] is True

    repository.create_notification(_queued_record("canary-1"))
    report = repository.pre_enable_report(allowed_case_uids={"canary-1"})

    assert report["eligible_notifications"] == 1
    assert report["eligible_schedules"] == 0
    assert report["non_canary_work"] == 0
    assert report["queue_empty"] is False
    assert report["safe_to_enable"] is False


def test_function_treats_durably_cancelled_policy_work_as_safe_to_settle():
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "functions"
        / "notification_worker"
        / "function_app.py"
    ).read_text(encoding="utf-8")
    assert '"policy_revoked"' in source
