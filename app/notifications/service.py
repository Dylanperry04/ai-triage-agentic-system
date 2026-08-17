"""Application service binding workflow-state events to durable notifications."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from app.notifications.config import NotificationSettings
from app.notifications.models import (
    NotificationRecord,
    ScheduleRecord,
    canonical_time_key,
    parse_utc,
    stable_id,
    utc_iso,
)
from app.notifications.publisher import reconcile_outbox
from app.notifications.repository import NotificationRepository, get_notification_repository


logger = logging.getLogger("alter.notifications")
OVERDUE_VITALS_MINUTES = 210


def _parse_optional(value: Any) -> datetime | None:
    try:
        return parse_utc(str(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _case_closed(state: dict[str, Any]) -> bool:
    status = str(state.get("case_status") or "").strip().lower()
    return bool(state.get("notifications_suppressed")) or status in {
        "discharged", "case_closed", "closed", "transferred", "completed",
    }


def _target_role(state: dict[str, Any], *, escalation: bool = False) -> str:
    if escalation:
        value = state.get("escalation_target_role")
    else:
        value = state.get("notification_target_role") or state.get("assigned_staff_role")
    role = str(value or "").strip()
    if role in {"triage_nurse", "ed_doctor", "clinical_supervisor", "security_admin"}:
        return role
    return "clinical_supervisor" if escalation else "triage_nurse"


def _vitals_reference(state: dict[str, Any], case: dict[str, Any] | None) -> datetime | None:
    edstay = (case or {}).get("edstay") or {}
    audit = (case or {}).get("audit_metadata") or {}
    candidates = [
        state.get("last_vitals_updated_at"), state.get("last_vitals_checked_at"),
        edstay.get("last_vitals_updated_at"), edstay.get("last_vitals_checked_at"),
        edstay.get("intime"), edstay.get("arrival_time_utc"), edstay.get("arrival_time"),
        audit.get("submitted_at_utc"), audit.get("submitted_at"),
    ]
    parsed = [value for value in (_parse_optional(item) for item in candidates) if value is not None]
    return max(parsed) if parsed else None


def create_notification_for_event(
    *,
    repository: NotificationRepository,
    settings: NotificationSettings,
    kind: str,
    case_uid: str,
    event_key: str,
    target_role: str,
    created_at: str,
    initial_active: bool = True,
) -> tuple[NotificationRecord, bool]:
    titles = {
        "overdue_vitals": "Vitals recheck due",
        "escalation": "Escalation awaiting review",
        "clinical_alert": "Clinical alert",
    }
    bodies = {
        "overdue_vitals": "Observations have not been repeated within the recheck window. Open the case to acknowledge.",
        "escalation": "This case was escalated and needs a senior decision.",
        "clinical_alert": "A clinical alert needs review.",
    }
    event = canonical_time_key(event_key)
    created = canonical_time_key(created_at)
    eligible, ineligible_reason = settings.sms_eligibility(case_uid, created)
    record = NotificationRecord.create(
        kind=kind,
        case_uid=case_uid,
        event_key=event,
        target_role=target_role,
        title=titles[kind],
        body=bodies[kind],
        created_at=created,
        sms_enabled=eligible,
        sms_eligible=eligible,
        sms_ineligible_reason=ineligible_reason,
        active=initial_active,
        retention_days=settings.retention_days,
    )
    return repository.create_notification(record)


def sync_workflow_state(
    state: dict[str, Any],
    *,
    case: dict[str, Any] | None = None,
    settings: NotificationSettings | None = None,
    repository: NotificationRepository | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """Idempotently reconcile one persisted workflow state into the outbox."""
    config = settings or NotificationSettings.from_env()
    store = repository or get_notification_repository(config)
    case_uid = str(state.get("case_uid") or "").strip()
    if not case_uid:
        raise ValueError("workflow state cannot be reconciled without case_uid")
    results = {"notifications_created": 0, "schedules_changed": 0, "published": 0, "failed": 0}
    schedule_id = stable_id("sch-v1", "overdue_vitals", case_uid)

    if _case_closed(state):
        store.cancel_schedule(schedule_id)
        store.deactivate_notifications(
            case_uid, None, utc_iso(), cancel_reason="case_closed"
        )
        # Terminal and explicitly suppressed snapshots are authoritative.  A
        # partially updated/external snapshot can still carry stale overdue or
        # escalation flags; those must never recreate work after deactivation.
        if publish and config.sms_publish_enabled:
            publication = reconcile_outbox(store, config, limit=100)
            results["published"] = publication["published"]
            results["failed"] = publication["failed"]
        return results

    if config.sms_publish_enabled:
        reference = _vitals_reference(state, case)
        if reference is not None:
            reference_key = canonical_time_key(reference)
            existing_id = stable_id(
                "ntf-v1", "overdue_vitals", case_uid, reference_key
            )
            if store.get_notification(existing_id) is None:
                due_at = reference + timedelta(minutes=OVERDUE_VITALS_MINUTES)
                eligible, ineligible_reason = config.sms_eligibility(case_uid, due_at)
                schedule = ScheduleRecord.create(
                    case_uid=case_uid,
                    reference_at=reference_key,
                    due_minutes=OVERDUE_VITALS_MINUTES,
                    target_role=_target_role(state),
                    sms_eligible=eligible,
                    sms_ineligible_reason=ineligible_reason,
                )
                _, changed = store.upsert_schedule(schedule)
                results["schedules_changed"] += int(changed)
            else:
                # This clock already produced its immutable notification. Never
                # reactivate the one-shot schedule on unrelated state updates.
                store.cancel_schedule(schedule_id)

    if state.get("overdue_vitals_alert_active"):
        reference_key = str(
            state.get("overdue_vitals_reference_at")
            or state.get("last_vitals_updated_at")
            or state.get("last_vitals_checked_at")
            or ""
        ).strip()
        if reference_key:
            reference_key = canonical_time_key(reference_key)
            _, created = create_notification_for_event(
                repository=store,
                settings=config,
                kind="overdue_vitals",
                case_uid=case_uid,
                event_key=reference_key,
                target_role=_target_role(state),
                created_at=str(state.get("overdue_vitals_alert_created_at") or utc_iso()),
            )
            results["notifications_created"] += int(created)
    else:
        acknowledged_reference = str(
            state.get("overdue_vitals_acknowledged_reference_at") or ""
        ).strip()
        reference = _vitals_reference(state, case)
        if acknowledged_reference:
            try:
                acknowledged_reference = canonical_time_key(acknowledged_reference)
            except (TypeError, ValueError):
                acknowledged_reference = ""
        current_reference = canonical_time_key(reference) if reference is not None else ""
        if acknowledged_reference and acknowledged_reference == current_reference:
            # Acknowledgement hides the in-app item but deliberately leaves SMS
            # state untouched; queued work is still required by policy.
            store.deactivate_notifications(
                case_uid, "overdue_vitals", utc_iso(), cancel_sms=False
            )
        elif current_reference:
            # Preserve a Function-materialised alert for the current vitals
            # clock while retiring any alerts made obsolete by new readings.
            store.deactivate_notifications_except_event(
                case_uid, "overdue_vitals", current_reference, utc_iso()
            )

    escalation_status = str(state.get("escalation_status") or "").strip().lower()
    requested_at = str(state.get("escalation_requested_at") or state.get("escalation_timestamp") or "").strip()
    if escalation_status in {"requested", "pending"} and requested_at:
        requested_at = canonical_time_key(requested_at)
        _, created = create_notification_for_event(
            repository=store,
            settings=config,
            kind="escalation",
            case_uid=case_uid,
            event_key=requested_at,
            target_role=_target_role(state, escalation=True),
            created_at=requested_at,
        )
        results["notifications_created"] += int(created)
    else:
        store.deactivate_notifications(
            case_uid, "escalation", utc_iso(), cancel_reason="escalation_resolved"
        )

    if publish and config.sms_publish_enabled:
        publication = reconcile_outbox(store, config, limit=100)
        results["published"] = publication["published"]
        results["failed"] = publication["failed"]
    return results


def materialize_schedule(
    schedule_id: str,
    version: str,
    outbox_generation: int,
    *,
    settings: NotificationSettings,
    repository: NotificationRepository,
) -> tuple[NotificationRecord | None, str]:
    schedule = repository.get_schedule(schedule_id)
    if schedule is None:
        return None, "missing"
    if schedule.version != version:
        return None, "stale_version"
    if int(schedule.outbox_generation) != int(outbox_generation):
        return None, "stale_generation"
    if not schedule.active:
        if schedule.materialized_notification_id:
            existing = repository.activate_notification(
                schedule.materialized_notification_id, now=utc_iso()
            )
            return existing, "existing" if existing else "inactive"
        return None, "inactive"
    if parse_utc(schedule.due_at) > datetime.now(timezone.utc):
        repository.requeue_schedule_generation(
            schedule_id, version, outbox_generation, utc_iso()
        )
        return None, "deferred"
    record, created = create_notification_for_event(
        repository=repository,
        settings=settings,
        kind="overdue_vitals",
        case_uid=schedule.case_uid,
        event_key=schedule.reference_at,
        target_role=schedule.target_role,
        created_at=schedule.due_at,
        initial_active=False,
    )
    consumed = repository.consume_schedule_if_version(
        schedule.schedule_id, version, outbox_generation,
        record.notification_id, utc_iso(),
    )
    if not consumed:
        # A duplicate delivery of the same schedule generation can lose the
        # consume CAS after another worker records this deterministic ID.  In
        # that case the shared notification is the winner, not stale cleanup.
        current_schedule = repository.get_schedule(schedule_id)
        if (
            current_schedule is not None
            and current_schedule.version == version
            and int(current_schedule.outbox_generation) == int(outbox_generation)
            and current_schedule.materialized_notification_id == record.notification_id
        ):
            winner = repository.activate_notification(
                record.notification_id, now=utc_iso()
            )
            if winner is None:
                raise RuntimeError(
                    "materialized schedule winner notification is missing"
                )
            return winner, "existing"
        if created:
            repository.cancel_notification(
                record.notification_id, reason="stale_schedule_generation", now=utc_iso()
            )
        return None, "stale_generation"
    activated = repository.activate_notification(record.notification_id, now=utc_iso())
    if activated is None:
        raise RuntimeError("consumed schedule could not activate its notification")
    return activated, "created" if created else "existing"


def reconcile_current_workflow_states(*, limit: int = 50000) -> dict[str, int]:
    """One-time/repeatable backfill from the current workflow-state surface.

    This makes a rolling deployment safe: alerts that were already visible in
    the legacy worklist become durable records, while deterministic IDs ensure
    that startup, polling, and reconciliation cannot create a second record.
    """
    from app.api import case_resolver
    from app.config import settings as app_settings
    from app.storage.case_state_repository import read_case_states

    config = NotificationSettings.from_env()
    repository = get_notification_repository(config)
    states = read_case_states(
        app_settings.processed_dir / "case_workflow_state.jsonl",
        limit=max(1, min(int(limit), 50000)),
    )
    latest: dict[str, dict[str, Any]] = {}
    for state in states:
        case_uid = str(state.get("case_uid") or "").strip()
        if case_uid:
            latest[case_uid] = state
    created = changed = failures = 0
    for case_uid, state in latest.items():
        try:
            resolved = case_resolver.resolve(case_uid)
            result = sync_workflow_state(
                state,
                case=(resolved.case if resolved else None),
                settings=config,
                repository=repository,
                publish=False,
            )
            created += int(result["notifications_created"])
            changed += int(result["schedules_changed"])
        except Exception as exc:
            failures += 1
            logger.error("workflow notification backfill failed error=%s", exc.__class__.__name__)
    publication = reconcile_outbox(repository, config, limit=1000) if config.sms_publish_enabled else {"published": 0, "failed": 0}
    return {
        "states": len(latest), "notifications_created": created,
        "schedules_changed": changed, "failures": failures,
        "published": int(publication["published"]),
        "publication_failures": int(publication["failed"]),
    }
