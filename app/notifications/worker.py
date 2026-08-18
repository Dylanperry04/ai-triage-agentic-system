"""Idempotent ACS dispatcher and privacy-safe delivery-report processor."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
import logging
import uuid
from typing import Any

from app.notifications.config import (
    NotificationSettings,
    SmsRolloutPolicy,
    validate_sms_startup,
)
from app.notifications.models import (
    assert_one_segment_gsm7,
    canonical_time_key,
    sms_template,
    utc_iso,
    utc_now,
    validate_identifier,
)
from app.notifications.recipient import DemoAllowlistRecipientResolver
from app.notifications.repository import NotificationRepository, get_notification_repository
from app.notifications.service import materialize_schedule


logger = logging.getLogger("alter.notifications.worker")


@dataclass(frozen=True)
class SmsSubmissionResult:
    successful: bool
    message_id: str = ""
    http_status_code: int = 0


class _AcsHttpError(RuntimeError):
    """HTTP failure carrying only status code for existing retry classification."""

    def __init__(self, status_code: int):
        super().__init__(f"acs_http_{int(status_code)}")
        self.status_code = int(status_code)


class AzureCommunicationSmsProvider:
    def __init__(self, settings: NotificationSettings):
        try:
            from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise RuntimeError("Azure identity dependency is not installed") from exc

        self._settings = settings
        self._credential = (
            ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
            if settings.managed_identity_client_id
            else DefaultAzureCredential(exclude_interactive_browser_credential=True)
        )
        self._client = None

        if not settings.messaging_connect_api_key:
            try:
                from azure.communication.sms import SmsClient
            except ImportError as exc:  # pragma: no cover - packaging guard
                raise RuntimeError("Azure Communication Services SMS dependency is not installed") from exc
            self._client = SmsClient(settings.acs_endpoint, self._credential)

    @staticmethod
    def _result_from_payload(payload: Any, response_status: int) -> SmsSubmissionResult:
        items = payload.get("value") if isinstance(payload, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise RuntimeError("acs_unexpected_result_count")
        item = items[0]
        status = int(item.get("httpStatusCode") or response_status or 0)
        return SmsSubmissionResult(
            successful=bool(item.get("successful", False)),
            message_id=str(item.get("messageId") or ""),
            http_status_code=status,
        )

    def _send_messaging_connect(
        self, *, sender: str, recipient: str, message: str, tag: str
    ) -> SmsSubmissionResult:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise RuntimeError("requests is required for Messaging Connect REST transport") from exc

        token = self._credential.get_token(
            "https://communication.azure.com/.default"
        ).token
        url = (
            f"{self._settings.acs_endpoint.rstrip('/')}/sms"
            f"?api-version={self._settings.messaging_connect_api_version}"
        )
        payload = {
            "from": sender,
            "smsRecipients": [{"to": recipient}],
            "message": message,
            "smsSendOptions": {
                "enableDeliveryReport": True,
                "tag": tag,
                "messagingConnect": {
                    "apiKey": self._settings.messaging_connect_api_key,
                    "partner": self._settings.messaging_connect_partner,
                },
            },
        }

        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            # The request may have reached ACS before the local transport failed.
            # Existing dispatch logic treats this as ambiguous and never blindly retries.
            raise RuntimeError("acs_transport_outcome_unknown") from exc

        if response.status_code >= 400:
            raise _AcsHttpError(response.status_code)

        try:
            body = response.json()
        except ValueError:
            return SmsSubmissionResult(
                successful=True,
                message_id="",
                http_status_code=response.status_code,
            )
        return self._result_from_payload(body, response.status_code)

    def send(self, *, sender: str, recipient: str, message: str, tag: str) -> SmsSubmissionResult:
        if self._settings.messaging_connect_api_key:
            return self._send_messaging_connect(
                sender=sender,
                recipient=recipient,
                message=message,
                tag=tag,
            )

        if self._client is None:
            raise RuntimeError("acs_sms_client_unavailable")
        results = self._client.send(
            from_=sender,
            to=[recipient],
            message=message,
            enable_delivery_report=True,
            tag=tag,
        )
        if len(results) != 1:
            raise RuntimeError("acs_unexpected_result_count")
        result = results[0]
        return SmsSubmissionResult(
            successful=bool(getattr(result, "successful", False)),
            message_id=str(getattr(result, "message_id", "") or ""),
            http_status_code=int(getattr(result, "http_status_code", 0) or 0),
        )


def parse_work_message(raw: bytes | str) -> dict[str, str | int]:
    if isinstance(raw, bytes):
        if len(raw) > 8192:
            raise ValueError("notification work message exceeds 8 KiB")
        text = raw.decode("utf-8", errors="strict")
    else:
        text = str(raw)
        if len(text.encode("utf-8")) > 8192:
            raise ValueError("notification work message exceeds 8 KiB")
    payload = json.loads(text)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported notification work schema")
    generation = payload.get("outbox_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("outbox_generation must be a positive integer")
    work_type = str(payload.get("work_type") or "")
    if work_type == "dispatch_notification":
        return {
            "schema_version": 1,
            "work_type": work_type,
            "notification_id": validate_identifier("notification_id", str(payload.get("notification_id") or "")),
            "outbox_generation": generation,
        }
    if work_type == "materialize_schedule":
        return {
            "schema_version": 1,
            "work_type": work_type,
            "schedule_id": validate_identifier("schedule_id", str(payload.get("schedule_id") or "")),
            "schedule_version": validate_identifier("schedule_version", str(payload.get("schedule_version") or "")),
            "outbox_generation": generation,
        }
    raise ValueError("unsupported notification work type")


def _classify_status(status: int) -> str:
    if status in {429} or 500 <= status <= 599:
        return "retryable"
    return "permanent"


def _exception_classification(exc: Exception) -> tuple[str, str]:
    status = int(getattr(exc, "status_code", 0) or 0)
    if status == 429:
        return "retryable", "acs_http_429"
    if 500 <= status <= 599:
        return "retryable", f"acs_http_{status}"
    if status in {400, 401, 403, 404}:
        return "permanent", f"acs_http_{status}"
    # A transport timeout may have happened after ACS accepted the request.
    # Never retry an outcome whose external submission state is unknowable.
    return "ambiguous", "acs_transport_outcome_unknown"


def _cancel_if_rollout_policy_revoked(
    *,
    store: NotificationRepository,
    record: Any,
    expected_generation: int,
    worker_id: str,
    expected_state: str,
    expected_policy_version: str = "",
) -> tuple[str | None, SmsRolloutPolicy | None]:
    """Apply current rollout policy to an owned record before any ACS call.

    ``sms_eligible`` is a creation-time audit snapshot.  It cannot authorise a
    later chargeable send after an operator tightens the active case allowlist
    or moves the activation watermark.  The repository transition is scoped to
    this exact owner/generation/state so a concurrent cancellation or dispatch
    cannot be overwritten.
    """
    policy = store.get_rollout_policy()
    if policy is None:
        return "policy_unavailable", None
    eligible, _ = policy.sms_eligibility(record.case_uid, record.created_at)
    version_changed = bool(
        expected_policy_version and policy.version != expected_policy_version
    )
    if eligible and not version_changed:
        return None, policy
    cancelled = store.cancel_dispatch_for_policy(
        record.notification_id,
        expected_generation,
        worker_id,
        expected_state,
        "rollout_policy_revoked",
        utc_iso(),
    )
    return ("policy_revoked" if cancelled else "state_conflict"), policy


def dispatch_notification(
    notification_id: str,
    *,
    expected_generation: int | None = None,
    settings: NotificationSettings | None = None,
    repository: NotificationRepository | None = None,
    provider: Any | None = None,
    owner: str | None = None,
) -> str:
    config = settings or validate_sms_startup()
    store = repository or get_notification_repository(config)
    # The repository is the live charge-control authority. Monotonic versions
    # let a new process publish operator changes while preventing an older,
    # in-flight process from restoring stale App Settings.
    store.upsert_rollout_policy(config.rollout_policy())
    if expected_generation is None:
        current = store.get_notification(notification_id)
        if current is None:
            return "missing"
        expected_generation = int(current.outbox_generation)
    if expected_generation < 1:
        return "stale_generation"
    if not config.sms_enabled:
        # A Function invocation can already be in flight while the trigger is
        # being disabled.  Advance to an unpublished successor generation
        # before the old broker message is auto-completed, so re-enabling SMS
        # cannot strand a row in published/queued state.
        return store.defer_disabled_dispatch(
            notification_id, expected_generation, utc_iso()
        )
    worker_id = owner or f"worker-{uuid.uuid4().hex}"
    now = utc_iso()
    claim = store.claim_dispatch(
        notification_id, expected_generation, worker_id, now, lease_seconds=300
    )
    if claim == "not_due":
        if store.requeue_unclaimed_generation(notification_id, expected_generation, utc_iso()):
            return "deferred"
        return "stale_generation"
    if claim != "claimed":
        return claim
    record = store.get_notification(notification_id)
    if record is None:
        return "missing"
    if not store.revalidate_dispatch(
        notification_id, expected_generation, worker_id, "claimed"
    ):
        return "cancelled"
    policy_outcome, rollout_policy = _cancel_if_rollout_policy_revoked(
        store=store,
        record=record,
        expected_generation=expected_generation,
        worker_id=worker_id,
        expected_state="claimed",
    )
    if policy_outcome is not None:
        return policy_outcome
    if rollout_policy is None:  # defensive; non-null when outcome is None
        return "policy_unavailable"

    try:
        recipient = DemoAllowlistRecipientResolver(config.demo_recipient_e164).resolve(
            target_role=record.target_role,
            target_user_id=record.target_user_id,
        )
        message = sms_template(record.kind)
        assert_one_segment_gsm7(message)
    except Exception as exc:
        store.mark_preflight_failure(
            notification_id, expected_generation, worker_id,
            error_code=f"recipient_or_template_{exc.__class__.__name__.lower()}",
            now=utc_iso(),
        )
        logger.error("SMS preflight failed error=%s", exc.__class__.__name__)
        return "failed_permanent"

    day = utc_now().strftime("%Y-%m-%d")
    reservation = store.reserve_attempt(
        notification_id, expected_generation, worker_id, day,
        rollout_policy.daily_limit,
    )
    if reservation != "reserved":
        if reservation == "cap_blocked":
            logger.error("SMS daily submission cap reached; submission blocked")
        return reservation
    if not store.mark_sending(
        notification_id, expected_generation, worker_id, utc_iso(),
        rollout_policy.version,
    ):
        return "state_conflict"
    if not store.revalidate_dispatch(
        notification_id, expected_generation, worker_id, "sending"
    ):
        store.cancel_sending_before_submit(
            notification_id, expected_generation, worker_id, utc_iso()
        )
        return "cancelled"
    # Construct the transport before the final authorization point. Credential
    # discovery or SDK initialization must not create a window after the live
    # policy read in which an operator revocation can go unnoticed.
    transport = provider or AzureCommunicationSmsProvider(config)
    policy_outcome, _ = _cancel_if_rollout_policy_revoked(
        store=store,
        record=record,
        expected_generation=expected_generation,
        worker_id=worker_id,
        expected_state="sending",
        expected_policy_version=rollout_policy.version,
    )
    if policy_outcome is not None:
        return policy_outcome

    try:
        result = transport.send(
            sender=config.sms_sender,
            recipient=recipient.e164,
            message=message,
            tag=notification_id,
        )
    except Exception as exc:
        classification, code = _exception_classification(exc)
        if classification == "retryable":
            refreshed = store.get_notification(notification_id)
            attempts = int((refreshed or record).attempt_count)
            if attempts < config.retry_max_attempts:
                delay = config.retry_base_seconds * (2 ** max(0, attempts - 1))
                retry_at = utc_iso(utc_now() + timedelta(seconds=delay))
                store.mark_retryable(
                    notification_id, expected_generation, worker_id, error_code=code,
                    retry_at=retry_at, now=utc_iso(),
                )
                return "retryable"
            classification = "permanent"
            code = "retry_limit_reached"
        state = "ambiguous" if classification == "ambiguous" else "failed_permanent"
        store.mark_terminal_failure(
            notification_id, expected_generation, worker_id,
            state=state, error_code=code, now=utc_iso(),
        )
        logger.error("ACS SMS submission failed classification=%s code=%s", classification, code)
        return state

    if result.successful:
        if not result.message_id:
            store.mark_terminal_failure(
                notification_id, expected_generation, worker_id, state="ambiguous",
                error_code="acs_success_without_message_id", now=utc_iso(),
            )
            return "ambiguous"
        try:
            validate_identifier("acs_message_id", result.message_id)
        except ValueError:
            # ACS may already have accepted the chargeable submission. Do not
            # retry merely because the returned identifier cannot be persisted.
            store.mark_terminal_failure(
                notification_id, expected_generation, worker_id, state="ambiguous",
                error_code="acs_invalid_message_id", now=utc_iso(),
            )
            return "ambiguous"
        store.mark_submitted(
            notification_id, expected_generation, worker_id, message_id=result.message_id,
            masked=recipient.masked, now=utc_iso(),
        )
        logger.info("ACS SMS accepted recipient=%s", recipient.masked)
        return "submitted"

    status = result.http_status_code
    classification = _classify_status(status)
    code = f"acs_result_http_{status or 'unknown'}"
    refreshed = store.get_notification(notification_id)
    attempts = int((refreshed or record).attempt_count)
    if classification == "retryable" and attempts < config.retry_max_attempts:
        delay = config.retry_base_seconds * (2 ** max(0, attempts - 1))
        store.mark_retryable(
            notification_id, expected_generation, worker_id, error_code=code,
            retry_at=utc_iso(utc_now() + timedelta(seconds=delay)), now=utc_iso(),
        )
        return "retryable"
    store.mark_terminal_failure(
        notification_id, expected_generation, worker_id, state="failed_permanent",
        error_code="retry_limit_reached" if classification == "retryable" else code,
        now=utc_iso(),
    )
    return "failed_permanent"


def process_work_message(
    raw: bytes | str,
    *,
    settings: NotificationSettings | None = None,
    repository: NotificationRepository | None = None,
    provider: Any | None = None,
) -> str:
    config = settings or validate_sms_startup()
    store = repository or get_notification_repository(config)
    message = parse_work_message(raw)
    if message["work_type"] == "materialize_schedule":
        notification, result = materialize_schedule(
            str(message["schedule_id"]), str(message["schedule_version"]),
            int(message["outbox_generation"]),
            settings=config, repository=store,
        )
        if notification is None:
            return result
        # This schedule trigger directly owns the first dispatch for the created
        # notification. Claim and mark that exact generation before dispatch so
        # the concurrent outbox timer cannot emit a second broker message.
        direct_owner = f"schedule-dispatch-{uuid.uuid4().hex}"
        generation = str(notification.outbox_generation)
        if store.claim_publication(
            "notification", notification.notification_id, generation,
            direct_owner, utc_iso(), 120,
        ):
            store.mark_published(
                "notification", notification.notification_id, generation,
                direct_owner, utc_iso(),
            )
        return dispatch_notification(
            notification.notification_id,
            expected_generation=notification.outbox_generation,
            settings=config, repository=store, provider=provider,
        )
    return dispatch_notification(
        str(message["notification_id"]),
        expected_generation=int(message["outbox_generation"]),
        settings=config, repository=store, provider=provider,
    )


def _delivery_detail_code(data: dict[str, Any], status: str) -> str:
    attempts = data.get("deliveryAttempts") or []
    if not isinstance(attempts, list):
        return "carrier_status_invalid"
    failed = 0
    for attempt in attempts[:20]:
        if isinstance(attempt, dict):
            try:
                failed += max(0, int(attempt.get("segmentsFailed") or 0))
            except (TypeError, ValueError):
                return "carrier_status_invalid"
    return "carrier_delivered" if status == "Delivered" else f"carrier_failed_segments_{failed}"


def process_delivery_report(
    raw: bytes | str,
    *,
    repository: NotificationRepository,
) -> dict[str, int]:
    text = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else str(raw)
    if len(text.encode("utf-8")) > 256 * 1024:
        raise ValueError("delivery report exceeds 256 KiB")
    payload = json.loads(text)
    events = payload if isinstance(payload, list) else [payload]
    accepted = duplicates = ignored = 0
    for event in events:
        if not isinstance(event, dict) or event.get("eventType") != "Microsoft.Communication.SMSDeliveryReportReceived":
            ignored += 1
            continue
        event_id = validate_identifier("event_id", str(event.get("id") or ""))
        data = event.get("data") or {}
        if not isinstance(data, dict):
            ignored += 1
            continue
        message_id = validate_identifier("message_id", str(data.get("messageId") or ""))
        status = str(data.get("deliveryStatus") or "")
        if status not in {"Delivered", "Failed"}:
            ignored += 1
            continue
        observed = canonical_time_key(
            str(data.get("receivedTimestamp") or event.get("eventTime") or utc_iso())
        )
        created = repository.record_delivery(
            event_id=event_id,
            message_id=message_id,
            status=status,
            observed_at=observed,
            detail_code=_delivery_detail_code(data, status),
        )
        accepted += int(created)
        duplicates += int(not created)
    return {"accepted": accepted, "duplicates": duplicates, "ignored": ignored}
