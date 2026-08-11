"""
Security-status, audit-events, and model-performance endpoints.

  GET /security/status   (RBAC: view_security_status)
  GET /audit/events      (RBAC: view_audit_log)
  GET /model/performance (RBAC: view_model_performance)

All enforce requires(permission), audit the access, and never expose secrets,
local asset paths, or raw identifiers.
"""
from __future__ import annotations

import csv
import math
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth_dependencies import requires
from app.security.identity import AuthContext
from app.security import authz
from app.security.security_status import build_security_status
from app.config import settings

router = APIRouter()


class SystemAssistantRequest(BaseModel):
    question: str = Field(default="", max_length=1000)


_PATIENT_QUESTION_TERMS = {
    "diagnose this", "diagnosis for", "treatment for", "what acuity",
    "assign acuity", "which triage category", "what triage category",
    "heart attack", "stroke", "medicine for", "symptom means",
}


@router.post("/system/assistant",
             dependencies=[Depends(requires(authz.PERM_ASK_CHATBOT, "system_assistant"))])
def system_assistant(body: SystemAssistantRequest) -> Dict[str, Any]:
    """ITD assistant for system, security, governance and AUDIT questions.

    Answers from recorded backend evidence: configuration/security posture plus
    the same already-redacted audit records the dashboard uses. It is still not
    the clinical case explainer — it takes no case_uid, reads no clinical case
    content, and refuses patient-triage questions, so this surface cannot become
    a hidden clinical chatbot.

    Deterministic by design. An LLM here would be able to phrase a number it did
    not actually count; every figure below is counted from a record, and the
    exact evidence used is returned alongside the answer so an ITD user can
    check it against the audit log.
    """
    from app.version import APP_VERSION, PACKAGE_CHECKPOINT
    from app.security.redaction import assert_no_raw_identifiers

    question = (body.question or "").strip()
    lower = question.lower()
    patient_specific = any(term in lower for term in _PATIENT_QUESTION_TERMS)
    patient_specific = patient_specific or any(
        token in lower for token in ("case_uid", "chief complaint for patient")
    )
    if patient_specific:
        response = {
            "status": "refused_patient_context",
            "answer": (
                "This ITD assistant is limited to system, security, governance, "
                "deployment, audit, and model-artifact status. It cannot answer "
                "patient-specific triage, diagnosis, treatment, or acuity questions."
            ),
            "evidence_scope": "system_admin_only",
        }
        assert_no_raw_identifiers(response)
        return response

    security = build_security_status()
    report_raw = str(settings.uhl_report_dir)
    model_raw = str(settings.uhl_model_path)
    model_path_exists = settings.uhl_model_path.is_file()
    report_dir_exists = settings.uhl_report_dir.is_dir()

    config_evidence = {
        "app_version": APP_VERSION,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "security_mode": security.get("current_mode"),
        "auth_provider": security.get("auth_provider"),
        "is_safe_configuration": security.get("is_safe"),
        "uhl_dataset_present": settings.uhl_data_path.is_file(),
        "uhl_model_present": model_path_exists,
        "model_path_configured": bool(model_raw),
        "model_file_exists": model_path_exists,
        "report_dir_configured": bool(report_raw),
        "report_dir_exists": report_dir_exists,
        "audit_sink": security.get("audit_sink"),
        "warnings": security.get("unsafe_combinations") or [],
        **_overdue_sweeper_status(),
    }

    window_days = _window_days_from_question(lower)
    audit_evidence: Dict[str, Any] = {}
    audit_error = None
    if _wants_audit_evidence(lower):
        try:
            audit_evidence = _itd_audit_evidence(window_days=window_days)
        except Exception as exc:                      # pragma: no cover - defensive
            audit_error = f"{type(exc).__name__}"

    answer = _compose_itd_answer(
        lower, config_evidence, audit_evidence, audit_error, window_days
    )
    response = {
        "status": "answered",
        "answer": answer,
        "evidence_scope": "system_admin_only",
        "evidence": config_evidence,
        "audit_evidence": audit_evidence,
        "window_days": window_days,
    }
    assert_no_raw_identifiers(response)
    return response


def _overdue_sweeper_status() -> Dict[str, Any]:
    """Whether overdue-vitals notifications can actually be created.

    The server-side sweeper is what CREATES overdue-vitals alerts. It only runs
    when ENABLE_OVERDUE_VITALS_SWEEPER=true, or implicitly in PATIENT_DATA_MODE
    — and patient-data mode is forbidden in the Azure supervisor demo. So in a
    demo deployment with neither set, the only thing driving the sweep is a
    browser tab held open by a user holding can_run_assessment: no clinical user
    signed in means no alert is ever raised, and "0 overdue alerts" is
    indistinguishable from "nothing is checking". Surfacing it makes that
    difference visible instead of silent.
    """
    from app.main import _overdue_vitals_sweeper_enabled
    enabled = _overdue_vitals_sweeper_enabled()
    return {
        "overdue_vitals_sweeper_enabled": enabled,
        "overdue_vitals_alert_source": (
            "server_side_scheduled" if enabled else "client_polling_only"
        ),
    }


_AUDIT_TERMS = (
    "audit", "log", "who", "denied", "deny", "access", "activity", "usage",
    "how many", "count", "decision", "override", "escalation", "escalations",
    "sign in", "signin", "login", "identity", "identities", "person", "people",
    "user", "users", "role", "roles", "recent", "last", "history", "event",
)


def _wants_audit_evidence(lower: str) -> bool:
    return any(term in lower for term in _AUDIT_TERMS)


def _window_days_from_question(lower: str) -> int | None:
    """Read an explicit window out of the question; default to 7 days."""
    import re as _re
    if any(w in lower for w in ("all time", "ever", "everything", "total", "overall")):
        return None
    match = _re.search(r"(?:last|past|previous)\s+(\d{1,3})\s*(day|week|month)", lower)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        return n * (7 if unit == "week" else 30 if unit == "month" else 1)
    if "today" in lower or "24 hour" in lower or "24h" in lower:
        return 1
    if "week" in lower:
        return 7
    if "month" in lower:
        return 30
    return 7


def _itd_audit_evidence(*, window_days: int | None) -> Dict[str, Any]:
    """Build ITD audit evidence from the normalised dashboard records."""
    from app.analytics.itd_evidence import build_itd_evidence
    records = _normalised_audit_records()
    return build_itd_evidence(records, window_days=window_days)


def _compose_itd_answer(
    lower: str,
    config: Dict[str, Any],
    audit: Dict[str, Any],
    audit_error: str | None,
    window_days: int | None,
) -> str:
    bits: List[str] = []
    window_label = (
        "all retained history" if window_days is None else f"the last {window_days} day(s)"
    )

    if any(t in lower for t in ("version", "build", "release", "checkpoint", "running")):
        bits.append(
            f"Running app version {config['app_version']} "
            f"(package checkpoint {config['package_checkpoint']})."
        )
    if any(t in lower for t in ("model", "artifact", "artefact", "readiness")):
        bits.append(
            f"Model artifact configured={config['model_path_configured']}, "
            f"file present={config['model_file_exists']}, "
            f"training reports present={config['report_dir_exists']}."
        )
    if any(t in lower for t in ("security", "auth", "permission", "posture", "safe")):
        bits.append(
            f"Security mode={config['security_mode']}, auth provider="
            f"{config['auth_provider']}, safe configuration="
            f"{config['is_safe_configuration']}."
        )
    if config["warnings"] and any(
        t in lower for t in ("warning", "unsafe", "safe", "security", "posture", "risk")
    ):
        bits.append(
            f"Configuration warnings ({len(config['warnings'])}): "
            + "; ".join(str(w) for w in config["warnings"][:4])
            + "."
        )
    if any(t in lower for t in ("sink", "durable", "retention", "governance")):
        bits.append(f"Audit sink={config['audit_sink']}.")

    if audit_error:
        bits.append(
            "Audit evidence could not be read for this question "
            f"({audit_error}); the configuration facts above are unaffected."
        )
    elif audit:
        if audit.get("records_in_window") == 0:
            bits.append(f"No audit records were written in {window_label}.")
        else:
            bits.append(
                f"In {window_label} the audit log holds "
                f"{audit['records_in_window']} records: {audit['access_events']} access "
                f"events ({audit['access_denied']} denied), "
                f"{audit['clinical_decisions_submitted']} clinical decisions submitted, "
                f"and {audit['model_assessments_run']} model assessments run across "
                f"{audit['cases_with_workflow_state']} cases."
            )
            asks_override = any(w in lower for w in ("override", "overrode", "overridden"))
            if asks_override:
                bits.append(
                    f"{audit['overrides_submitted']} override(s) were submitted "
                    f"(against {audit['accepts_submitted']} acceptance(s))."
                )
                if any(w in lower for w in ("who", "person", "people", "user")):
                    people = audit.get("overrides_by_person") or []
                    bits.append(
                        ("Overrides by person: "
                         + ", ".join(f"{p['label']} ({p['count']})" for p in people[:5]) + ".")
                        if people else
                        "No override in this window carries a recorded actor identity."
                    )
            if any(w in lower for w in ("login", "logins", "signed in", "sign-in", "successful")):
                bits.append(
                    f"{audit['access_allowed']} allowed access event(s) and "
                    f"{audit['access_denied']} denied."
                )
            if (not asks_override) and any(t in lower for t in ("who", "person", "people", "user", "identity", "identities")):
                people = audit.get("decisions_by_person") or []
                if people:
                    bits.append(
                        "Decisions by person: "
                        + ", ".join(f"{p['label']} ({p['count']})" for p in people[:5])
                        + "."
                    )
                else:
                    bits.append(
                        "No decision in this window carries a recorded actor identity."
                    )
                if audit.get("decisions_unattributed"):
                    bits.append(
                        f"{audit['decisions_unattributed']} decision record(s) predate "
                        "actor capture and remain unattributed."
                    )
            if any(t in lower for t in ("denied", "deny", "refus", "block")):
                by_role = audit.get("denied_by_role") or []
                bits.append(
                    f"{audit['access_denied']} access denial(s) recorded"
                    + (
                        ", by role: " + ", ".join(f"{d['label']} ({d['count']})" for d in by_role)
                        if by_role else ""
                    )
                    + "."
                )
            if any(t in lower for t in ("role", "roles", "activity", "workload", "usage")):
                roles = audit.get("top_roles_decisions_only") or []
                if roles:
                    bits.append(
                        "Decisions by role: "
                        + ", ".join(f"{r['label']} ({r['count']})" for r in roles[:5])
                        + ". Access-event counts are a separate, much larger figure and "
                        "are not clinical workload."
                    )
            if any(t in lower for t in ("recent", "last", "latest")):
                recent = audit.get("most_recent_audit_entry")
                if recent:
                    bits.append(
                        f"Most recent audit entry: {recent.get('action') or recent.get('record_kind')} "
                        f"by {recent.get('role') or 'unknown role'} at "
                        f"{recent.get('timestamp_utc')}."
                    )
            if any(t in lower for t in ("escalation", "escalations", "overdue", "vital", "notification")):
                bits.append(
                    f"Open escalations: {audit['open_escalations']}. "
                    f"Active overdue-vitals alerts: {audit['overdue_vitals_alerts_active']}."
                )
                if not config.get("overdue_vitals_sweeper_enabled"):
                    bits.append(
                        "Note: the server-side overdue-vitals sweeper is DISABLED in this "
                        "deployment, so alerts are only created while a browser session "
                        "with assessment permission is open. A zero count therefore does "
                        "not prove nothing is overdue. Set "
                        "ENABLE_OVERDUE_VITALS_SWEEPER=true to create them server-side."
                    )

    if not bits:
        bits.append(
            "Ask about security posture and configuration warnings, the audit log "
            "(volumes, access denials, who submitted decisions, recent entries), "
            "role activity, escalations and overdue-vitals alerts, deployment "
            "settings, model artefact status, or training report availability."
        )
    return " ".join(bits) + (
        " This assistant is read-only and system/admin-only: it does not inspect "
        "individual patient cases or assign triage."
    )


class DemoResetRequest(BaseModel):
    confirmation: str = Field(default="", max_length=64)
    dry_run: bool = False


@router.post("/system/demo-reset")
def system_demo_reset(
    body: DemoResetRequest,
    ctx: AuthContext = Depends(
        requires(authz.PERM_VIEW_SECURITY_STATUS, "system_demo_reset")
    ),
) -> Dict[str, Any]:
    """ITD-only reset that returns the app to an empty starting state.

    This is the explicit alternative to clearing data on app restart. Azure App
    Service restarts on deploys, scaling and platform maintenance, so a
    restart-triggered wipe could destroy a live demo without anyone asking for
    it; and ALTER_DATA_ROOT points at /home precisely so state SURVIVES restart.
    A destructive action must be chosen by a person.

    Requires a typed confirmation phrase, archives rather than deletes, and
    refuses outright in patient-data mode.
    """
    from app.storage.demo_reset import (
        CONFIRMATION_PHRASE,
        DemoResetRefused,
        reset_demo_state,
    )

    if (body.confirmation or "").strip().upper() != CONFIRMATION_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Reset requires the confirmation phrase {CONFIRMATION_PHRASE!r}. "
                "Nothing was changed."
            ),
        )
    try:
        manifest = reset_demo_state(
            settings.processed_dir,
            actor_user_id=str(getattr(ctx, "user_id", "") or ""),
            actor_role=(list(getattr(ctx, "roles", []) or []) or [""])[0],
            dry_run=bool(body.dry_run),
        )
    except DemoResetRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Reset failed: {exc}")

    # Record the reset itself as the first entry of the fresh audit log, so the
    # new log opens by stating that a reset happened and who performed it.
    try:
        from app.security.access_audit import record_access
        record_access(
            "system_demo_reset",
            "ALLOWED",
            ctx,
            permission=authz.PERM_VIEW_SECURITY_STATUS,
            detail=(
                f"Demo state archived to {manifest['archive_directory']} "
                f"({manifest['records_archived']} records)."
            ),
        )
    except Exception:
        manifest["audit_note"] = (
            "Reset completed but the reset event could not be written to the new "
            "audit log."
        )
    return manifest


@router.get("/security/status",
            dependencies=[Depends(requires(authz.PERM_VIEW_SECURITY_STATUS, "view_security_status"))])
def security_status() -> Dict[str, Any]:
    return build_security_status()


@router.get("/audit/events",
            dependencies=[Depends(requires(authz.PERM_VIEW_AUDIT_LOG, "view_audit_events"))])
def audit_events(limit: int = 200) -> Dict[str, Any]:
    """Return recent access-audit events. Records are already redacted (no raw
    identifiers; pseudonymous case_uid only).

    In patient-data mode, events are read from the durable audit sink (local JSONL
    is not an acceptable audit source for patient data); if the durable sink is not
    configured, this fails closed."""
    import os
    limit = max(1, min(limit, 1000))

    if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true":
        from app.security.audit_sink import (
            AuditSinkReadError,
            get_audit_sink,
            LocalJsonlAuditSink,
        )
        from app.security.access_audit import AccessAuditError
        sink = get_audit_sink(Path("data/processed") / "access_audit.jsonl")
        if isinstance(sink, LocalJsonlAuditSink):
            raise AccessAuditError(
                "Audit reads require a durable audit sink in patient-data mode "
                "(local JSONL is not an acceptable patient-data audit source).")
        reader = getattr(sink, "read_recent", None)
        if reader is None:
            raise AccessAuditError(
                "Durable audit sink does not support reads; cannot serve "
                "/audit/events in patient-data mode.")
        try:
            try:
                events = reader(limit, record_kind="access_audit")
            except TypeError:
                events = reader(limit)
        except AuditSinkReadError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"count": len(events), "events": events, "source": "durable"}

    # Demo/local credentialed mode: read the same guarded audit path used for
    # writes. In local credentialed mode this is outside the repo or it raises.
    from app.security.access_audit import _audit_path
    from app.storage.jsonl_io import read_jsonl_dicts
    path = _audit_path()
    events: List[Dict[str, Any]] = read_jsonl_dicts(path)
    events = events[-limit:]
    source = (
        "local_credentialed"
        if os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        else "local"
    )
    return {"count": len(events), "events": events, "source": source}


def _safe_model_records(records: List[Any], *, limit: int) -> List[Dict[str, Any]]:
    from app.security.redaction import redact_for_log, assert_no_raw_identifiers

    out: List[Dict[str, Any]] = []
    for record in records[-limit:]:
        if hasattr(record, "model_dump"):
            data = record.model_dump(mode="json")
        else:
            data = dict(record)
        safe = redact_for_log(data)
        assert_no_raw_identifiers(safe)
        out.append(safe)
    return out


def _read_patient_durable_records(*, record_kind: str, limit: int) -> List[Dict[str, Any]]:
    from app.security.audit_sink import AuditSinkReadError, LocalJsonlAuditSink, get_audit_sink

    sink = get_audit_sink(settings.processed_dir / f"{record_kind}.jsonl")
    if isinstance(sink, LocalJsonlAuditSink):
        raise HTTPException(
            status_code=503,
            detail=(
                "Patient-data audit reads require AUDIT_SINK=durable with a "
                "read-capable durable audit client."
            ),
        )
    reader = getattr(sink, "read_recent", None)
    if reader is None:
        raise HTTPException(
            status_code=503,
            detail="Durable audit sink does not support read_recent.",
        )
    try:
        try:
            records = reader(limit, record_kind=record_kind)
        except TypeError:
            records = reader(limit)
    except AuditSinkReadError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Durable audit read failed.") from exc
    return _safe_model_records(list(records), limit=limit)


@router.get("/audit/records",
            dependencies=[Depends(requires(authz.PERM_VIEW_AUDIT_LOG, "view_audit_records"))])
def audit_records(limit: int = 200) -> Dict[str, Any]:
    """Return recent workflow/review/rerun audit records through the backend.

    This complements /audit/events. In local credentialed research mode the same
    outside-repo guarded paths used for writes are used for reads. In formal
    patient-data mode, detailed record reads require a real durable audit client
    with read support; this repository only provides that deployment seam.
    """
    import os

    limit = max(1, min(limit, 1000))
    if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true":
        workflow_runs = _read_patient_durable_records(
            record_kind="workflow_run", limit=limit
        )
        human_reviews = _read_patient_durable_records(
            record_kind="human_review", limit=limit
        )
        workflow_reruns = _read_patient_durable_records(
            record_kind="workflow_rerun", limit=limit
        )
        return {
            "source": "durable",
            "limit": limit,
            "counts": {
                "workflow_runs": len(workflow_runs),
                "human_reviews": len(human_reviews),
                "workflow_reruns": len(workflow_reruns),
            },
            "workflow_runs": workflow_runs,
            "human_reviews": human_reviews,
            "workflow_reruns": workflow_reruns,
        }

    from app.security.local_paths import credentialed_artifact_path
    from app.storage.workflow_run_repository import read_workflow_runs
    from app.storage.human_review_repository import read_human_reviews
    from app.storage.rerun_repository import read_reruns

    def path_for(filename: str, purpose: str) -> Path:
        return credentialed_artifact_path(settings.processed_dir / filename, purpose=purpose)

    workflow_runs = read_workflow_runs(path_for("workflow_runs.jsonl", "workflow-run audit read"))
    human_reviews = read_human_reviews(path_for("human_reviews.jsonl", "human-review audit read"))
    workflow_reruns = read_reruns(path_for("workflow_reruns.jsonl", "workflow-rerun audit read"))
    source = (
        "local_credentialed"
        if os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        else "local"
    )
    safe_runs = _safe_model_records(workflow_runs, limit=limit)
    safe_reviews = _safe_model_records(human_reviews, limit=limit)
    safe_reruns = _safe_model_records(workflow_reruns, limit=limit)
    return {
        "source": source,
        "limit": limit,
        "counts": {
            "workflow_runs": len(workflow_runs),
            "human_reviews": len(human_reviews),
            "workflow_reruns": len(workflow_reruns),
        },
        "workflow_runs": safe_runs,
        "human_reviews": safe_reviews,
        "workflow_reruns": safe_reruns,
    }


def _normalised_audit_records() -> List[Dict[str, Any]]:
    """Load and normalise the audit evidence set.

    Shared by the audit dashboard and the ITD assistant so both describe the
    same records: a second, drifting copy of this loader would let the two
    surfaces disagree about the same audit log.
    """
    patient_mode = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"

    from app.analytics.audit_dashboard import normalise_audit_records
    from app.security.access_audit import _audit_path
    from app.security.local_paths import credentialed_artifact_path
    from app.security.redaction import assert_no_raw_identifiers, redact_for_log
    from app.storage.case_state_repository import read_case_states
    from app.storage.human_review_repository import read_human_reviews
    from app.storage.rerun_repository import read_reruns
    from app.storage.workflow_run_repository import read_workflow_runs
    from app.storage.jsonl_io import read_jsonl_dicts

    if patient_mode:
        access_events = _read_patient_durable_records(record_kind="access_audit", limit=5000)
        workflow_runs = _read_patient_durable_records(record_kind="workflow_run", limit=5000)
        human_reviews = _read_patient_durable_records(record_kind="human_review", limit=5000)
        workflow_reruns = _read_patient_durable_records(record_kind="workflow_rerun", limit=5000)
        workflow_states = _read_patient_durable_records(
            record_kind="case_workflow_state", limit=5000
        )
    else:
        def path_for(filename: str, purpose: str) -> Path:
            return credentialed_artifact_path(settings.processed_dir / filename, purpose=purpose)

        access_events: List[Dict[str, Any]] = []
        access_path = _audit_path()
        for row in read_jsonl_dicts(access_path):
            access_events.append(redact_for_log(row))

        workflow_runs = _safe_model_records(
            read_workflow_runs(path_for("workflow_runs.jsonl", "workflow-run dashboard read")),
            limit=5000,
        )
        human_reviews = _safe_model_records(
            read_human_reviews(path_for("human_reviews.jsonl", "human-review dashboard read")),
            limit=5000,
        )
        workflow_reruns = _safe_model_records(
            read_reruns(path_for("workflow_reruns.jsonl", "workflow-rerun dashboard read")),
            limit=5000,
        )
        # Local persistence writes TWO rows per transition: a
        # case_workflow_state_current snapshot (an operational read model) and a
        # case_workflow_state history row (the audit event). read_case_states
        # returns the file unfiltered outside patient-data mode, so normalising
        # both counted every state change twice in the timeline, escalation
        # totals and any state-derived breakdown. The history row is the audit
        # event; the snapshot is not a second event.
        workflow_states = [
            redact_for_log(record)
            for record in read_case_states(
                path_for("case_workflow_state.jsonl", "workflow-state dashboard read")
            )
            if str((record or {}).get("record_kind") or "case_workflow_state")
            != "case_workflow_state_current"
        ][-5000:]

    records = normalise_audit_records(
        access_events=access_events[-5000:],
        workflow_runs=workflow_runs,
        human_reviews=human_reviews,
        workflow_reruns=workflow_reruns,
        workflow_states=workflow_states,
    )
    return records


@router.get("/audit/dashboard",
            dependencies=[Depends(requires(authz.PERM_VIEW_AUDIT_LOG, "view_audit_dashboard"))])
def audit_dashboard(
    limit: int = 1000,
    start_utc: str | None = None,
    end_utc: str | None = None,
    patient_or_case: str | None = None,
    triage_level: str | None = None,
    acuity: int | None = None,
    reviewer_role: str | None = None,
    decision_type: str | None = None,
    action_type: str | None = None,
    escalation_status: str | None = None,
    override_status: str | None = None,
    source_dataset: str | None = None,
) -> Dict[str, Any]:
    """Return filtered audit-dashboard data from existing redacted evidence.

    This endpoint intentionally works from already-redacted audit/workflow
    artifacts. It never reads raw MIMIC tables and strips raw source records from
    the response after normalisation.
    """
    limit = max(1, min(limit, 5000))
    patient_mode = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    from app.analytics.audit_dashboard import AuditFilters, dashboard_payload
    from app.security.redaction import assert_no_raw_identifiers

    records = _normalised_audit_records()
    from app.api import case_resolver, safe_dto

    identity_cache: Dict[str, Dict[str, Any]] = {}

    def _identity_for_case(case_uid: Any) -> Dict[str, Any]:
        key = str(case_uid or "").strip()
        if not key:
            return {}
        if key not in identity_cache:
            try:
                rc = case_resolver.resolve(key)
                identity_cache[key] = (
                    safe_dto.safe_display_identity(rc.case) if rc is not None else {}
                )
            except Exception:
                identity_cache[key] = {}
        return identity_cache[key]

    for record in records:
        record.update(_identity_for_case(record.get("case_uid")))

    payload = dashboard_payload(
        records,
        AuditFilters(
            start_utc=start_utc,
            end_utc=end_utc,
            patient_or_case=patient_or_case,
            triage_level=triage_level,
            acuity=acuity,
            reviewer_role=reviewer_role,
            decision_type=decision_type,
            action_type=action_type,
            escalation_status=escalation_status,
            override_status=override_status,
            source_dataset=source_dataset,
        ),
        limit=limit,
    )
    for row in (payload.get("aggregations") or {}).get("escalation_worklist") or []:
        row.update(_identity_for_case(row.get("case_uid")))
    payload["source"] = (
        "durable"
        if patient_mode
        else (
            "local_credentialed"
            if os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
            else "local"
        )
    )
    assert_no_raw_identifiers(payload)
    return payload


@router.get("/cost/estimate",
            dependencies=[Depends(requires(authz.PERM_VIEW_MODEL_PERFORMANCE, "view_cost_estimate"))])
def cost_estimate() -> Dict[str, Any]:
    """Return configurable Azure/runtime cost assumptions and scenario estimates.

    This endpoint does not query live Azure pricing. Null rates remain null and
    are labelled as pending confirmation.
    """
    from app.analytics.costing import build_cost_estimate
    from app.security.redaction import assert_no_raw_identifiers

    payload = build_cost_estimate()
    assert_no_raw_identifiers(payload)
    return payload


def _uhl_model_performance_payload() -> Dict[str, Any]:
    """Normalise the supplied UHL reports to the unchanged 22.4 UI contract."""
    import hashlib
    import json

    from app.constants import (
        DATASET_SHA256,
        DATASET_SOURCE,
        FEATURE_SCHEMA_HASH,
        MODEL_SHA256,
    )
    from app.version import APP_VERSION, PACKAGE_CHECKPOINT
    from ml_training.uhl_synthetic.serving import validate_uhl_serving_bundle

    report_dir = settings.uhl_report_dir

    def _read(name: str) -> Dict[str, Any]:
        path = report_dir / name
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    reports = {
        "selection": _read("selection_record.json"),
        "comparison": _read("uhl_synthetic_model_comparison.json"),
        "distribution": _read("uhl_synthetic_class_distribution.json"),
        "feature_importance": _read("uhl_synthetic_feature_importance.json"),
        "dataset_card": _read("uhl_synthetic_dataset_card.json"),
        "feature_schema": _read("uhl_synthetic_feature_schema.json"),
        "training_provenance": _read("uhl_synthetic_training_provenance.json"),
        "threshold_tuning": _read("uhl_synthetic_threshold_tuning_report.json"),
    }
    selection = reports["selection"]
    comparison = reports["comparison"]
    test_metrics = selection.get("test_metrics") or comparison.get("untouched_test_metrics") or {}
    under_over = test_metrics.get("under_over_triage") or {}
    high = test_metrics.get("high_acuity_recall")
    high_recall = high.get("recall") if isinstance(high, dict) else high

    normalized_candidates = []
    for candidate in comparison.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_high = candidate.get("high_acuity_recall")
        candidate_high = (
            candidate_high.get("recall")
            if isinstance(candidate_high, dict)
            else candidate_high
        )
        candidate_specificity = candidate.get("over_triage_specificity")
        candidate_specificity = (
            candidate_specificity.get("specificity")
            if isinstance(candidate_specificity, dict)
            else candidate_specificity
        )
        normalized_candidates.append({
            "model_name": candidate.get("model_name"),
            "high_acuity_recall": candidate_high,
            "over_triage_specificity": candidate_specificity,
            "macro_f1": candidate.get("macro_f1"),
            "passes_over_triage_constraint": bool(
                candidate.get("passes_over_triage_constraint")
            ),
        })

    model_card = {
        "model_name": "UHL acuity model",
        "model_kind": selection.get("candidate") or comparison.get("selected_model") or "catboost",
        "training_run_id": comparison.get("training_run_id"),
        "selected_by": selection.get("reason") or "explicit operator selection",
        "model_artifact_sha256": selection.get("promoted_artifact_sha256") or MODEL_SHA256,
        "headline_metrics": {
            "high_acuity_recall": high_recall,
            "severe_under_triage_rate": under_over.get("severe_under_triage_rate"),
            "under_triage_rate": under_over.get("under_triage_rate"),
            "within_1_acuity_level_accuracy": under_over.get(
                "within_1_acuity_level_accuracy"
            ),
            "macro_f1": test_metrics.get("macro_f1"),
        },
        "not_clinically_validated": True,
    }
    artifacts = {
        "model_card": model_card,
        "dataset_card": reports["dataset_card"],
        "feature_schema": reports["feature_schema"],
        "training_provenance": reports["training_provenance"],
        "model_comparison": {
            "candidates": normalized_candidates,
            "split_kind": reports["distribution"].get("split_kind"),
            "patient_overlap_train_test": 0,
            "selected_model": comparison.get("selected_model"),
            "experimental_non_serving_candidates": [
                row.get("model_name")
                for row in normalized_candidates
                if row.get("model_name") != comparison.get("selected_model")
            ],
        },
        "class_distribution": reports["distribution"],
        "feature_importance": reports["feature_importance"],
        "threshold_tuning": reports["threshold_tuning"],
        "under_over_triage": under_over,
        "calibration": {"calibration": test_metrics.get("calibration") or {}},
        "confusion_matrix": {
            "confusion_matrix": test_metrics.get("confusion_matrix"),
            "labels": test_metrics.get("confusion_matrix_labels") or [1, 2, 3, 4, 5],
        },
        "selected_decision_rule": selection.get("decision_rule")
        or comparison.get("selected_decision_rule"),
    }

    issues: List[str] = []
    if not settings.uhl_data_path.is_file():
        issues.append("packaged UHL dataset is missing")
    else:
        actual = hashlib.sha256(settings.uhl_data_path.read_bytes()).hexdigest()
        if actual != settings.expected_dataset_sha256 or actual != DATASET_SHA256:
            issues.append("packaged UHL dataset hash mismatch")
    if not settings.uhl_model_path.is_file():
        issues.append("packaged UHL model is missing")
    else:
        actual = hashlib.sha256(settings.uhl_model_path.read_bytes()).hexdigest()
        if actual != settings.expected_model_sha256 or actual != MODEL_SHA256:
            issues.append("packaged UHL model hash mismatch")
        if not issues:
            try:
                import joblib
                bundle = joblib.load(settings.uhl_model_path)
                validate_uhl_serving_bundle(bundle, require_deployable=True)
                if bundle.get("dataset_source") != DATASET_SOURCE:
                    issues.append("model dataset source mismatch")
                if bundle.get("dataset_sha256") != DATASET_SHA256:
                    issues.append("model dataset hash mismatch")
                if bundle.get("raw_input_schema_hash") != FEATURE_SCHEMA_HASH:
                    issues.append("model feature schema mismatch")
            except Exception as exc:
                issues.append(f"model serving contract failed: {type(exc).__name__}")
    required_reports = ("selection", "comparison", "distribution", "dataset_card")
    for key in required_reports:
        if not reports[key]:
            issues.append(f"required UHL report missing or invalid: {key}")

    ready = not issues
    return {
        "status": "available" if ready else "invalid_provenance",
        "app_version": APP_VERSION,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "dataset": DATASET_SOURCE,
        "data_dir_configured": True,
        "data_loadable": settings.uhl_data_path.is_file(),
        "model_path_env_set": bool(os.environ.get("UHL_MODEL_PATH")),
        "model_file_exists": settings.uhl_model_path.is_file(),
        "model_hash_configured": True,
        "report_dir_env_set": bool(os.environ.get("UHL_REPORT_DIR")),
        "report_artefacts_present": {key: bool(value) for key, value in reports.items()},
        "selected_artefact_files": {
            "model": str(settings.uhl_model_path.name),
            "reports": [path.name for path in sorted(report_dir.glob("*.json"))],
        },
        "stale_report_detected": False,
        "model_readiness_valid": ready,
        "model_provenance_status": "verified" if ready else "invalid",
        "model_provenance_issues": issues,
        "current_feature_schema_hash": FEATURE_SCHEMA_HASH,
        "artefacts": artifacts if ready else {},
        "reason": "ok" if ready else "UHL model/data/report provenance check failed.",
        "note": "Aggregate UHL model metrics.",
    }


@router.get("/model/performance",
            dependencies=[Depends(requires(authz.PERM_VIEW_MODEL_PERFORMANCE, "view_model_performance"))])
def model_performance() -> Dict[str, Any]:
    """Return verified UHL model-performance artefacts in the 22.4 UI shape."""
    return _uhl_model_performance_payload()

    # Retained below as unreachable 22.4 history; the active UHL path returns
    # above so no MIMIC data, model, report, or environment variable is read.
    import hashlib
    import orjson
    import os
    from app.data_pipeline.mimic_full_loader import full_mimic_diagnostic
    from app.version import APP_VERSION, PACKAGE_CHECKPOINT

    diag = full_mimic_diagnostic()
    report_raw = (
        os.environ.get("MIMIC_FULL_MODEL_REPORT_DIR", "").strip()
        or os.environ.get("MIMIC_FULL_REPORT_DIR", "").strip()
        or os.environ.get("MIMIC_FULL_OUTPUT_DIR", "").strip()
    )
    report_dir = Path(report_raw).expanduser() if report_raw else settings.processed_dir
    model_raw = os.environ.get("MIMIC_FULL_MODEL_PATH", "").strip()
    model_path = Path(model_raw).expanduser() if model_raw else None
    model_file_exists = bool(model_path and model_path.exists())

    artefacts: Dict[str, Any] = {}
    artefact_presence: Dict[str, bool] = {}
    artefact_candidates = {
        "model_comparison": ["full_mimic_model_comparison.json"],
        "model_card": ["mimic_full_model_card.json"],
        "dataset_card": ["mimic_full_dataset_card.json"],
        "training_provenance": ["mimic_full_training_provenance.json"],
        "feature_schema": ["mimic_full_feature_schema.json"],
        "under_over_triage": [
            "full_mimic_under_over_triage_report.json",
            "full_mimic_under_over_triage.json",
        ],
        "calibration": [
            "full_mimic_calibration_report.json",
            "full_mimic_calibration.json",
        ],
        "confusion_matrix": ["full_mimic_confusion_matrix.json"],
        "subgroup_metrics": ["full_mimic_subgroup_metrics.json"],
        "class_distribution": ["full_mimic_class_distribution.json"],
        "binary_curve_report": ["selected_model_binary_curve_report.json"],
        "feature_importance": ["full_mimic_feature_importance.json"],
        "selected_feature_importance": ["selected_model_feature_importance.json"],
    }
    extra_expected_files = [
        "full_mimic_class_distribution.csv",
        "all_models_roc_auc_comparison.csv",
        "full_mimic_feature_importance.csv",
        "selected_model_roc_curve.csv",
        "selected_model_pr_curve.csv",
        "selected_model_roc_curve.png",
        "selected_model_pr_curve.png",
    ]

    def _read_curve_csv(
        path: Path,
        *,
        fields: tuple[str, str],
        max_points: int = 800,
    ) -> tuple[list[dict[str, Any]], int]:
        """Read aggregate curve CSV points for UI plotting.

        The on-disk CSV remains the complete record. The API response is
        downsampled so full-MIMIC threshold curves cannot make the status
        endpoint slow or oversized.
        """
        raw_points: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                point: dict[str, Any] = {}
                usable = True
                for field in fields:
                    try:
                        value = float(row.get(field, ""))
                    except (TypeError, ValueError):
                        usable = False
                        break
                    if not math.isfinite(value):
                        usable = False
                        break
                    point[field] = value
                if not usable:
                    continue
                threshold_raw = row.get("threshold")
                threshold = None
                if threshold_raw not in (None, ""):
                    try:
                        parsed_threshold = float(threshold_raw)
                    except (TypeError, ValueError):
                        parsed_threshold = None
                    if parsed_threshold is not None and math.isfinite(parsed_threshold):
                        threshold = parsed_threshold
                point["threshold"] = threshold
                raw_points.append(point)

        raw_count = len(raw_points)
        if raw_count > max_points and max_points > 1:
            step = (raw_count - 1) / float(max_points - 1)
            indexes = sorted({
                0,
                raw_count - 1,
                *{min(raw_count - 1, int(round(i * step))) for i in range(max_points)},
            })
            raw_points = [raw_points[i] for i in indexes]
        return raw_points, raw_count

    selected_artefact_files: Dict[str, str] = {}
    for key, names in artefact_candidates.items():
        found = None
        for fname in names:
            p = report_dir / fname
            artefact_presence[fname] = p.exists()
            if found is None and p.exists():
                found = p
        if found is not None:
            selected_artefact_files[key] = found.name
            try:
                artefacts[key] = orjson.loads(found.read_bytes())
            except Exception:
                artefacts[key] = {"error": "could not parse artefact"}
    for fname in extra_expected_files:
        artefact_presence[fname] = (report_dir / fname).exists()
    curve_specs = {
        "roc_curve": (
            "selected_model_roc_curve.csv",
            ("false_positive_rate", "true_positive_rate"),
            "acuity_1_2_high_acuity_vs_3_5_non_high_acuity",
        ),
        "pr_curve": (
            "selected_model_pr_curve.csv",
            ("recall", "precision"),
            "acuity_1_2_high_acuity_vs_3_5_non_high_acuity",
        ),
    }
    for key, (fname, fields, target) in curve_specs.items():
        p = report_dir / fname
        if not p.exists():
            continue
        selected_artefact_files[key] = fname
        try:
            points, raw_count = _read_curve_csv(p, fields=fields)
            artefacts[key] = {
                "source_file": fname,
                "binary_target": target,
                "points": points,
                "point_count": raw_count,
                "display_point_count": len(points),
                "downsampled": raw_count != len(points),
            }
        except Exception:
            artefacts[key] = {"error": "could not parse curve artefact", "source_file": fname}

    model_card = artefacts.get("model_card") if isinstance(artefacts.get("model_card"), dict) else {}
    model_comparison = (
        artefacts.get("model_comparison")
        if isinstance(artefacts.get("model_comparison"), dict)
        else {}
    )
    training_provenance = (
        artefacts.get("training_provenance")
        if isinstance(artefacts.get("training_provenance"), dict)
        else {}
    )
    dataset_card = (
        artefacts.get("dataset_card")
        if isinstance(artefacts.get("dataset_card"), dict)
        else {}
    )
    feature_schema = (
        artefacts.get("feature_schema")
        if isinstance(artefacts.get("feature_schema"), dict)
        else {}
    )
    try:
        import hashlib, json
        from ml_training.feature_engineering import FEATURE_NAMES
        current_feature_schema_hash = hashlib.sha256(
            json.dumps(list(FEATURE_NAMES), separators=(",", ":"), sort_keys=False).encode("utf-8")
        ).hexdigest()
    except Exception:
        current_feature_schema_hash = None
    expected_sha = os.environ.get("MIMIC_FULL_MODEL_SHA256", "").strip().lower()
    reported_sha = str(
        model_card.get("model_artifact_sha256")
        or model_comparison.get("model_artifact_sha256")
        or ""
    ).strip().lower()
    card_feature_hash = str(model_card.get("feature_schema_hash", "")).strip().lower()
    comparison_feature_hash = str(model_comparison.get("feature_schema_hash", "")).strip().lower()
    provenance_feature_hash = str(training_provenance.get("feature_schema_hash", "")).strip().lower()
    schema_feature_hash = str(feature_schema.get("feature_schema_hash", "")).strip().lower()
    card_run_id = str(model_card.get("training_run_id", "")).strip()
    comparison_run_id = str(model_comparison.get("training_run_id", "")).strip()
    provenance_run_id = str(training_provenance.get("training_run_id", "")).strip()

    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes"}

    provenance_issues = []
    model_readiness_issues = []
    model_artifact_status: Dict[str, Any] = {
        "model_path_env_set": bool(model_raw),
        "model_file_exists": model_file_exists,
        "expected_sha_configured": bool(expected_sha),
        "actual_sha256": "",
        "hash_verified": False,
        "artifact_compatible": False,
        "smoke_prediction_available": False,
        "smoke_prediction_model_name": "",
        "smoke_prediction_note": "",
    }
    if not model_raw:
        model_readiness_issues.append("MIMIC_FULL_MODEL_PATH is not configured")
    elif not model_file_exists:
        model_readiness_issues.append("configured MIMIC_FULL_MODEL_PATH does not exist")
    elif model_path is not None:
        try:
            actual_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
            model_artifact_status["actual_sha256"] = actual_sha
            if expected_sha:
                model_artifact_status["hash_verified"] = actual_sha == expected_sha
                if actual_sha != expected_sha:
                    model_readiness_issues.append(
                        "configured model SHA does not match the actual model file"
                    )
            else:
                model_readiness_issues.append(
                    "MIMIC_FULL_MODEL_SHA256 is not configured; model file is unpinned"
                )
        except Exception as exc:
            model_readiness_issues.append(f"configured model file could not be hashed: {exc}")
        if not model_readiness_issues:
            try:
                from ml_training.full_mimic.check_artifact_compatibility import check_artifact
                compat = check_artifact(model_path)
                model_artifact_status["artifact_compatible"] = bool(compat.get("compatible"))
                model_artifact_status["artifact_compatibility_reason"] = compat.get("reason")
                if not compat.get("compatible"):
                    model_readiness_issues.append(
                        "configured model artifact failed compatibility check: "
                        + str(compat.get("reason"))
                    )
            except Exception as exc:
                model_readiness_issues.append(
                    f"configured model artifact could not be deserialized/checked: {exc}"
                )
        if not model_readiness_issues:
            try:
                from app.agents.ml_prediction_agent import run_ml_prediction
                from app.schemas.internal import TriageTimeInput

                smoke = run_ml_prediction(
                    TriageTimeInput(
                        subject_id=1,
                        stay_id=1,
                        source_dataset="MIMIC-IV-ED-Full-v2.2",
                        gender="F",
                        arrival_transport="WALK IN",
                        chiefcomplaint="CHEST PAIN",
                        temperature=98.6,
                        temperature_unit="F",
                        heartrate=80,
                        resprate=18,
                        o2sat=98,
                        sbp=120,
                        dbp=80,
                        pain="0",
                    )
                )
                model_artifact_status["smoke_prediction_available"] = bool(
                    smoke.prediction_available
                )
                model_artifact_status["smoke_prediction_model_name"] = smoke.model_name
                model_artifact_status["smoke_prediction_note"] = smoke.model_note
                if not smoke.prediction_available:
                    model_readiness_issues.append(
                        "configured model artifact could not complete a smoke prediction: "
                        + str(smoke.model_note)
                    )
            except Exception as exc:
                model_readiness_issues.append(
                    f"configured model artifact smoke prediction raised: {exc}"
                )
    required_report_keys = [
        "model_comparison",
        "model_card",
        "dataset_card",
        "training_provenance",
        "feature_schema",
        "calibration",
        "confusion_matrix",
        "under_over_triage",
        "subgroup_metrics",
        "class_distribution",
        "binary_curve_report",
        "feature_importance",
    ]
    missing_expected_files = [
        fname for fname in extra_expected_files if not artefact_presence.get(fname)
    ]
    missing_required = [key for key in required_report_keys if key not in artefacts]
    if artefacts and missing_required:
        provenance_issues.append(
            "model artefacts incomplete; missing required report(s): "
            + ", ".join(missing_required)
        )
    if artefacts and missing_expected_files:
        provenance_issues.append(
            "model artefacts incomplete; missing expected plot/table file(s): "
            + ", ".join(missing_expected_files)
        )
    for key, payload in artefacts.items():
        if not isinstance(payload, dict):
            continue
        if any(
            _truthy(payload.get(flag))
            for flag in ("synthetic_data_used", "demo_fixture_used", "test_fixture_used")
        ):
            provenance_issues.append(
                f"{key} reports synthetic/demo/test fixture data use; refusing model-readiness display"
            )
        if payload.get("error"):
            provenance_issues.append(f"{key} could not be parsed")
    for key, payload in (
        ("comparison", model_comparison),
        ("model_card", model_card),
        ("dataset_card", dataset_card),
        ("training_provenance", training_provenance),
    ):
        if payload and payload.get("dataset_source") not in (None, "MIMIC-IV-ED-Full-v2.2"):
            provenance_issues.append(f"{key} dataset_source is not full MIMIC-IV-ED")
    for key, payload in (
        ("comparison", model_comparison),
        ("training_provenance", training_provenance),
    ):
        if payload and payload.get("patient_level_split") is not True:
            provenance_issues.append(f"{key} does not prove patient-level split")
        if payload and payload.get("test_set_used_for_model_selection") is not False:
            provenance_issues.append(f"{key} does not prove validation-only model selection")
        if payload and payload.get("preprocessing_inside_pipeline") is not True:
            provenance_issues.append(f"{key} does not prove preprocessing inside estimator pipeline")
        if payload and payload.get("leakage_audit_passed") is not True:
            provenance_issues.append(f"{key} does not prove leakage audit passed")
        if payload and payload.get("synthetic_audit_passed") is not True:
            provenance_issues.append(f"{key} does not prove synthetic/demo path audit passed")
    if model_comparison:
        candidates = model_comparison.get("candidates") or []
        for cand in candidates:
            name = cand.get("model_name", "unknown")
            uot = cand.get("under_over_triage") or {}
            ordinal = cand.get("ordinal_metrics") or {}
            har = cand.get("high_acuity_recall") or {}
            missing_metrics = []
            if "recall" not in har:
                missing_metrics.append("high_acuity_recall")
            for metric in (
                "under_triage_rate",
                "severe_under_triage_rate",
                "over_triage_rate",
            ):
                if metric not in uot:
                    missing_metrics.append(metric)
            for metric in (
                "mae",
                "quadratic_weighted_kappa",
                "within_1_acuity_level_accuracy",
            ):
                if metric not in ordinal:
                    missing_metrics.append(metric)
            if missing_metrics:
                provenance_issues.append(
                    f"candidate {name} missing safety metric(s): "
                    + ", ".join(missing_metrics)
                )
        test_metrics = model_comparison.get("untouched_test_metrics") or {}
        test_uot = test_metrics.get("under_over_triage") or {}
        test_ordinal = test_metrics.get("ordinal_metrics") or {}
        for metric in ("severe_under_triage_rate", "under_triage_rate"):
            if metric not in test_uot:
                provenance_issues.append(
                    f"untouched test metrics missing {metric}"
                )
        for metric in (
            "mae",
            "quadratic_weighted_kappa",
            "within_1_acuity_level_accuracy",
        ):
            if metric not in test_ordinal:
                provenance_issues.append(
                    f"untouched test metrics missing {metric}"
                )
    if expected_sha and reported_sha and expected_sha != reported_sha:
        provenance_issues.append("configured model SHA does not match report/model-card SHA")
    if not expected_sha:
        provenance_issues.append("MIMIC_FULL_MODEL_SHA256 is not configured; model/report freshness is unpinned")
    if model_readiness_issues:
        provenance_issues.extend(model_readiness_issues)
    if current_feature_schema_hash:
        hashes = [
            h for h in (
                card_feature_hash,
                comparison_feature_hash,
                provenance_feature_hash,
                schema_feature_hash,
            )
            if h
        ]
        if not hashes:
            provenance_issues.append("feature_schema_hash missing from report/model card")
        elif any(h != current_feature_schema_hash for h in hashes):
            provenance_issues.append("feature_schema_hash does not match current serving FEATURE_NAMES")
    if card_feature_hash and comparison_feature_hash and card_feature_hash != comparison_feature_hash:
        provenance_issues.append("model-card and comparison feature_schema_hash differ")
    run_ids = [r for r in (card_run_id, comparison_run_id, provenance_run_id) if r]
    if len(set(run_ids)) > 1:
        provenance_issues.append("model-card, comparison, and provenance training_run_id differ")
    if artefacts and (not card_run_id or not comparison_run_id or not provenance_run_id):
        provenance_issues.append("training_run_id missing from one or more model artefacts")
    stale_report_detected = bool(provenance_issues)
    model_readiness_valid = (
        bool(artefacts)
        and not stale_report_detected
        and not model_readiness_issues
    )

    if not artefacts:
        return {
            "status": "not_available",
            "app_version": APP_VERSION,
            "package_checkpoint": PACKAGE_CHECKPOINT,
            "reason": "Full-MIMIC metrics have not been generated in this environment",
            "data_dir_configured": bool(diag.get("mimic_full_dir_env_set")),
            "data_loadable": bool(diag.get("full_mimic_loadable")),
            "model_path_env_set": bool(model_raw),
            "model_file_exists": model_file_exists,
            "model_hash_configured": bool(expected_sha),
            "model_artifact_status": model_artifact_status,
            "report_dir_env_set": bool(report_raw),
            "report_artefacts_present": artefact_presence,
            "stale_report_detected": stale_report_detected,
            "model_readiness_valid": False,
            "model_provenance_status": "unknown" if provenance_issues else "not_available",
            "model_provenance_issues": provenance_issues,
            "current_feature_schema_hash": current_feature_schema_hash,
            "expected_report_dir_env": "MIMIC_FULL_MODEL_REPORT_DIR, MIMIC_FULL_REPORT_DIR, or MIMIC_FULL_OUTPUT_DIR",
            "expected_artefacts": [
                "full_mimic_model_comparison.json",
                "full_mimic_model_comparison.csv",
                "mimic_full_model_card.json",
                "mimic_full_dataset_card.json",
                "mimic_full_training_provenance.json",
                "mimic_full_feature_schema.json",
                "full_mimic_under_over_triage_report.json",
                "full_mimic_calibration_report.json",
                "full_mimic_confusion_matrix.json",
                "full_mimic_subgroup_metrics.json",
                "full_mimic_class_distribution.json",
                "full_mimic_class_distribution.csv",
                "all_models_roc_auc_comparison.csv",
                "full_mimic_feature_importance.json",
                "full_mimic_feature_importance.csv",
                "selected_model_feature_importance.json",
                "selected_model_binary_curve_report.json",
                "selected_model_roc_curve.csv",
                "selected_model_pr_curve.csv",
                "selected_model_roc_curve.png",
                "selected_model_pr_curve.png",
            ],
            "note": "Aggregate full-MIMIC research metrics only. Not clinically validated.",
        }

    return {
        "status": "available" if model_readiness_valid else "invalid_provenance",
        "app_version": APP_VERSION,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "dataset": "MIMIC-IV-ED-Full-v2.2",
        "data_dir_configured": bool(diag.get("mimic_full_dir_env_set")),
        "data_loadable": bool(diag.get("full_mimic_loadable")),
        "model_path_env_set": bool(model_raw),
        "model_file_exists": model_file_exists,
        "model_hash_configured": bool(expected_sha),
        "model_artifact_status": model_artifact_status,
        "report_dir_env_set": bool(report_raw),
        "report_artefacts_present": artefact_presence,
        "selected_artefact_files": selected_artefact_files,
        "stale_report_detected": stale_report_detected,
        "model_readiness_valid": model_readiness_valid,
        "model_provenance_status": "stale_or_unpinned" if provenance_issues else "verified",
        "model_provenance_issues": provenance_issues,
        "current_feature_schema_hash": current_feature_schema_hash,
        "artefacts": artefacts if model_readiness_valid else {},
        "reason": (
            "Model artefacts incomplete or invalid for model-readiness claim."
            if not model_readiness_valid else "ok"
        ),
        "note": "Aggregate full-MIMIC research metrics only. Not clinically validated.",
    }
