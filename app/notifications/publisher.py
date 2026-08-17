"""Managed-identity Service Bus publisher for the durable notification outbox."""
from __future__ import annotations

from datetime import timedelta, timezone
import json
import logging
import threading
import uuid
from typing import Any

from app.notifications.config import NotificationSettings
from app.notifications.models import parse_utc, utc_iso, utc_now
from app.notifications.repository import NotificationRepository


logger = logging.getLogger("alter.notifications.publisher")


class ServiceBusNotificationPublisher:
    def __init__(self, settings: NotificationSettings):
        if not settings.servicebus_fqdn:
            raise ValueError("SERVICEBUS_FQDN is required to publish SMS work")
        try:
            from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
            from azure.servicebus import ServiceBusClient
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise RuntimeError("Azure Service Bus dependencies are not installed") from exc
        credential = (
            ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
            if settings.managed_identity_client_id
            else DefaultAzureCredential(exclude_interactive_browser_credential=True)
        )
        self._client = ServiceBusClient(settings.servicebus_fqdn, credential=credential)
        self._queue = settings.dispatch_queue
        self._dispatch_min_age_seconds = settings.dispatch_min_age_seconds

    def publish(self, entity_type: str, value: dict[str, Any]) -> None:
        from azure.servicebus import ServiceBusMessage

        if entity_type == "notification":
            entity_id = str(value["notification_id"])
            generation = str(int(value.get("outbox_generation") or 1))
            payload = {
                "schema_version": 1,
                "work_type": "dispatch_notification",
                "notification_id": entity_id,
                "outbox_generation": int(generation),
            }
            message_id = f"dispatch:{entity_id}:{generation}"
            earliest = parse_utc(str(value["created_at"])) + timedelta(
                seconds=self._dispatch_min_age_seconds
            )
            retry = parse_utc(str(value["retry_at"])) if value.get("retry_at") else earliest
            due_value = max(earliest, retry)
            due = due_value if due_value > utc_now() else None
        elif entity_type == "schedule":
            entity_id = str(value["schedule_id"])
            version = str(value["version"])
            generation = f"{version}:{int(value.get('outbox_generation') or 1)}"
            payload = {
                "schema_version": 1,
                "work_type": "materialize_schedule",
                "schedule_id": entity_id,
                "schedule_version": version,
                "outbox_generation": int(value.get("outbox_generation") or 1),
            }
            message_id = f"schedule:{entity_id}:{generation}"
            due = parse_utc(str(value["due_at"]))
        else:
            raise ValueError(f"unsupported outbox entity type: {entity_type}")

        message = ServiceBusMessage(
            json.dumps(payload, separators=(",", ":")),
            message_id=message_id,
            content_type="application/json",
            subject=payload["work_type"],
        )
        with self._client.get_queue_sender(queue_name=self._queue) as sender:
            if due is not None:
                sender.schedule_messages(message, due.astimezone(timezone.utc))
            else:
                sender.send_messages(message)

    def close(self) -> None:
        self._client.close()


_publisher: ServiceBusNotificationPublisher | None = None
_publisher_key: tuple[str, str, str, int] | None = None
_publisher_lock = threading.Lock()


def get_publisher(settings: NotificationSettings) -> ServiceBusNotificationPublisher:
    global _publisher, _publisher_key
    key = (
        settings.servicebus_fqdn, settings.dispatch_queue,
        settings.managed_identity_client_id, settings.dispatch_min_age_seconds,
    )
    with _publisher_lock:
        if _publisher is None or _publisher_key != key:
            _publisher = ServiceBusNotificationPublisher(settings)
            _publisher_key = key
        return _publisher


def reconcile_outbox(
    repository: NotificationRepository,
    settings: NotificationSettings,
    *,
    limit: int = 500,
) -> dict[str, int]:
    """Publish committed-but-unpublished work; safe to rerun after any crash."""
    if not settings.sms_publish_enabled:
        return {
            "examined": 0, "published": 0, "failed": 0,
            "recovered_ambiguous": 0, "recovered_claims": 0,
            "delivery_events_applied": 0, "correlations_repaired": 0,
        }
    now = utc_iso()
    recovered_claims = repository.recover_expired_claims(now)
    recovered = repository.recover_stale_sending(now)
    delivery_repairs = repository.repair_delivery_state(limit=limit)
    publisher = get_publisher(settings)
    examined = published = failed = 0
    reconciler_id = f"publisher-{uuid.uuid4().hex}"
    for entity_type, value in repository.list_unpublished(limit=limit):
        examined += 1
        entity_id = str(
            value["notification_id"] if entity_type == "notification" else value["schedule_id"]
        )
        generation = (
            str(int(value.get("outbox_generation") or 1))
            if entity_type == "notification"
            else f"{value.get('version')}:{int(value.get('outbox_generation') or 1)}"
        )
        if not repository.claim_publication(
            entity_type, entity_id, generation, reconciler_id, utc_iso(), 120
        ):
            continue
        try:
            publisher.publish(entity_type, value)
            if repository.mark_published(
                entity_type, entity_id, generation, reconciler_id, utc_iso()
            ):
                published += 1
        except Exception as exc:
            repository.release_publication(
                entity_type, entity_id, generation, reconciler_id
            )
            failed += 1
            # No phone, message body, case identifier, or queue payload is logged.
            logger.error(
                "notification outbox publication failed type=%s error=%s",
                entity_type,
                exc.__class__.__name__,
            )
    return {
        "examined": examined,
        "published": published,
        "failed": failed,
        "recovered_ambiguous": recovered,
        "recovered_claims": recovered_claims,
        "delivery_events_applied": int(delivery_repairs["delivery_events_applied"]),
        "correlations_repaired": int(delivery_repairs["correlations_repaired"]),
    }


def reset_publisher_for_tests() -> None:
    global _publisher, _publisher_key
    with _publisher_lock:
        if _publisher is not None:
            try:
                _publisher.close()
            except Exception:
                pass
        _publisher = None
        _publisher_key = None
