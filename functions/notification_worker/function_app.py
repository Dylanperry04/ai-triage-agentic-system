"""Azure Functions entry points. Message bodies and phone numbers are never logged."""
import logging

import azure.functions as func

from app.notifications.config import validate_sms_startup
from app.notifications.models import utc_iso
from app.notifications.publisher import reconcile_outbox
from app.notifications.repository import get_notification_repository
from app.notifications.worker import process_delivery_report, process_work_message


app = func.FunctionApp()

_SAFE_DISPATCH_OUTCOMES = {
    "submitted", "delivered", "failed_permanent", "ambiguous", "cap_blocked",
    "disabled", "cancelled", "stale_generation", "stale_version",
    "inactive", "missing", "existing", "busy", "not_claimed",
    "retryable", "deferred", "policy_revoked",
}


def _heartbeat(repository, settings, status: str) -> None:
    try:
        repository.record_worker_heartbeat(
            component="notification_worker", build_id=settings.build_id,
            status=status, observed_at=utc_iso(), sms_enabled=settings.sms_enabled,
            sms_publish_enabled=settings.sms_publish_enabled,
        )
    except Exception as exc:
        # Heartbeat loss must be visible but must not replay an already-settled
        # external submission merely to repair observability.
        logging.error("Notification worker heartbeat failed error=%s", exc.__class__.__name__)


@app.function_name(name="sms_dispatch")
@app.service_bus_queue_trigger(
    arg_name="message",
    queue_name="%SmsDispatchQueueName%",
    connection="ServiceBusConnection",
)
def sms_dispatch(message: func.ServiceBusMessage) -> None:
    settings = validate_sms_startup()
    repository = get_notification_repository(settings)
    result = process_work_message(
        message.get_body(), settings=settings, repository=repository
    )
    if result not in _SAFE_DISPATCH_OUTCOMES:
        logging.error("Notification work has no safe settlement result=%s", result)
        raise RuntimeError("notification_work_not_durably_settled")
    _heartbeat(repository, settings, f"dispatch:{result}")
    logging.info("Notification work completed result=%s", result)


@app.function_name(name="sms_delivery_report")
@app.service_bus_queue_trigger(
    arg_name="message",
    queue_name="%SmsDeliveryQueueName%",
    connection="ServiceBusConnection",
)
def sms_delivery_report(message: func.ServiceBusMessage) -> None:
    settings = validate_sms_startup()
    repository = get_notification_repository(settings)
    result = process_delivery_report(message.get_body(), repository=repository)
    _heartbeat(repository, settings, "delivery:processed")
    logging.info(
        "Delivery reports processed accepted=%s duplicates=%s ignored=%s",
        result["accepted"], result["duplicates"], result["ignored"],
    )


@app.function_name(name="notification_outbox_reconciler")
@app.timer_trigger(
    schedule="0 */1 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def notification_outbox_reconciler(timer: func.TimerRequest) -> None:
    del timer
    settings = validate_sms_startup()
    repository = get_notification_repository(settings)
    result = reconcile_outbox(repository, settings, limit=500)
    _heartbeat(repository, settings, "outbox:complete")
    logging.info(
        "Notification outbox reconciled examined=%s published=%s failed=%s recovered_ambiguous=%s recovered_claims=%s delivery_applied=%s",
        result["examined"], result["published"], result["failed"],
        result["recovered_ambiguous"],
        result["recovered_claims"], result["delivery_events_applied"],
    )


@app.function_name(name="notification_retention_cleanup")
@app.timer_trigger(
    schedule="0 17 2 * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def notification_retention_cleanup(timer: func.TimerRequest) -> None:
    del timer
    settings = validate_sms_startup()
    repository = get_notification_repository(settings)
    deleted = repository.purge_expired(utc_iso(), settings.retention_days)
    _heartbeat(repository, settings, "retention:complete")
    logging.info("Notification retention cleanup completed counts=%s", deleted)
