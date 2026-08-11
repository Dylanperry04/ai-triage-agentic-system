"""
Canonical case_uid-keyed API (the final external surface).

All external identifiers are the PSEUDONYMOUS case_uid. Raw stay_id never appears
in a path or a response. Every protected route enforces requires(permission),
returns 401 unauthenticated / 403 unauthorised, audits the decision, and fails
closed in patient-data mode (via the auth dependency + the resolver + guarded
writes).

Routes:
  GET  /cases                          (list, RBAC: view_case)
  GET  /cases/{case_uid}               (one case, RBAC: view_case)
  POST /cases/{case_uid}/assessments   (run ML+rules workflow, RBAC: run_assessment)
  POST /cases/{case_uid}/explanations  (LLM explanation, RBAC: ask_chatbot)
  POST /cases/{case_uid}/multiagent-explanations (AutoGen explanation, RBAC: ask_chatbot)
  POST /cases/{case_uid}/reviews       (clinician review, RBAC: submit_review)
  POST /cases/{case_uid}/followups     (rerun/compare, RBAC: run_assessment)
  POST /cases/{case_uid}/followups/explanations (LLM explanation of reassessment)
  POST /cases/{case_uid}/followups/multiagent-explanations (explain reassessment)
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from app.api.auth_dependencies import requires, get_auth_context
from app.api import case_resolver
from app.security import authz
from app.security.identity import AuthContext
from app.schemas.internal import EDTriageCase
from app.agents.orchestrator import run_workflow

router = APIRouter()


def _sensitive_audit_mode() -> bool:
    import os
    return (
        os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
        or os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
    )


def _patient_data_mode() -> bool:
    import os
    return os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"


def _resolve_or_404(case_uid: str) -> case_resolver.ResolvedCase:
    rc = case_resolver.resolve(case_uid)
    if rc is None:
        raise HTTPException(status_code=404, detail=f"case_uid not found: {case_uid}")
    return rc


def _case_state_path():
    from app.config import settings
    return settings.processed_dir / "case_workflow_state.jsonl"


def _latest_workflow_state(case_uid: str) -> Dict[str, Any]:
    try:
        from app.storage.case_state_repository import latest_case_state
        return latest_case_state(_case_state_path(), case_uid)
    except Exception as exc:
        if _patient_data_mode():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Workflow state could not be read from the durable state/audit "
                    "sink in patient-data mode."
                ),
            ) from exc
        return {}


def _append_workflow_state(state: Dict[str, Any]) -> Dict[str, Any]:
    from app.storage.case_state_repository import append_case_state
    append_case_state(_case_state_path(), state)
    return state


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _primary_role(ctx: AuthContext) -> Optional[str]:
    roles = list(getattr(ctx, "roles", []) or [])
    return roles[0] if roles else None


def _actor_identity(ctx: AuthContext) -> Dict[str, Any]:
    """Who performed this action, for per-person attribution.

    The authenticated principal already carries a stable user_id (real SSO
    principal, or ``demo-<slug>`` for a demo persona) — it was simply never
    written onto the decision, so analytics could only ever attribute work to a
    ROLE. Recording it here makes the "who did this" drill-down answerable.

    Honesty about provenance matters more than the attribution itself:
    ``actor_identity_source`` records whether the identity came from real auth
    or from the demo role-switcher, and ``actor_identity_verified`` is False for
    the demo path because the role there is asserted by a client-supplied
    X-Demo-Role header, not proven. A UI must not present an unverified actor
    as if it were an authenticated one.
    """
    user_id = str(getattr(ctx, "user_id", "") or "").strip()
    display = str(getattr(ctx, "display_name", "") or "").strip()
    source = str(getattr(ctx, "source", "") or "").strip() or "unknown"
    verified = bool(getattr(ctx, "authenticated", False)) and not bool(
        getattr(ctx, "is_demo_stub", False)
    )
    out: Dict[str, Any] = {}
    if user_id:
        out["actor_user_id"] = user_id[:128]
        out["actor_display_name"] = (display or user_id)[:128]
        out["actor_identity_source"] = source[:64]
        out["actor_identity_verified"] = verified
    return out


_ESCALATION_REQUEST_STATUSES = {"ESCALATION_REQUIRED", "OVERRIDE_REQUIRED"}
_ESCALATION_CONFIRM_STATUSES = {"ESCALATION_CONFIRMED"}
_ESCALATION_CLOSE_STATUSES = {
    "ESCALATION_REJECTED",
    "ESCALATION_CLOSED",
    "ESCALATION_RESOLVED",
}
_CASE_CLOSE_STATUSES = {"DISCHARGED", "CASE_CLOSED"}
_ACTIVE_ESCALATION_STATES = {"requested", "confirmed"}
_REQUESTED_ESCALATION_STATES = {"requested", "pending"}
_TERMINAL_ESCALATION_STATES = {"rejected", "closed", "resolved"}
_OVERDUE_VITALS_MINUTES = 210
_ESCALATION_RESOLUTION_ROLES = {"ed_doctor", "clinical_supervisor", "security_admin"}
_CASE_CLOSE_ROLES = {"ed_doctor", "clinical_supervisor", "security_admin"}
_ESCALATION_TARGET_ROLES = {"ed_doctor", "clinical_supervisor"}


def _is_case_closed(state: Dict[str, Any]) -> bool:
    status = str(state.get("case_status") or state.get("review_status") or "").lower()
    return status in {"discharged", "closed", "case_closed"} or bool(state.get("discharged_at"))


def _active_escalation_exists(state: Dict[str, Any]) -> bool:
    if not state:
        return False
    if _is_case_closed(state):
        return False
    esc_state = str(state.get("escalation_state") or "").lower()
    esc_status = str(state.get("escalation_status") or "").lower()
    return esc_state in _ACTIVE_ESCALATION_STATES or esc_status in _ACTIVE_ESCALATION_STATES


def _requested_escalation_exists(state: Dict[str, Any]) -> bool:
    if not state or _is_case_closed(state):
        return False
    esc_state = str(state.get("escalation_state") or "").lower()
    esc_status = str(state.get("escalation_status") or "").lower()
    return esc_state in _REQUESTED_ESCALATION_STATES or esc_status in _REQUESTED_ESCALATION_STATES


def _escalation_terminal_state(state: Dict[str, Any]) -> bool:
    esc_state = str(state.get("escalation_state") or "").lower()
    esc_status = str(state.get("escalation_status") or "").lower()
    return esc_state in _TERMINAL_ESCALATION_STATES or esc_status in _TERMINAL_ESCALATION_STATES


def _escalation_confirmed_state(state: Dict[str, Any]) -> bool:
    esc_state = str(state.get("escalation_state") or "").lower()
    esc_status = str(state.get("escalation_status") or "").lower()
    return esc_state == "confirmed" or esc_status == "confirmed"


def _ctx_role_set(ctx: AuthContext) -> set[str]:
    return {str(role) for role in (getattr(ctx, "roles", []) or [])}


def _require_review_action_authorised(status: str, ctx: AuthContext) -> None:
    roles = _ctx_role_set(ctx)
    if status in (_ESCALATION_CONFIRM_STATUSES | _ESCALATION_CLOSE_STATUSES):
        if roles.isdisjoint(_ESCALATION_RESOLUTION_ROLES):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Escalation confirmation/resolution requires an ED doctor "
                    "or clinical supervisor role."
                ),
            )
    if status in _CASE_CLOSE_STATUSES:
        if roles.isdisjoint(_CASE_CLOSE_ROLES):
            raise HTTPException(
                status_code=403,
                detail="Discharge/close-case requires an ED doctor or clinical supervisor role.",
            )


def _merge_workflow_state(
    previous: Dict[str, Any],
    update: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge workflow updates without accidentally clearing active state.

    Workflow state is append-only on disk, so every write stores the current
    snapshot. Reassessment should not de-escalate a previously requested
    escalation unless an explicit escalation resolution/closure action was
    submitted by an authorised reviewer.
    """
    merged = dict(previous or {})
    history = list(merged.get("escalation_history") or [])
    terminal_review = bool(update.pop("_terminal_review_decision", False))
    supersede_acceptance = bool(update.pop("_supersede_prior_acceptance", False))
    # A terminal review decision also resolves any active escalation.
    resolve_escalation = bool(update.pop("_resolve_active_escalation", False)) or (
        terminal_review and _active_escalation_exists(merged)
    )
    close_case = bool(update.get("_close_case", False))
    update.pop("_close_case", None)

    if resolve_escalation and _active_escalation_exists(merged):
        history.append({
            "previous_state": merged.get("escalation_state"),
            "previous_status": merged.get("escalation_status"),
            "target_role": merged.get("escalation_target_role"),
            "reason": merged.get("escalation_reason"),
            "requested_at": merged.get("escalation_requested_at")
            or merged.get("escalation_timestamp"),
            "closed_at": update.get("escalation_closed_at")
            or update.get("discharged_at")
            or update.get("updated_at_utc"),
            "closed_by_role": update.get("reviewer_role"),
            "resolution_status": update.get("escalation_status"),
            "resolution_note": update.get("escalation_resolution_note")
            or update.get("escalation_reason"),
        })

    merged.update(update)
    if history:
        merged["escalation_history"] = history

    if resolve_escalation:
        merged["escalation_required"] = False
        merged["escalation_state"] = str(
            update.get("escalation_status") or "closed"
        ).lower()
    elif _active_escalation_exists(previous) and "escalation_state" not in update:
        for key in (
            "escalation_required",
            "escalation_state",
            "escalation_status",
            "escalation_target_role",
            "escalation_timestamp",
            "escalation_requested_at",
            "escalation_reason",
            "escalation_evidence",
        ):
            if key in previous and key not in merged:
                merged[key] = previous[key]
        merged["escalation_required"] = True

    if supersede_acceptance and previous.get("case_level_clinician_acceptance"):
        # A later escalation makes a prior acceptance non-authoritative. Record
        # the supersession explicitly rather than leaving a contradictory
        # "accepted + escalation_requested" pair with no explanation.
        merged["case_level_clinician_acceptance"] = False
        merged["prior_acceptance_superseded_by_escalation"] = True
        merged["prior_acceptance_superseded_at"] = update.get("updated_at_utc")
        merged["prior_acceptance_by_role"] = previous.get("accepted_by_role")

    if terminal_review:
        # Clear a stale "request more information" so an accepted/overridden
        # case does not carry a dangling info request, and make sure the
        # escalation reads as resolved rather than still-requested.
        for stale in ("requested_fields", "requesting_role", "request_timestamp"):
            merged.pop(stale, None)
        if _active_escalation_exists(previous):
            merged["escalation_required"] = False
            merged["escalation_state"] = "resolved_by_review"
            merged["escalation_status"] = "resolved"
            merged["escalation_resolved_by_terminal_review"] = True

    if close_case:
        # Preserve the disposition chosen by the state builder: CASE_CLOSED
        # (admitted) and DISCHARGED are both terminal but must stay distinct.
        closed_status = str(update.get("case_status") or "discharged")
        merged.update({
            "case_status": closed_status,
            "review_status": str(update.get("review_status") or closed_status),
            "escalation_required": False,
            "escalation_state": "closed",
            "escalation_status": "closed",
            "overdue_vitals_alert_active": False,
            "notifications_suppressed": True,
        })
    return merged


def _case_dict_with_workflow_updates(
    case: Dict[str, Any],
    workflow_state: Dict[str, Any] | None,
) -> Dict[str, Any]:
    case_dict = dict(case or {})
    updates = dict((workflow_state or {}).get("latest_triage_updates") or {})
    if not updates:
        return case_dict
    triage = dict(case_dict.get("triage") or {})
    for key in set(_ALLOWED_FOLLOWUP_VITALS) | {"chiefcomplaint"}:
        if key in updates and updates[key] not in (None, ""):
            triage[key] = updates[key]
    case_dict["triage"] = triage
    return case_dict


def _last_vitals_reference_dt(
    rc: case_resolver.ResolvedCase,
    workflow_state: Dict[str, Any],
) -> datetime | None:
    edstay = rc.case.get("edstay") or {}
    audit_meta = rc.case.get("audit_metadata") or {}
    candidates = [
        workflow_state.get("last_vitals_updated_at"),
        workflow_state.get("last_vitals_checked_at"),
        edstay.get("last_vitals_updated_at"),
        edstay.get("last_vitals_checked_at"),
        edstay.get("intime"),
        edstay.get("arrival_time_utc"),
        edstay.get("arrival_time"),
        audit_meta.get("submitted_at_utc"),
        audit_meta.get("submitted_at"),
    ]
    parsed = [dt for dt in (_parse_utc_dt(value) for value in candidates) if dt is not None]
    return max(parsed) if parsed else None


def _vitals_reference_changed_since_ack(
    workflow_state: Dict[str, Any],
    clock_dt: datetime,
) -> bool:
    ack_dt = _parse_utc_dt(workflow_state.get("overdue_vitals_acknowledged_at"))
    if ack_dt is None:
        return True
    for key in ("last_vitals_updated_at", "last_vitals_checked_at"):
        dt = _parse_utc_dt(workflow_state.get(key))
        if dt is not None and dt > ack_dt:
            return True
    ack_ref = _parse_utc_dt(
        workflow_state.get("overdue_vitals_acknowledged_reference_at")
        or workflow_state.get("overdue_vitals_reference_at")
    )
    if ack_ref is None:
        return False
    return abs((clock_dt - ack_ref).total_seconds()) > 1.0


def _notification_target_role(
    ctx: AuthContext,
    workflow_state: Dict[str, Any],
) -> str:
    target = str(
        workflow_state.get("assigned_staff_role")
        or workflow_state.get("escalation_target_role")
        or ""
    ).strip()
    if target in _ESCALATION_TARGET_ROLES or target == "triage_nurse":
        return target
    role = _primary_role(ctx)
    if role in {"triage_nurse", "ed_doctor", "clinical_supervisor"}:
        return role
    return "triage_nurse"


def _notification_target_role_for_state(workflow_state: Dict[str, Any]) -> str:
    target = str(
        workflow_state.get("assigned_staff_role")
        or workflow_state.get("escalation_target_role")
        or ""
    ).strip()
    if target in _ESCALATION_TARGET_ROLES or target == "triage_nurse":
        return target
    return "triage_nurse"


def _canonical_escalation_target(value: Optional[str]) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    target = str(value).strip()
    if target not in _ESCALATION_TARGET_ROLES:
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid escalation_target_role. Use one of: "
                + ", ".join(sorted(_ESCALATION_TARGET_ROLES))
            ),
        )
    return target


def _overdue_vitals_alert_update(
    rc: case_resolver.ResolvedCase,
    previous_state: Dict[str, Any],
    *,
    now_dt: datetime,
    created_by_role: str,
) -> tuple[Dict[str, Any] | None, str, float | None]:
    """Return an overdue-vitals workflow update when the backend clock is due.

    This is shared by the single-case endpoint and the server-side sweeper so
    both paths enforce the same 210-minute rule, acknowledgement guard, duplicate
    guard, and discharged-case suppression.
    """
    if _is_case_closed(previous_state):
        return None, "case_closed", None
    if previous_state.get("overdue_vitals_alert_active"):
        return None, "already_active", None
    clock_dt = _last_vitals_reference_dt(rc, previous_state)
    if clock_dt is None:
        return None, "missing_vitals_clock", None
    elapsed_minutes = (now_dt - clock_dt).total_seconds() / 60.0
    if elapsed_minutes < _OVERDUE_VITALS_MINUTES:
        return None, "not_due", elapsed_minutes
    if not _vitals_reference_changed_since_ack(previous_state, clock_dt):
        return None, "already_acknowledged_for_current_clock", elapsed_minutes
    now = now_dt.isoformat()
    responsible_role = _notification_target_role_for_state(previous_state)
    return {
        "case_uid": rc.case_uid,
        "source_dataset": rc.source_dataset,
        "updated_at_utc": now,
        "last_action": "OVERDUE_VITALS_ALERT_CREATED",
        "review_status": previous_state.get("review_status")
        or "overdue_vitals_alert_created",
        "overdue_vitals_alert_active": True,
        "overdue_vitals_alert_created_at": now,
        "overdue_vitals_alert_created_by_role": created_by_role,
        "overdue_vitals_responsible_role": responsible_role,
        "overdue_vitals_reference_at": clock_dt.isoformat(),
        "overdue_vitals_elapsed_minutes": round(elapsed_minutes, 1),
        "notification_target_role": responsible_role,
    }, "due", elapsed_minutes


def sweep_overdue_vitals_once(*, limit: int = 50000) -> Dict[str, Any]:
    """Backend-authoritative overdue-vitals sweep for scheduled execution.

    The sweep acts only on cases that already have workflow state. In a governed
    deployment this should run from the API background sweeper or an external
    Azure Timer/worker against the durable workflow-state table.
    """
    from collections import Counter
    from app.storage.case_state_repository import read_case_states

    limit = max(1, min(int(limit or 50000), 50000))
    now_dt = datetime.now(timezone.utc)
    states = read_case_states(_case_state_path(), limit=limit)
    latest: Dict[str, Dict[str, Any]] = {}
    for state in states:
        case_uid = str(state.get("case_uid") or "").strip()
        if case_uid:
            latest[case_uid] = state

    reasons: Counter[str] = Counter()
    created: list[str] = []
    errors: list[Dict[str, Any]] = []
    for case_uid, previous_state in latest.items():
        try:
            rc = case_resolver.resolve(case_uid)
            if rc is None:
                reasons["case_not_found"] += 1
                continue
            update, reason, _elapsed = _overdue_vitals_alert_update(
                rc,
                previous_state,
                now_dt=now_dt,
                created_by_role="system_overdue_vitals_sweeper",
            )
            reasons[reason] += 1
            if update is None:
                continue
            _append_workflow_state(_merge_workflow_state(previous_state, update))
            created.append(case_uid)
        except Exception as exc:
            errors.append({"case_uid": case_uid, "error": exc.__class__.__name__})
            if _sensitive_audit_mode():
                raise
    return {
        "status": "completed",
        "checked": len(latest),
        "created": len(created),
        "created_case_uids": created[:100],
        "reason_counts": dict(reasons),
        "errors": errors[:20],
        "limit": limit,
    }


def _require_multiagent_acuity_explanation_enabled(ctx: AuthContext) -> None:
    """Gate for the clinician-facing MULTI-AGENT case-acuity explanation.

    This is the explanation surface on the triage-review screen (right column,
    under the ML estimate). It EXPLAINS the deterministic model/rules output for
    the case the clinician is already reviewing and never decides acuity. It is
    a different feature from the ITD free-text system chatbot
    (_require_patient_explanation_route_enabled / PERM_ASK_CHATBOT), so it is
    NOT disabled with that blanket switch.

    It stays disable-able for a deployment that wants no cloud-LLM calls at all
    via ALTER_DISABLE_MULTIAGENT_EXPLANATION=true; otherwise it is callable by
    the clinical roles that hold PERM_EXPLAIN_CASE_ACUITY. When Azure OpenAI is
    not configured the runner itself returns a clean NOT_CONFIGURED result, so
    no extra gate is needed for that case.
    """
    import os

    if os.environ.get("ALTER_DISABLE_MULTIAGENT_EXPLANATION", "").lower() == "true":
        raise HTTPException(
            status_code=403,
            detail=(
                "The multi-agent case-acuity explanation is disabled for this "
                "deployment (ALTER_DISABLE_MULTIAGENT_EXPLANATION). The "
                "deterministic model/rules evidence remains available."
            ),
        )


def _require_patient_explanation_route_enabled(ctx: AuthContext) -> None:
    """Keep ITD Ask separate from patient-specific explanation APIs.

    The Streamlit UI no longer calls these routes from clinical screens. Leaving
    the endpoints callable by ITD/security_admin would turn the system assistant
    permission into a hidden patient-case chatbot, so the backend refuses by
    default. Tests or explicitly approved debugging can opt in with a narrowly
    named flag.
    """
    import os

    if os.environ.get("ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE", "").lower() == "true":
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "Patient-specific LLM/AutoGen explanation routes are disabled in the "
            "presentation build. Use the Explainability tab's deterministic "
            "model/rules evidence or the ITD System Assistant for system-only "
            "questions."
        ),
    )


def _public_case_view(rc: case_resolver.ResolvedCase, *, clinical: bool) -> Dict[str, Any]:
    """Build a response that never includes raw identifiers or retrospective data.
    Clinical content (triage-time vitals/chief complaint) only when the caller
    holds can_view_clinical_content; otherwise a minimal non-clinical summary."""
    from app.api import safe_dto
    workflow_state = _latest_workflow_state(rc.case_uid)
    if not clinical:
        out = safe_dto.safe_case_summary(
            rc.case_uid, rc.source_dataset, workflow_state=workflow_state)
        out.update(safe_dto.safe_display_identity(
            _case_dict_with_workflow_updates(rc.case, workflow_state)
        ))
        if rc.case.get("synthetic_demo"):
            out.update({
                "is_synthetic_demo": True,
                "demo_data_notice": (
                    rc.case.get("demo_data_notice")
                    or "Synthetic supervisor-demo case. Not real patient data."
                ),
            })
        if rc.case.get("public_mimic_demo"):
            out.update({
                "is_public_mimic_demo": True,
                "demo_data_notice": (
                    rc.case.get("demo_data_notice")
                    or "Public MIMIC-IV-ED demo subset. View-only sample data."
                ),
            })
        return out
    return safe_dto.safe_clinical_case_view(
        rc.case_uid,
        rc.source_dataset,
        _case_dict_with_workflow_updates(rc.case, workflow_state),
        workflow_state=workflow_state,
    )


@router.get("/cases")
def list_cases(dataset: Optional[str] = None,
               limit: int = 50, offset: int = 0, q: Optional[str] = None,
               subject_id: Optional[str] = None,
               stay_id: Optional[str] = None,
               acuity_level: Optional[str] = None,
               workflow_status: Optional[str] = None,
               case_status: Optional[str] = None,
               active_state: Optional[str] = None,
               ctx: AuthContext = Depends(requires(authz.PERM_VIEW_CASE, "list_cases"))):
    """List pseudonymous case summaries, PAGINATED. limit is capped server-side
    (see case_resolver.MAX_PAGE_SIZE); offset selects the page. Returns the page
    plus pagination metadata so clients can page without ever requesting an
    unbounded result set."""
    filter_params = {
        "subject_id": subject_id,
        "stay_id": stay_id,
        "acuity_level": acuity_level,
        "workflow_status": workflow_status,
        "case_status": case_status,
        "active_state": active_state,
    }
    active_filters = {k: v for k, v in filter_params.items() if str(v or "").strip()}
    if (q and q.strip() or active_filters) and _patient_data_mode():
        exact_q = str(q or "").strip()
        if exact_q and "~" in exact_q and not active_filters:
            try:
                rc = case_resolver.resolve(exact_q)
            except Exception:
                rc = None
            if rc is not None:
                clinical = authz.has_permission(ctx, authz.PERM_VIEW_CLINICAL_CONTENT)
                return {
                    "cases": [_public_case_view(rc, clinical=clinical)],
                    "pagination": {
                        "total": 1,
                        "limit": 1,
                        "offset": 0,
                        "returned": 1,
                        "has_more": False,
                        "next_offset": None,
                        "query": exact_q,
                        "filters": {},
                        "search_mode": "exact_case_uid_patient_mode",
                    },
                }
        raise HTTPException(
            status_code=503,
            detail=(
                "Unindexed /cases search/filter is disabled in PATIENT_DATA_MODE "
                "except exact case_uid lookup. "
                "Wire a database/search-index-backed case query layer before "
                "enabling patient-data search."
            ),
        )
    clinical = authz.has_permission(ctx, authz.PERM_VIEW_CLINICAL_CONTENT)
    cases = case_resolver.list_cases(
        dataset,
        limit=limit,
        offset=offset,
        search=q,
        filters=active_filters or None,
    )
    total = case_resolver.count_cases(dataset, search=q, filters=active_filters or None)
    search_meta = case_resolver.search_metadata(
        dataset, search=q, filters=active_filters or None
    )
    effective_limit = max(0, min(int(limit), case_resolver.MAX_PAGE_SIZE))
    effective_offset = max(0, int(offset))
    next_offset = effective_offset + effective_limit
    has_more = next_offset < total
    if q and search_meta.get("search_truncated"):
        has_more = True
    return {
        "cases": [_public_case_view(rc, clinical=clinical) for rc in cases],
        "pagination": {
            "total": total,
            "limit": effective_limit,
            "offset": effective_offset,
            "returned": len(cases),
            "has_more": has_more,
            "next_offset": next_offset if next_offset < total else None,
            "query": q or "",
            "filters": active_filters,
            **search_meta,
        },
    }


def _queue_state_label(state: Dict[str, Any]) -> str:
    case_status = str(state.get("case_status") or "").lower()
    review_status = str(state.get("review_status") or "").lower()
    escalation_status = str(state.get("escalation_status") or "").lower()
    if case_status in {"discharged", "closed", "case_closed"} or state.get("discharged_at"):
        return "discharged"
    if escalation_status in {"requested", "pending"}:
        return "escalation_requested"
    if escalation_status == "confirmed":
        return "escalated"
    if "information_requested" in review_status or case_status == "request_more_info":
        return "request_more_info"
    if case_status == "accepted":
        return "accepted"
    if review_status:
        return review_status
    return "new_unreviewed"


@router.get("/workflow/queue")
def workflow_queue(
    status: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    ctx: AuthContext = Depends(requires(authz.PERM_VIEW_WORKFLOW_QUEUE, "workflow_queue")),
):
    """Backend-authoritative workflow worklist.

    Cases with persisted workflow state are read from the workflow-state
    repository. A bounded page of cases with no state is added as
    ``new_unreviewed`` for demo/local browsing, but escalated, request-info,
    accepted, discharged, and overdue states come from persisted backend state.
    """
    from collections import Counter
    from app.storage.case_state_repository import read_case_states

    # The worklist is a retention surface, not a paged feed: an active case that
    # falls outside the window disappears from the queue entirely rather than
    # moving to a second page. A 1000 clamp silently truncated it regardless of
    # what the client asked for.
    limit = max(1, min(int(limit or 200), 5000))
    offset = max(0, int(offset or 0))
    wanted = str(status or "").strip().lower()
    state_read_limit = 50000
    states = read_case_states(_case_state_path(), limit=state_read_limit)
    state_read_may_be_truncated = len(states) >= state_read_limit
    if _patient_data_mode() and state_read_may_be_truncated:
        raise HTTPException(
            status_code=503,
            detail=(
                "Workflow queue state read reached the safety cap. Configure an "
                "indexed durable workflow-state query before presenting the "
                "patient-data worklist as authoritative."
            ),
        )
    latest: Dict[str, Dict[str, Any]] = {}
    for state in states:
        case_uid = str(state.get("case_uid") or "").strip()
        if case_uid:
            latest[case_uid] = state

    rows: list[Dict[str, Any]] = []
    from app.api import safe_dto
    for case_uid, state in latest.items():
        label = _queue_state_label(state)
        if wanted and wanted not in {label, str(state.get("case_status") or "").lower()}:
            continue
        identity_labels: Dict[str, Any] = {}
        try:
            rc = case_resolver.resolve(case_uid)
            if rc is not None:
                identity_labels = safe_dto.safe_display_identity(
                    _case_dict_with_workflow_updates(rc.case, state)
                )
        except Exception:
            identity_labels = {}
        rows.append({
            "case_uid": case_uid,
            "source_dataset": state.get("source_dataset"),
            **identity_labels,
            "workflow_status": label,
            "case_status": state.get("case_status") or label,
            "review_status": state.get("review_status"),
            # The acuity the last terminal decision settled on. Present so the
            # review queue colours cards from persisted backend state and keeps
            # that colour across reloads until the case is closed.
            "assigned_acuity": _assigned_acuity_for_row(state),
            "assigned_acuity_source": (
                state.get("assigned_acuity_source")
                if state.get("assigned_acuity") is not None
                else ("derived_legacy" if _assigned_acuity_for_row(state) is not None else None)
            ),
            "escalation_status": state.get("escalation_status"),
            "escalation_target_role": state.get("escalation_target_role"),
            "escalation_requested_by_role": state.get("escalation_requested_by_role"),
            "escalation_confirmed_by_role": state.get("escalation_confirmed_by_role"),
            "escalation_requested_at": state.get("escalation_requested_at"),
            "escalation_confirmed_at": state.get("escalation_confirmed_at"),
            "discharged_at": state.get("discharged_at"),
            "last_vitals_updated_at": state.get("last_vitals_updated_at"),
            "last_vitals_checked_at": state.get("last_vitals_checked_at"),
            "overdue_vitals_alert_active": bool(state.get("overdue_vitals_alert_active")),
            "notification_target_role": state.get("notification_target_role"),
            "updated_at_utc": state.get("updated_at_utc"),
        })

    # Include a bounded page of never-actioned cases as new/unreviewed outside
    # patient-data mode. Patient-data mode should use an indexed workflow table,
    # not a raw case scan, for the authoritative clinical worklist.
    if (not _patient_data_mode()) and (not wanted or wanted == "new_unreviewed"):
        for rc in case_resolver.list_cases(limit=limit + offset, offset=0):
            if rc.case_uid in latest:
                continue
            if wanted and wanted != "new_unreviewed":
                continue
            rows.append({
                "case_uid": rc.case_uid,
                "source_dataset": rc.source_dataset,
                **safe_dto.safe_display_identity(rc.case),
                "workflow_status": "new_unreviewed",
                "case_status": "new_unreviewed",
                "updated_at_utc": "",
            })

    rows.sort(key=lambda row: str(row.get("updated_at_utc") or ""), reverse=True)
    counts = Counter(str(row.get("workflow_status") or "unknown") for row in rows)
    page = rows[offset:offset + limit]
    return {
        "count": len(page),
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < len(rows),
        "summary": dict(counts),
        "state_read_limit": state_read_limit,
        "state_read_may_be_truncated": state_read_may_be_truncated,
        "authoritative": not state_read_may_be_truncated,
        "rows": page,
    }


@router.get("/cases/{case_uid}")
def get_case(case_uid: str,
             ctx: AuthContext = Depends(requires(authz.PERM_VIEW_CASE, "get_case"))):
    rc = _resolve_or_404(case_uid)
    clinical = authz.has_permission(ctx, authz.PERM_VIEW_CLINICAL_CONTENT)
    return _public_case_view(rc, clinical=clinical)


@router.post("/cases/{case_uid}/assessments")
def run_assessment(case_uid: str,
                   preview: bool = False,
                   ctx: AuthContext = Depends(requires(authz.PERM_RUN_ASSESSMENT, "run_assessment"))):
    """Run the deterministic assessment workflow for a case.

    ``preview=true`` computes the advisory acuity for read-only purposes (queue
    colouring / ordering) and does NOT write a workflow-run audit record. It is
    used when a UI needs a case's provisional category without recording that a
    clinician reviewed the case — rendering a queue must not create
    review evidence. The default (preview=false) is the real assessment action
    a clinician takes on an opened case, and it IS audited.
    """
    rc = _resolve_or_404(case_uid)
    workflow_state = _latest_workflow_state(rc.case_uid)
    case = EDTriageCase(**_case_dict_with_workflow_updates(rc.case, workflow_state))
    result = run_workflow(case, include_llm_explanation=False)
    from app.api import safe_dto
    out = safe_dto.safe_assessment_response(
        rc.case_uid, rc.source_dataset, result.model_dump(mode="json"))
    safe_dto.assert_no_raw_identifiers(out)  # final guard
    if preview:
        # Read-only preview: no audit-run record, no compute attribution.
        out["preview"] = True
        return out
    # Persist the workflow-run audit record (redacted, guarded, fail-closed).
    try:
        import uuid as _uuid
        from datetime import datetime, timezone
        from app.schemas.workflow_run import build_workflow_run_record
        from app.storage.workflow_run_repository import append_workflow_run
        from app.config import settings as _settings
        _rec = build_workflow_run_record(
            result, run_id=str(_uuid.uuid4()),
            timestamp_utc=datetime.now(timezone.utc).isoformat())
        append_workflow_run(_settings.processed_dir / "workflow_runs.jsonl", _rec)
    except Exception:
        # In patient-data and local credentialed research modes the guarded
        # writer raises; let it surface so the action fails closed. In public
        # demo mode, audit-write issues are non-fatal.
        if _sensitive_audit_mode():
            raise
    return out


class ExplanationRequest(BaseModel):
    question: Optional[str] = None


@router.post("/cases/{case_uid}/explanations")
def explain_case(case_uid: str, body: ExplanationRequest = ExplanationRequest(),
                 ctx: AuthContext = Depends(requires(authz.PERM_ASK_CHATBOT, "explain_case"))):
    _require_patient_explanation_route_enabled(ctx)
    rc = _resolve_or_404(case_uid)
    workflow_state = _latest_workflow_state(rc.case_uid)
    case = EDTriageCase(**_case_dict_with_workflow_updates(rc.case, workflow_state))
    from app.api import safe_dto
    # If the caller supplied a question, screen it through the agent gateway
    # (authorisation already checked above; this adds prompt-injection screening,
    # max-length, rate limiting, and audit). The LLM only EXPLAINS the already-
    # computed result; it cannot assign/alter triage.
    screened_question: Optional[str] = None
    if body.question:
        screened_question = body.question.strip()
        from app.security.agent_gateway import authorise_agent_call
        gate = authorise_agent_call(ctx, action="explain_case",
                                    case_uid=rc.case_uid, user_text=screened_question)
        if not gate.allowed:
            detail = {"reason": gate.reason}
            if gate.rate_limited:
                raise HTTPException(status_code=429, detail="rate limit exceeded")
            if gate.too_long:
                raise HTTPException(status_code=413, detail="question too long")
            raise HTTPException(status_code=400, detail=f"question blocked: {gate.reason}")
    result = run_workflow(
        case,
        include_llm_explanation=True,
        clinician_question=screened_question,
    )
    out = safe_dto.safe_explanation_response(
        rc.case_uid, rc.source_dataset, result.model_dump(mode="json"))
    safe_dto.assert_no_raw_identifiers(out)
    return out


@router.post("/cases/{case_uid}/multiagent-explanations")
async def multiagent_explain_case(
    case_uid: str,
    body: ExplanationRequest = ExplanationRequest(),
    ctx: AuthContext = Depends(requires(authz.PERM_EXPLAIN_CASE_ACUITY, "multiagent_explain_case")),
):
    _require_multiagent_acuity_explanation_enabled(ctx)
    rc = _resolve_or_404(case_uid)
    workflow_state = _latest_workflow_state(rc.case_uid)
    case = EDTriageCase(**_case_dict_with_workflow_updates(rc.case, workflow_state))
    from app.api import safe_dto

    screened_question: Optional[str] = None
    if body.question:
        screened_question = body.question.strip()

    result = run_workflow(case, include_llm_explanation=False)
    workflow_dict = result.model_dump(mode="json")
    evidence = safe_dto.safe_multiagent_evidence(
        rc.case_uid, rc.source_dataset, workflow_dict)
    safe_dto.assert_no_raw_identifiers(evidence)

    from app.security.agent_gateway import authorise_agent_call
    gate = authorise_agent_call(
        ctx,
        action="multiagent_explain_case",
        case_uid=rc.case_uid,
        user_text=screened_question or "",
        evidence=evidence,
        required_permission=authz.PERM_EXPLAIN_CASE_ACUITY,
    )
    if not gate.allowed:
        if gate.rate_limited:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        if gate.too_long:
            raise HTTPException(status_code=413, detail="question too long")
        raise HTTPException(status_code=400, detail=f"question blocked: {gate.reason}")

    from app.agents.autogen_multi_agent_team import run_case_uid_team_explanation
    team_result = await run_case_uid_team_explanation(
        rc.case_uid,
        gate.minimised_evidence or evidence,
        screened_question,
    )
    status = str(team_result.get("status") or "")
    if status == "SAFETY_FAIL":
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "multiagent_safety_filter_failed",
                "safety_failures": team_result.get("safety_failures") or [],
            },
        )
    if status == "ERROR":
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "multiagent_team_failed",
                "safety_failures": team_result.get("safety_failures") or [],
            },
        )

    out = safe_dto.safe_multiagent_explanation_response(
        rc.case_uid, rc.source_dataset, team_result)
    safe_dto.assert_no_raw_identifiers(out)
    return out


class ReviewBody(BaseModel):
    review_status: str
    review_comment: str = ""
    clinician_decision: Optional[str] = None
    clinician_override: Optional[str] = None
    override_reason: Optional[str] = None
    system_prediction: Optional[str] = None
    requested_fields: list[str] = Field(default_factory=list)
    escalation_target_role: Optional[str] = None

    @field_validator("review_status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        from app.schemas.review import _ALLOWED_REVIEW_STATUSES
        up = (v or "").strip().upper()
        if up not in _ALLOWED_REVIEW_STATUSES:
            raise ValueError(
                f"Invalid review_status '{v}'. Allowed: {sorted(_ALLOWED_REVIEW_STATUSES)}")
        return up

    @field_validator("review_comment")
    @classmethod
    def _check_comment(cls, v: str) -> str:
        from app.schemas.review import _MAX_COMMENT_LEN
        from app.security.redaction import redact_text
        v = v or ""
        if len(v) > _MAX_COMMENT_LEN:
            raise ValueError(f"review_comment too long (max {_MAX_COMMENT_LEN})")
        return redact_text(v)

    @field_validator(
        "clinician_decision", "clinician_override", "override_reason",
        "system_prediction", "escalation_target_role",
    )
    @classmethod
    def _check_short_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        from app.schemas.review import _MAX_OVERRIDE_LEN
        from app.security.redaction import redact_text
        if len(v) > _MAX_OVERRIDE_LEN:
            raise ValueError(f"review field too long (max {_MAX_OVERRIDE_LEN})")
        return redact_text(v)

    @field_validator("requested_fields")
    @classmethod
    def _check_requested_fields(cls, v: list[str]) -> list[str]:
        from app.security.redaction import redact_text
        out = []
        for item in v or []:
            value = redact_text(str(item or "").strip())
            if value:
                out.append(value[:120])
        return out[:20]


from app.rules.acuity_mts_mapping import acuity_from_text as _acuity_from_text  # noqa: E402


def _text_or_empty(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _assigned_acuity_for_row(state: Dict[str, Any]) -> Optional[int]:
    """Acuity for a decided case, falling back to legacy record fields.

    assigned_acuity is only written by decisions taken since it was introduced.
    Decisions recorded before that still carry the clinician's override text or
    the accepted system prediction, so the acuity IS recoverable -- reading only
    the new field left every historical card grey and showing "acuity pending",
    which is a backward-compatibility gap, not a styling one.

    Derivation is read-only and never written back: the stored record stays
    exactly as it was recorded, and the row reports source "derived_legacy" so a
    reviewer can tell a reconstructed value from a recorded one.
    """
    direct = state.get("assigned_acuity")
    if direct is not None:
        parsed = _acuity_from_text(direct)
        if parsed is not None:
            return parsed
    status = str(state.get("review_status") or state.get("last_action") or "").upper()
    if status == "OVERRIDDEN":
        for key in ("clinician_override_decision", "clinician_override", "clinician_decision"):
            parsed = _acuity_from_text(state.get(key))
            if parsed is not None:
                return parsed
    for key in ("system_prediction", "clinician_override_decision", "clinician_decision"):
        parsed = _acuity_from_text(state.get(key))
        if parsed is not None:
            return parsed
    return None


def _review_workflow_state(
    rc: case_resolver.ResolvedCase,
    body: ReviewBody,
    ctx: AuthContext,
) -> Dict[str, Any]:
    status = (body.review_status or "").upper()
    now = _timestamp_utc()
    role = _primary_role(ctx)
    state: Dict[str, Any] = {
        "case_uid": rc.case_uid,
        "source_dataset": rc.source_dataset,
        "updated_at_utc": now,
        "last_action": status,
        "review_status": status.lower(),
        "review_state_updated_at": now,
        "reviewer_role": role,
        **_actor_identity(ctx),
    }
    # Persist the acuity this decision settled on, so the review queue can
    # colour a card from backend state instead of from browser-session memory.
    # An override's acuity is the CLINICIAN's chosen category; otherwise it is
    # the system prediction the clinician accepted. Never guessed: if neither
    # field carries a readable acuity the key is simply absent and the card
    # renders as pending.
    assigned = None
    if status == "OVERRIDDEN":
        assigned = _acuity_from_text(body.clinician_override or body.clinician_decision)
    if assigned is None:
        assigned = _acuity_from_text(body.system_prediction)
    if assigned is not None:
        state["assigned_acuity"] = assigned
        state["assigned_acuity_source"] = (
            "clinician_override" if status == "OVERRIDDEN" else "system_prediction_accepted"
        )
        state["assigned_acuity_set_at"] = now
    if status == "ACCEPTED_AS_PRESENTED":
        state.update({
            "case_status": "accepted",
            "case_level_clinician_acceptance": True,
            "accepted_timestamp": now,
            "accepted_by_role": role,
            "acceptance_scope": "individual_case_review_only",
            # A clinician accepting the case as presented is a terminal review
            # decision: it closes any open escalation and clears a pending
            # request-for-info so the case cannot sit in two states at once.
            "_terminal_review_decision": True,
        })
    if status == "OVERRIDDEN":
        # An override IS the clinician's decision: the case leaves the triage
        # queue and moves to the review board like an acceptance does, and it
        # likewise closes any open escalation / request-for-info.
        state.update({
            "case_status": "overridden",
            "overridden_at": now,
            "overridden_by_role": role,
            "clinician_override_decision": body.clinician_override or body.clinician_decision,
            "_terminal_review_decision": True,
        })
    if status == "UNCERTAIN":
        state.update({
            "case_status": "uncertain",
            "uncertain_at": now,
            "uncertain_by_role": role,
        })
    if status == "REQUEST_MORE_INFORMATION":
        state.update({
            "case_status": "request_more_info",
            "review_status": "information_requested",
            "requested_fields": list(body.requested_fields or []),
            "requesting_role": role,
            "request_timestamp": now,
        })
    if status in _ESCALATION_REQUEST_STATUSES:
        target_role = _canonical_escalation_target(
            body.escalation_target_role
        ) or "clinical_supervisor"
        state.update({
            "case_status": "escalation_requested",
            "escalation_required": True,
            "escalation_state": "requested",
            "escalation_status": "requested",
            "escalation_target_role": target_role,
            "assigned_staff_role": target_role,
            "escalation_timestamp": now,
            "escalation_requested_at": now,
            "escalation_requested_by_role": role,
            "escalation_reason": (
                body.override_reason
                or body.review_comment
                or "Reviewer requested escalation."
            ),
            "escalation_evidence": {
                "review_status": status,
                "system_prediction": body.system_prediction,
                "clinician_decision": body.clinician_decision,
            },
            # If the case had already been accepted, this escalation supersedes
            # that acceptance — the merge marks the prior acceptance non-current
            # so the case is not left reading as both accepted and escalated
            # without an explicit supersede record.
            "_supersede_prior_acceptance": True,
        })
    if status in _ESCALATION_CONFIRM_STATUSES:
        state.update({
            "case_status": "escalated",
            "review_status": "escalation_confirmed",
            "escalation_required": True,
            "escalation_state": "confirmed",
            "escalation_status": "confirmed",
            "escalation_confirmed_at": now,
            "escalation_confirmed_by_role": role,
            "escalation_confirmation_note": body.review_comment,
        })
    if status in _ESCALATION_CLOSE_STATUSES:
        resolution = status.replace("ESCALATION_", "").lower()
        state.update({
            "case_status": f"escalation_{resolution}",
            "review_status": f"escalation_{resolution}",
            "escalation_required": False,
            "escalation_state": resolution,
            "escalation_status": resolution,
            "escalation_closed_at": now,
            "escalation_closed_by_role": role,
            "escalation_resolution_note": body.review_comment,
            "_resolve_active_escalation": True,
        })
    if status in _CASE_CLOSE_STATUSES:
        # DISCHARGED and CASE_CLOSED are both terminal, but they are different
        # dispositions (home vs admitted). Persist them distinctly — collapsing
        # CASE_CLOSED into "discharged" misrepresented admitted patients.
        admitted = status == "CASE_CLOSED"
        state.update({
            "case_status": "case_closed" if admitted else "discharged",
            "review_status": "case_closed" if admitted else "discharged",
            "closed_disposition": "admitted" if admitted else "discharged",
            "closed_at": now,
            "closed_by_role": role,
            "escalation_closed_at": now,
            "escalation_status": "closed",
            "escalation_state": "closed",
            "_resolve_active_escalation": True,
            "_close_case": True,
        })
        if not admitted:
            state.update({
                "discharged_at": now,
                "discharged_by_role": role,
                "discharge_reason": body.review_comment or body.override_reason,
            })
    return state


@router.post("/cases/{case_uid}/reviews")
def submit_review(case_uid: str, body: ReviewBody,
                  ctx: AuthContext = Depends(requires(authz.PERM_SUBMIT_REVIEW, "submit_review"))):
    rc = _resolve_or_404(case_uid)
    # Reviewer identity comes from the AUTHENTICATED context, never the client.
    from app.schemas.review import HumanReviewRecord
    from app.storage.human_review_repository import append_human_review
    from app.config import settings
    from datetime import datetime, timezone
    from uuid import uuid4

    status = (body.review_status or "").upper()
    _require_review_action_authorised(status, ctx)
    # A reason is required only for a genuine override or an explicitly uncertain
    # decision (item C). "Request more information" is a routing action, not an
    # override, so it does not require an override reason.
    override_like = (
        bool(body.clinician_override)
        or status in ("OVERRIDDEN", "OVERRIDE", "UNCERTAIN", "OVERRIDE_REQUIRED")
    )
    if override_like and not body.override_reason:
        raise HTTPException(status_code=422,
                            detail="override_reason is required for an override/uncertain decision")
    previous_state = _latest_workflow_state(rc.case_uid)
    if _is_case_closed(previous_state):
        raise HTTPException(
            status_code=409,
            detail="case is discharged/closed and cannot receive further clinical workflow actions",
        )
    if status in _ESCALATION_CONFIRM_STATUSES:
        if _escalation_confirmed_state(previous_state):
            raise HTTPException(
                status_code=409,
                detail="escalation is already confirmed for this case",
            )
        if not _requested_escalation_exists(previous_state):
            raise HTTPException(
                status_code=409,
                detail="no requested escalation exists for this case",
            )
        if not (body.review_comment or body.override_reason):
            raise HTTPException(
                status_code=422,
                detail="a confirmation/resolution note is required for escalation workflow actions",
            )
    if status in _ESCALATION_CLOSE_STATUSES:
        if _escalation_terminal_state(previous_state):
            raise HTTPException(
                status_code=409,
                detail="escalation is already resolved/closed for this case",
            )
        if not _active_escalation_exists(previous_state):
            raise HTTPException(
                status_code=409,
                detail="no active escalation exists for this case",
            )
        if not (body.review_comment or body.override_reason):
            raise HTTPException(
                status_code=422,
                detail="a confirmation/resolution note is required for escalation workflow actions",
            )

    rec = HumanReviewRecord(
        review_id=str(uuid4()),
        stay_id=rc.stay_id,                       # redacted before persistence
        source_dataset=rc.source_dataset,
        case_uid=rc.case_uid,
        reviewer_user_id=getattr(ctx, "user_id", None),
        reviewer_display_name=(
            getattr(ctx, "display_name", None) or getattr(ctx, "user_id", None)
        ),
        # False for demo personas: X-Demo-Role/X-Demo-User are client-supplied
        # presentation identities, not authenticated proof of who acted.
        reviewer_identity_verified=bool(getattr(ctx, "authenticated", False))
        and not bool(getattr(ctx, "is_demo_stub", False)),
        reviewer_email_or_pseudonym=getattr(ctx, "email", None) or getattr(ctx, "user_id", None),
        reviewer_roles=list(getattr(ctx, "roles", []) or []),
        auth_source=getattr(ctx, "source", None),
        reviewer_role=(list(getattr(ctx, "roles", []) or []) or [None])[0],
        review_status=body.review_status,
        review_comment=body.review_comment,
        system_prediction=body.system_prediction,
        clinician_decision=body.clinician_decision,
        clinician_override=body.clinician_override,
        override_reason=body.override_reason,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    workflow_state = _append_workflow_state(
        _merge_workflow_state(
            previous_state,
            _review_workflow_state(rc, body, ctx),
        )
    )
    # Operational state is the safety-critical readback surface. Persist it
    # before the append-only review evidence so a review/audit write cannot claim
    # a transition that is absent from the current case state.
    append_human_review(settings.processed_dir / "human_reviews.jsonl", rec)
    return {
        "review_id": rec.review_id,
        "case_uid": rc.case_uid,
        "status": "recorded",
        "workflow_state": workflow_state,
    }


# key -> (min, max) inclusive plausible ranges for triage-time vitals.
_ALLOWED_FOLLOWUP_VITALS = {
    # Lower bounds are plausible-but-survivable minimums: a recorded 0 for a
    # perfusing vital is missing/erroneous data, not an observation, and must be
    # rejected rather than persisted and fed to the model. Ranges stay wide
    # enough for genuine peri-arrest physiology (HR 20, SBP 40, SpO2 40).
    "heartrate": (10.0, 350.0),
    "resprate": (2.0, 120.0),
    "o2sat": (20.0, 100.0),
    "sbp": (20.0, 400.0),
    "dbp": (10.0, 300.0),
    "temperature": (50.0, 115.0),   # Fahrenheit range (MIMIC default unit)
    "pain": (0.0, 10.0),            # 0 = no pain is a valid observation
}
_MAX_SUPPORTING_UPLOAD_BYTES = 50 * 1024 * 1024
_ALLOWED_SUPPORTING_UPLOAD_TYPES = {
    "application/dicom",
    "application/octet-stream",
    "application/pdf",
    "image/bmp",
    "image/dicom",
    "image/heic",
    "image/heif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
}
_SAFE_UPLOAD_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_upload_filename(filename: str) -> str:
    from app.security.redaction import redact_text

    raw = str(filename or "").replace("\\", "/").split("/")[-1].strip()
    raw = redact_text(raw)[:128]
    clean = _SAFE_UPLOAD_NAME_RE.sub("_", raw).strip("._-")
    return clean or "supporting-file"


def _supporting_upload_dir(case_uid: str):
    from app.config import settings
    from app.security.local_paths import credentialed_artifact_path

    base = credentialed_artifact_path(
        settings.processed_dir / "supporting_uploads",
        purpose="supporting scan upload output",
    )
    case_dir = _SAFE_UPLOAD_NAME_RE.sub("_", case_uid)[:96].strip("._-")
    path = base / (case_dir or "case")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _supporting_upload_storage_ref(case_uid: str, stored_filename: str) -> str:
    case_dir = _SAFE_UPLOAD_NAME_RE.sub("_", case_uid)[:96].strip("._-") or "case"
    return f"supporting_uploads/{case_dir}/{stored_filename}"


class FollowupBody(BaseModel):
    # A follow-up reruns the case with updated TRIAGE-TIME vitals to compare
    # acuity. Only an allow-list of vital keys is accepted, each numeric and
    # within a plausible physiological range; unknown keys or out-of-range values
    # are rejected rather than blindly merged onto the case.
    updated_vitals: Dict[str, Any] = {}
    updated_complaint: Optional[str] = Field(default=None, max_length=240)
    updated_context: Optional[str] = Field(default=None, max_length=1000)
    scan_uploads: list[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("updated_vitals")
    @classmethod
    def _validate_updated_vitals(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if not v:
            return {}
        clean: Dict[str, Any] = {}
        for key, value in v.items():
            if key not in _ALLOWED_FOLLOWUP_VITALS:
                raise ValueError(
                    f"Unknown vital '{key}'. Allowed: {sorted(_ALLOWED_FOLLOWUP_VITALS)}")
            try:
                num = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"Vital '{key}' must be numeric, got {value!r}")
            lo, hi = _ALLOWED_FOLLOWUP_VITALS[key]
            if not (lo <= num <= hi):
                raise ValueError(
                    f"Vital '{key}'={num} out of plausible range [{lo}, {hi}]")
            clean[key] = num
        return clean

    @field_validator("updated_complaint", "updated_context")
    @classmethod
    def _validate_followup_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("scan_uploads")
    @classmethod
    def _validate_scan_uploads(cls, v: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        if not v:
            return []
        if len(v) > 5:
            raise ValueError("At most 5 supporting scan/image metadata entries are accepted.")
        out = []
        for item in v:
            if not isinstance(item, dict):
                raise ValueError("Each scan upload entry must be metadata object.")
            filename = str(item.get("filename") or "").split("/")[-1].split("\\")[-1].strip()[:128]
            if not filename:
                raise ValueError(
                    "scan upload metadata requires a non-empty filename.")
            clean = {
                "upload_id": str(item.get("upload_id") or "")[:64],
                "filename": filename,
                "content_type": str(item.get("content_type") or "")[:100],
                "size_bytes": item.get("size_bytes"),
                "sha256": str(item.get("sha256") or "")[:128],
                "storage_ref": str(item.get("storage_ref") or "")[:240],
                "analysis_status": str(
                    item.get("analysis_status") or "metadata_recorded_pending_multimodal_analysis"
                )[:100],
                "bytes_stored": bool(item.get("bytes_stored") is True),
            }
            if not clean["upload_id"]:
                clean.pop("upload_id")
            if not clean["storage_ref"]:
                clean.pop("storage_ref")
            if clean["size_bytes"] is not None:
                try:
                    size = int(clean["size_bytes"])
                except (TypeError, ValueError):
                    raise ValueError("scan upload size_bytes must be numeric.")
                if size < 0 or size > 50 * 1024 * 1024:
                    raise ValueError("scan upload metadata exceeds 50MB per file limit.")
                clean["size_bytes"] = size
            out.append(clean)
        return out


class FollowupExplanationBody(FollowupBody):
    question: Optional[str] = None


def _changed_vital_summary(case_dict: Dict[str, Any],
                           updated_vitals: Dict[str, Any]) -> list[dict[str, Any]]:
    triage = case_dict.get("triage") or {}
    return [
        {"field": key, "previous": triage.get(key), "new": value}
        for key, value in updated_vitals.items()
    ]


def _run_followup_workflows(
    rc,
    updated_vitals: Dict[str, Any],
    updated_complaint: Optional[str] = None,
):
    previous_state = _latest_workflow_state(rc.case_uid)
    base_case_dict = _case_dict_with_workflow_updates(rc.case, previous_state)
    previous = EDTriageCase(**base_case_dict)
    prev_result = run_workflow(previous, include_llm_explanation=False)
    new_case_dict = dict(base_case_dict)
    triage = dict(new_case_dict.get("triage") or {})
    if updated_vitals:
        # The triage model types `pain` as a string (MIMIC stores it as text),
        # but the followup validator normalises it to a number. Coerce it back
        # to a string when merging so rebuilding EDTriageCase does not fail on a
        # pain-inclusive reassessment.
        merged_vitals = dict(updated_vitals)
        if "pain" in merged_vitals and merged_vitals["pain"] is not None:
            pain_val = merged_vitals["pain"]
            merged_vitals["pain"] = str(int(pain_val)) if float(pain_val).is_integer() else str(pain_val)
        triage.update(merged_vitals)
    if updated_complaint:
        triage["chiefcomplaint"] = updated_complaint
    new_case_dict["triage"] = triage
    new_result = run_workflow(EDTriageCase(**new_case_dict), include_llm_explanation=False)
    return prev_result, new_result, _changed_vital_summary(base_case_dict, updated_vitals)


def _final_acuity_from_workflow(result) -> Any:
    faa = result.model_dump(mode="json").get("final_acuity_assessment") or {}
    return faa.get("final_acuity")


@router.post("/cases/{case_uid}/supporting-uploads")
async def upload_supporting_scan(
    case_uid: str,
    file: UploadFile = File(...),
    ctx: AuthContext = Depends(requires(authz.PERM_RUN_ASSESSMENT, "upload_supporting_scan")),
):
    if _patient_data_mode():
        raise HTTPException(
            status_code=503,
            detail="Supporting file uploads require durable clinical storage in patient-data mode.",
        )
    rc = _resolve_or_404(case_uid)
    previous_state = _latest_workflow_state(rc.case_uid)
    if _is_case_closed(previous_state):
        raise HTTPException(
            status_code=409,
            detail="case is discharged/closed and cannot receive supporting files",
        )
    filename = _safe_upload_filename(file.filename or "")
    content_type = (file.content_type or "application/octet-stream").lower()
    if content_type not in _ALLOWED_SUPPORTING_UPLOAD_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Upload MRI/X-ray images, DICOM, or PDF documents.",
        )
    upload_id = uuid.uuid4().hex
    stored_filename = f"{upload_id}_{filename}"
    dest = _supporting_upload_dir(rc.case_uid) / stored_filename
    digest = hashlib.sha256()
    size = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_SUPPORTING_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Supporting file exceeds the 50MB upload limit.",
                    )
                digest.update(chunk)
                out.write(chunk)
    except HTTPException:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        await file.close()
    if size <= 0:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=422, detail="Uploaded file was empty.")
    return {
        "upload_id": upload_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "storage_ref": _supporting_upload_storage_ref(rc.case_uid, stored_filename),
        "analysis_status": "uploaded_pending_clinician_review",
        "bytes_stored": True,
    }


@router.post("/cases/{case_uid}/followups")
def followup_case(case_uid: str, body: FollowupBody,
                  ctx: AuthContext = Depends(requires(authz.PERM_RUN_ASSESSMENT, "followup_case"))):
    rc = _resolve_or_404(case_uid)
    previous_state = _latest_workflow_state(rc.case_uid)
    if _is_case_closed(previous_state):
        raise HTTPException(
            status_code=409,
            detail="case is discharged/closed and cannot be reassessed",
        )
    if not body.updated_vitals and not body.updated_complaint and not body.updated_context and not body.scan_uploads:
        raise HTTPException(
            status_code=422,
            detail="Provide updated vitals, complaint/context, or scan metadata for reassessment.",
        )
    prev_result, new_result, changed_vitals = _run_followup_workflows(
        rc, body.updated_vitals, body.updated_complaint)
    prev_a, new_a = (
        _final_acuity_from_workflow(prev_result),
        _final_acuity_from_workflow(new_result),
    )
    from app.api import safe_dto
    changed_fields = list(body.updated_vitals.keys())
    if body.updated_complaint:
        changed_fields.append("chiefcomplaint")
    if body.updated_context:
        changed_fields.append("clinician_supplied_context")
    if body.scan_uploads:
        changed_fields.append("supporting_scan_metadata")
    out = safe_dto.safe_followup_response(
        rc.case_uid,
        prev_a,
        new_a,
        changed_fields=changed_fields,
        changed_vitals=changed_vitals,
    )
    # Be explicit about what actually drove the recomputation. The deterministic
    # rules engine and ML model operate on structured triage fields (vitals,
    # complaint); free-text clinical context and scan metadata are RECORDED with
    # the case but do not themselves recompute the acuity. Say so, so a
    # context-only submission is not mistaken for a vitals-driven reassessment.
    recomputed = bool(body.updated_vitals) or bool(body.updated_complaint)
    out["additional_information"] = {
        "updated_complaint_supplied": bool(body.updated_complaint),
        "updated_context_supplied": bool(body.updated_context),
        "scan_upload_count": len(body.scan_uploads),
        "scan_analysis_status": (
            "metadata_recorded_pending_multimodal_analysis" if body.scan_uploads else None
        ),
        "acuity_recomputed_from_structured_inputs": recomputed,
        "context_and_scan_are_recorded_not_scored": bool(
            body.updated_context or body.scan_uploads
        ),
        "reassessment_note": (
            "Acuity recomputed from updated vitals/complaint."
            if recomputed
            else "Clinical context and any scan metadata were recorded with the "
                 "case for the reviewing clinician. They do not by themselves "
                 "recompute the acuity — the category above is unchanged from the "
                 "current triage-time inputs."
        ),
    }
    safe_dto.assert_no_raw_identifiers(out)
    workflow_state = None
    # Persist a rerun audit record (redacted, guarded, fail-closed).
    try:
        import uuid as _uuid
        from datetime import datetime, timezone
        from app.schemas.rerun import (
            ScanUploadMetadata,
            WorkflowRerunRecord,
            VitalChange,
            compute_movement,
        )
        from app.storage.rerun_repository import append_rerun
        from app.config import settings as _settings
        base_case_dict = _case_dict_with_workflow_updates(rc.case, previous_state)
        base_triage = base_case_dict.get("triage") or {}
        changed = [VitalChange(field=k, previous=base_triage.get(k),
                               new=v) for k, v in body.updated_vitals.items()]
        _rec = WorkflowRerunRecord(
            rerun_id=str(_uuid.uuid4()),
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            case_uid=rc.case_uid, source_dataset=rc.source_dataset, stay_id=rc.stay_id,
            previous_final_acuity=prev_a, new_final_acuity=new_a,
            previous_category=None, new_category=None,
            changed_vitals=changed,
            changed_fields=changed_fields,
            previous_chiefcomplaint=base_triage.get("chiefcomplaint"),
            new_chiefcomplaint=body.updated_complaint,
            updated_context=body.updated_context,
            scan_uploads=[ScanUploadMetadata(**item) for item in body.scan_uploads],
            movement=compute_movement(prev_a, new_a),
            reason=out["change"])
        append_rerun(_settings.processed_dir / "workflow_reruns.jsonl", _rec)
    except Exception:
        if _sensitive_audit_mode():
            raise
    now = _timestamp_utc()
    followup_escalates = out.get("change_direction") == "escalation"
    workflow_state_payload = {
        "case_uid": rc.case_uid,
        "source_dataset": rc.source_dataset,
        "updated_at_utc": now,
        "last_action": (
            "FOLLOWUP_ESCALATION"
            if followup_escalates
            else "FOLLOWUP_REASSESSMENT"
        ),
        "review_status": (
            "escalation_pending"
            if followup_escalates
            else (
                "reassessment_complete_escalation_still_active"
                if _active_escalation_exists(previous_state)
                else "reassessment_complete"
            )
        ),
        "changed_fields": changed_fields,
        "changed_vitals": changed_vitals,
        "additional_information": out["additional_information"],
        "information_response_received_at": now,
        "information_response_by_role": _primary_role(ctx),
    }
    if body.updated_vitals:
        workflow_state_payload.update({
            "last_vitals_checked_at": now,
            "last_vitals_updated_at": now,
            "overdue_vitals_alert_active": False,
            "overdue_vitals_acknowledged_at": None,
        })
    latest_triage_updates = dict(previous_state.get("latest_triage_updates") or {})
    if body.updated_vitals:
        latest_triage_updates.update(body.updated_vitals)
    if body.updated_complaint:
        latest_triage_updates["chiefcomplaint"] = body.updated_complaint
    if latest_triage_updates:
        workflow_state_payload.update({
            "latest_triage_updates": latest_triage_updates,
            "latest_triage_updates_at": now,
        })
    if body.scan_uploads:
        workflow_state_payload["scan_uploads"] = body.scan_uploads
    if followup_escalates:
        workflow_state_payload.update({
            "case_status": "escalation_requested",
            "escalation_required": True,
            "escalation_state": "requested",
            "escalation_status": "requested",
            "escalation_target_role": "clinical_supervisor",
            "escalation_timestamp": now,
            "escalation_requested_at": now,
            "escalation_requested_by_role": _primary_role(ctx),
            "escalation_reason": out.get("change_summary"),
            "escalation_evidence": {
                "previous_acuity": prev_a,
                "new_acuity": new_a,
                "changed_fields": changed_fields,
                "changed_vitals": changed_vitals,
            },
        })
    workflow_state = _append_workflow_state(
        _merge_workflow_state(previous_state, workflow_state_payload)
    )
    if followup_escalates or _active_escalation_exists(workflow_state):
        out.update({
            "escalation_required": True,
            "escalation_status": workflow_state.get("escalation_status"),
            "escalation_state": workflow_state.get("escalation_state"),
            "escalation_target_role": workflow_state.get("escalation_target_role"),
            "escalation_timestamp": workflow_state.get("escalation_timestamp"),
            "escalation_reason": out.get("change_summary"),
            "escalation_evidence": workflow_state.get("escalation_evidence"),
            "workflow_state": workflow_state,
        })
    else:
        out["workflow_state"] = workflow_state
    safe_dto.assert_no_raw_identifiers(out)
    return out


@router.post("/cases/{case_uid}/vitals/mark-overdue-alert")
def mark_overdue_vitals_alert(
    case_uid: str,
    ctx: AuthContext = Depends(
        requires(authz.PERM_SUBMIT_REVIEW, "mark_overdue_vitals_alert")
    ),
):
    """Persist creation of an overdue-vitals staff notification once."""
    rc = _resolve_or_404(case_uid)
    previous_state = _latest_workflow_state(rc.case_uid)
    if _is_case_closed(previous_state):
        raise HTTPException(
            status_code=409,
            detail="case is discharged/closed; overdue-vitals notifications are disabled",
        )
    if previous_state.get("overdue_vitals_alert_active"):
        return {
            "case_uid": rc.case_uid,
            "status": "already_active",
            "workflow_state": previous_state,
        }
    now_dt = datetime.now(timezone.utc)
    update, reason, elapsed_minutes = _overdue_vitals_alert_update(
        rc,
        previous_state,
        now_dt=now_dt,
        created_by_role=_primary_role(ctx) or "unknown",
    )
    if reason == "missing_vitals_clock":
        raise HTTPException(
            status_code=422,
            detail=(
                "Cannot create overdue-vitals alert because the backend cannot "
                "verify the last vitals check/update time."
            ),
        )
    if reason == "not_due":
        raise HTTPException(
            status_code=409,
            detail=(
                "Vitals are not yet overdue by the 210-minute backend rule "
                f"(elapsed_minutes={(elapsed_minutes or 0.0):.1f})."
            ),
        )
    if reason == "already_acknowledged_for_current_clock":
        raise HTTPException(
            status_code=409,
            detail=(
                "This overdue-vitals notification has already been acknowledged "
                "for the current vitals clock. It will not be recreated until "
                "vitals are checked or updated."
            ),
        )
    if update is None:
        raise HTTPException(status_code=409, detail=f"overdue-vitals alert not created: {reason}")
    workflow_state = _append_workflow_state(
        _merge_workflow_state(
            previous_state,
            update,
        )
    )
    return {
        "case_uid": rc.case_uid,
        "status": "created",
        "workflow_state": workflow_state,
    }


@router.post("/workflow/overdue-vitals/sweep")
def sweep_overdue_vitals_alerts(
    limit: int = 50000,
    ctx: AuthContext = Depends(
        # The sweep WRITES alert state; a read-only workflow permission
        # (governance auditor) must not be able to mutate cases.
        requires(authz.PERM_RUN_ASSESSMENT, "sweep_overdue_vitals_alerts")
    ),
):
    return sweep_overdue_vitals_once(limit=limit)


@router.post("/cases/{case_uid}/vitals/acknowledge-overdue")
def acknowledge_overdue_vitals(
    case_uid: str,
    ctx: AuthContext = Depends(
        requires(authz.PERM_SUBMIT_REVIEW, "acknowledge_overdue_vitals")
    ),
):
    """Acknowledge an overdue-vitals notification without mutating triage data.

    Updating vitals via the reassessment endpoint clears the alert clock. This
    endpoint records that responsible staff saw the overdue-vitals notification,
    suppressing duplicate visible alerts until vitals are next updated.
    """
    rc = _resolve_or_404(case_uid)
    previous_state = _latest_workflow_state(rc.case_uid)
    if _is_case_closed(previous_state):
        raise HTTPException(
            status_code=409,
            detail="case is discharged/closed; overdue-vitals notifications are disabled",
        )
    if not previous_state.get("overdue_vitals_alert_active"):
        raise HTTPException(
            status_code=409,
            detail="no active overdue-vitals notification exists for this case",
        )
    # The alert is raised FOR a specific role and acknowledging it clears the
    # alert for everyone. Any holder of PERM_SUBMIT_REVIEW could therefore
    # silence a notification addressed to a different role, and the staff member
    # actually responsible would never see it. Enforce the target the sweeper
    # recorded; only clear it if you are who it was raised for.
    target_role = _text_or_empty(previous_state.get("notification_target_role"))
    actor_role = _primary_role(ctx) or ""
    if target_role and actor_role != target_role:
        raise HTTPException(
            status_code=403,
            detail=(
                f"this overdue-vitals notification is assigned to '{target_role}'; "
                f"'{actor_role or 'unknown role'}' cannot acknowledge it"
            ),
        )
    now = _timestamp_utc()
    workflow_state = _append_workflow_state(
        _merge_workflow_state(
            previous_state,
            {
                "case_uid": rc.case_uid,
                "source_dataset": rc.source_dataset,
                "updated_at_utc": now,
                "last_action": "OVERDUE_VITALS_ACKNOWLEDGED",
                "review_status": previous_state.get("review_status")
                or "overdue_vitals_acknowledged",
                "overdue_vitals_alert_active": False,
                "overdue_vitals_acknowledged_at": now,
                "overdue_vitals_acknowledged_by_role": _primary_role(ctx),
                "overdue_vitals_acknowledged_reference_at": (
                    previous_state.get("overdue_vitals_reference_at")
                ),
            },
        )
    )
    return {
        "case_uid": rc.case_uid,
        "status": "acknowledged",
        "workflow_state": workflow_state,
    }


@router.post("/cases/{case_uid}/followups/explanations")
def followup_explain_case(
    case_uid: str,
    body: FollowupExplanationBody,
    ctx: AuthContext = Depends(requires(authz.PERM_ASK_CHATBOT, "followup_explain_case")),
):
    _require_patient_explanation_route_enabled(ctx)
    if not body.updated_vitals and not body.updated_complaint:
        raise HTTPException(
            status_code=422,
            detail="updated vitals or an updated complaint is required before follow-up explanation.",
        )
    rc = _resolve_or_404(case_uid)
    from app.api import safe_dto

    prev_result, new_result, changed_vitals = _run_followup_workflows(
        rc, body.updated_vitals, body.updated_complaint)
    evidence = safe_dto.safe_followup_multiagent_evidence(
        rc.case_uid,
        rc.source_dataset,
        prev_result.model_dump(mode="json"),
        new_result.model_dump(mode="json"),
        changed_fields=(
            list(body.updated_vitals.keys())
            + (["chiefcomplaint"] if body.updated_complaint else [])
        ),
        changed_vitals=changed_vitals,
    )
    safe_dto.assert_no_raw_identifiers(evidence)

    screened_question = (
        body.question.strip()
        if body.question and body.question.strip()
        else "Why did the acuity change or stay the same after the edited vitals?"
    )
    from app.security.agent_gateway import authorise_agent_call
    gate = authorise_agent_call(
        ctx,
        action="followup_explain_case",
        case_uid=rc.case_uid,
        user_text=screened_question,
        evidence=evidence,
    )
    if not gate.allowed:
        if gate.rate_limited:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        if gate.too_long:
            raise HTTPException(status_code=413, detail="question too long")
        raise HTTPException(status_code=400, detail=f"question blocked: {gate.reason}")

    from app.agents.llm_explanation_agent import run_llm_explanation
    explanation = run_llm_explanation(
        gate.minimised_evidence or evidence,
        clinician_question=screened_question,
    )
    out = safe_dto.safe_followup_explanation_response(
        rc.case_uid,
        rc.source_dataset,
        explanation.model_dump(mode="json"),
        evidence,
    )
    safe_dto.assert_no_raw_identifiers(out)
    return out


@router.post("/cases/{case_uid}/followups/multiagent-explanations")
async def followup_multiagent_explain_case(
    case_uid: str,
    body: FollowupExplanationBody,
    ctx: AuthContext = Depends(
        requires(authz.PERM_EXPLAIN_CASE_ACUITY, "followup_multiagent_explain_case")
    ),
):
    _require_multiagent_acuity_explanation_enabled(ctx)
    if not body.updated_vitals and not body.updated_complaint:
        raise HTTPException(
            status_code=422,
            detail="updated vitals or an updated complaint is required before follow-up explanation.",
        )
    rc = _resolve_or_404(case_uid)
    from app.api import safe_dto

    prev_result, new_result, changed_vitals = _run_followup_workflows(
        rc, body.updated_vitals, body.updated_complaint)
    evidence = safe_dto.safe_followup_multiagent_evidence(
        rc.case_uid,
        rc.source_dataset,
        prev_result.model_dump(mode="json"),
        new_result.model_dump(mode="json"),
        changed_fields=(
            list(body.updated_vitals.keys())
            + (["chiefcomplaint"] if body.updated_complaint else [])
        ),
        changed_vitals=changed_vitals,
    )
    safe_dto.assert_no_raw_identifiers(evidence)

    screened_question = (
        body.question.strip()
        if body.question and body.question.strip()
        else "Why did the acuity change or stay the same after the edited vitals?"
    )
    from app.security.agent_gateway import authorise_agent_call
    gate = authorise_agent_call(
        ctx,
        action="followup_multiagent_explain_case",
        case_uid=rc.case_uid,
        user_text=screened_question,
        evidence=evidence,
        required_permission=authz.PERM_EXPLAIN_CASE_ACUITY,
    )
    if not gate.allowed:
        if gate.rate_limited:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        if gate.too_long:
            raise HTTPException(status_code=413, detail="question too long")
        raise HTTPException(status_code=400, detail=f"question blocked: {gate.reason}")

    from app.agents.autogen_multi_agent_team import run_case_uid_team_explanation
    team_result = await run_case_uid_team_explanation(
        rc.case_uid,
        gate.minimised_evidence or evidence,
        screened_question,
    )
    status = str(team_result.get("status") or "")
    if status == "SAFETY_FAIL":
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "multiagent_safety_filter_failed",
                "safety_failures": team_result.get("safety_failures") or [],
            },
        )
    if status == "ERROR":
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "multiagent_team_failed",
                "safety_failures": team_result.get("safety_failures") or [],
            },
        )

    out = safe_dto.safe_multiagent_explanation_response(
        rc.case_uid, rc.source_dataset, team_result)
    safe_dto.assert_no_raw_identifiers(out)
    return out


def _old_followup_return():
    return None
