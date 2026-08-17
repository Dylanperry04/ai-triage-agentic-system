"""RBAC-protected durable notification API used by the existing bell UI."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth_dependencies import requires
from app.notifications.config import NotificationSettings
from app.notifications.models import parse_utc, stable_id, utc_iso, utc_now
from app.notifications.repository import get_notification_repository
from app.security import authz
from app.security.identity import AuthContext


router = APIRouter(prefix="/notifications", tags=["notifications"])


def _reader_id(ctx: AuthContext) -> str:
    source = str(getattr(ctx, "source", "") or "unknown")
    user = str(getattr(ctx, "user_id", "") or "anonymous")
    return stable_id("reader-v1", source, user)


def _require_notification_access(record: Any, ctx: AuthContext) -> str:
    """Enforce the same role/person boundary for every notification path."""
    roles = set(getattr(ctx, "roles", []) or [])
    if record.target_role not in roles:
        raise HTTPException(
            status_code=403,
            detail="notification is assigned to a different role",
        )
    reader_id = _reader_id(ctx)
    if record.target_user_id and record.target_user_id != reader_id:
        raise HTTPException(
            status_code=403,
            detail="notification is assigned to a different user",
        )
    return reader_id


def _display_labels(case_uid: str) -> dict[str, str]:
    try:
        from app.api import case_resolver, safe_dto

        resolved = case_resolver.resolve(case_uid)
        return safe_dto.safe_display_identity(resolved.case) if resolved else {}
    except Exception:
        return {}


def _event_time_ms(value: Any) -> str:
    try:
        return parse_utc(str(value)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (TypeError, ValueError):
        return ""


@router.get("")
def list_notifications(
    request: Request,
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=50000),
    ctx: AuthContext = Depends(requires(authz.PERM_VIEW_WORKFLOW_QUEUE, "list_notifications")),
) -> dict[str, Any]:
    settings = NotificationSettings.from_env()
    repository = get_notification_repository(settings)
    rows = repository.list_notifications(
        roles=getattr(ctx, "roles", []) or [],
        user_id=_reader_id(ctx),
        limit=limit,
        offset=offset,
    )
    total = repository.count_notifications(
        roles=getattr(ctx, "roles", []) or [], user_id=_reader_id(ctx)
    )
    output = []
    for row in rows:
        labels = _display_labels(str(row.get("case_uid") or ""))
        label = labels.get("display_identifier") or labels.get("encounter_display_label") or "Case"
        body = str(row.get("body") or "")
        output.append({
            "notification_id": row.get("notification_id"),
            "kind": "recheck" if row.get("kind") == "overdue_vitals" else row.get("kind"),
            "case_uid": row.get("case_uid"),
            "case_label": label,
            "title": row.get("title"),
            "body": f"{label} — {body}",
            "created_at": row.get("created_at"),
            "event_key": row.get("event_key"),
            "event_time_ms": _event_time_ms(row.get("event_key")),
            "read": bool(row.get("is_read")),
        })
    reconciliation = getattr(
        request.app.state, "notification_backfill_status", {"state": "unknown", "complete": False}
    )
    complete = bool(reconciliation.get("complete"))
    return {
        "notifications": output, "count": len(output), "total_active": total,
        "offset": offset, "has_more": offset + len(output) < total,
        "next_offset": offset + len(output) if offset + len(output) < total else None,
        "source": "durable_notification_store", "complete": complete,
        "degraded": not complete, "reconciliation_state": reconciliation.get("state", "unknown"),
    }


@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    ctx: AuthContext = Depends(requires(authz.PERM_VIEW_WORKFLOW_QUEUE, "mark_notification_read")),
) -> dict[str, Any]:
    settings = NotificationSettings.from_env()
    repository = get_notification_repository(settings)
    record = repository.get_notification(notification_id)
    if record is None:
        raise HTTPException(status_code=404, detail="notification not found")
    reader_id = _require_notification_access(record, ctx)
    if not repository.mark_read(notification_id, reader_id, utc_iso()):
        raise HTTPException(status_code=404, detail="notification not found")
    return {"notification_id": notification_id, "status": "read"}


@router.post("/{notification_id}/acknowledge")
def acknowledge_notification(
    notification_id: str,
    ctx: AuthContext = Depends(requires(authz.PERM_SUBMIT_REVIEW, "acknowledge_notification")),
) -> dict[str, Any]:
    settings = NotificationSettings.from_env()
    repository = get_notification_repository(settings)
    record = repository.get_notification(notification_id)
    if record is None:
        raise HTTPException(status_code=404, detail="notification not found")
    reader_id = _require_notification_access(record, ctx)
    if record.kind != "overdue_vitals":
        raise HTTPException(status_code=409, detail="only overdue-vitals notifications are acknowledged here")
    # One backend command owns both durable surfaces. The workflow update is
    # idempotent for this exact event reference; if the notification write then
    # fails, retrying this command converges without acknowledging a newer clock.
    from app.api.case_routes import acknowledge_overdue_vitals_event

    workflow = acknowledge_overdue_vitals_event(
        record.case_uid, expected_reference=record.event_key,
        durable_target_role=record.target_role, ctx=ctx,
    )
    try:
        acknowledged = repository.acknowledge_notification(
            notification_id, reader_id, utc_iso()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "workflow acknowledgement was recorded but notification convergence is pending; "
                "retry this acknowledgement"
            ),
        ) from exc
    if not acknowledged:
        raise HTTPException(status_code=503, detail="notification convergence is pending; retry")
    return {
        "notification_id": notification_id, "status": "acknowledged",
        "workflow_status": workflow.get("status"),
        "idempotent": bool(workflow.get("idempotent")),
    }


@router.get("/system/health")
def notification_health(
    request: Request,
    ctx: AuthContext = Depends(requires(authz.PERM_VIEW_SECURITY_STATUS, "notification_health")),
) -> dict[str, Any]:
    del ctx
    settings = NotificationSettings.from_env()
    health = get_notification_repository(settings).health()
    rollout_policy = health.get("rollout_policy") or {}
    rollout_policy_match = bool(rollout_policy) and (
        str(rollout_policy.get("version") or "")
        == settings.sms_rollout_policy_version
    )
    worker = health.get("worker") or {}
    worker_observed = str(worker.get("observed_at") or "")
    worker_stale = True
    if worker_observed:
        try:
            worker_stale = (
                utc_now() - parse_utc(worker_observed)
            ).total_seconds() > settings.worker_stale_seconds
        except (TypeError, ValueError):
            worker_stale = True
    worker_required = settings.sms_publish_enabled or settings.sms_enabled
    build_match = bool(worker) and str(worker.get("build_id") or "") == settings.build_id
    worker_healthy = bool(worker) and not worker_stale and build_match
    backfill = getattr(request.app.state, "notification_backfill_status", {"state": "unknown"})
    return {
        **health,
        # Web publication and Function submission are deliberately separate.
        "sms_enabled": settings.sms_enabled,
        "sms_publish_enabled": settings.sms_publish_enabled,
        "publication": {
            "enabled": settings.sms_publish_enabled,
            "state": "enabled" if settings.sms_publish_enabled else "disabled",
        },
        "submission_worker": {
            "required": worker_required,
            "healthy": worker_healthy if worker_required else True,
            "state": (
                "not_required" if not worker_required else
                "missing" if not worker else
                "stale" if worker_stale else
                "build_mismatch" if not build_match else
                str(worker.get("status") or "unknown")
            ),
            "enabled": bool(worker.get("sms_enabled")),
            "publish_enabled": bool(worker.get("sms_publish_enabled")),
            "build_id": str(worker.get("build_id") or ""),
            "web_build_id": settings.build_id,
            "build_match": build_match,
            "last_heartbeat_at": worker_observed,
        },
        "recipient_mode": settings.sms_recipient_mode,
        "daily_limit": settings.daily_limit,
        "rollout_policy": {
            "configured_version": settings.sms_rollout_policy_version,
            "active_version": str(rollout_policy.get("version") or ""),
            "version_match": rollout_policy_match,
            "publish_enabled": bool(rollout_policy.get("publish_enabled")),
            "daily_limit": int(rollout_policy.get("daily_limit") or 0),
            "updated_at": str(rollout_policy.get("updated_at") or ""),
        },
        "retention_days": settings.retention_days,
        "sender_configured": settings.sms_sender == "ALTER",
        "recipient_configured": bool(settings.demo_recipient_e164),
        "backfill": backfill,
        "outbox": getattr(request.app.state, "notification_outbox_status", {"state": "disabled"}),
        "degraded": bool(
            not health.get("available")
            or not rollout_policy_match
            or not bool(backfill.get("complete"))
            or (worker_required and not worker_healthy)
        ),
        "dlq_status": "not_collected",
    }
