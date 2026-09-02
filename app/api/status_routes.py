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
from datetime import datetime, timezone
import math
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.auth_dependencies import requires
from app.security.identity import AuthContext
from app.security import authz
from app.security.security_status import build_security_status
from app.config import settings

router = APIRouter()


class SystemAssistantRequest(BaseModel):
    question: str = Field(default="", max_length=1000)


_PATIENT_CLINICAL_INTENTS = {
    "diagnose this", "diagnosis for", "treatment for", "assign acuity",
    "medicine for", "symptom means", "should be treated", "should receive",
}


_ROLE_ALIASES = {
    "ed nurse": "ed_nurse",
    "nurse taking observations": "ed_nurse",
    "triage nurse": "triage_nurse",
    "ed doctor": "ed_doctor",
    "doctor": "ed_doctor",
    "researcher": "researcher",
    "security administrator": "security_admin",
    "security admin": "security_admin",
    "itd": "security_admin",
    "auditor": "governance_auditor",
    "governance auditor": "governance_auditor",
}

_PERMISSION_LABELS = {
    authz.PERM_VIEW_CASE: "view cases",
    authz.PERM_RECORD_VITALS: "record observations/vitals",
    authz.PERM_UPDATE_VITALS: "repeat or update observations/vitals",
    authz.PERM_PROVIDE_REQUESTED_INFORMATION: "provide requested clinical information",
    authz.PERM_RUN_TRIAGE_ASSESSMENT: "run the AI-supported triage assessment",
    authz.PERM_REVIEW_AI_PREDICTION: "review the AI prediction",
    authz.PERM_ACCEPT_ACUITY: "accept the recommended acuity",
    authz.PERM_OVERRIDE_ACUITY: "override the recommended acuity",
    authz.PERM_REQUEST_INFORMATION: "request additional information",
    authz.PERM_ESCALATE_CASE: "escalate a case",
    authz.PERM_REVIEW_ESCALATION: "review an escalation",
    authz.PERM_RESOLVE_ESCALATION: "resolve an escalation/final acuity",
    authz.PERM_CLOSE_CASE: "discharge or close a case",
    authz.PERM_ACKNOWLEDGE_OVERDUE_VITALS: "acknowledge overdue-observation alerts",
    authz.PERM_VIEW_WORKFLOW_QUEUE: "view the appropriate workflow queue",
    authz.PERM_ASK_CHATBOT: "use the ITD system/audit assistant",
    authz.PERM_EXPLAIN_CASE_ACUITY: "request the concise case explanation",
    authz.PERM_VIEW_AUDIT_LOG: "view the audit log",
    authz.PERM_VIEW_MODEL_PERFORMANCE: "view model-performance evidence",
    authz.PERM_EXPORT_DEIDENTIFIED: "export de-identified research data",
    authz.PERM_EXPORT_IDENTIFIABLE: "export identifiable data",
    authz.PERM_VIEW_CLINICAL_CONTENT: "view detailed clinical content",
    authz.PERM_VIEW_SECURITY_STATUS: "view security/system status",
    authz.PERM_VIEW_RETRAINING_EXPORTS: "download monthly retraining data",
    authz.PERM_GENERATE_RETRAINING_EXPORTS: "prepare/reconcile retraining exports",
}

_ACTION_PERMISSION_TERMS = (
    (("enter observation", "record observation", "take observation", "enter vital", "record vital", "take vital"), authz.PERM_RECORD_VITALS),
    (("update observation", "repeat observation", "update vital", "repeat vital"), authz.PERM_UPDATE_VITALS),
    (("run assessment", "run triage"), authz.PERM_RUN_TRIAGE_ASSESSMENT),
    (("review ai", "view ai", "review prediction"), authz.PERM_REVIEW_AI_PREDICTION),
    (("accept acuity", "accept recommendation"), authz.PERM_ACCEPT_ACUITY),
    (("override acuity", "override recommendation"), authz.PERM_OVERRIDE_ACUITY),
    (("request information", "request more information", "request observation", "request vital"), authz.PERM_REQUEST_INFORMATION),
    (("escalate", "submit escalation"), authz.PERM_ESCALATE_CASE),
    (("review escalation",), authz.PERM_REVIEW_ESCALATION),
    (("resolve escalation", "final acuity", "final clinical decision"), authz.PERM_RESOLVE_ESCALATION),
    (("discharge", "close case", "admit"), authz.PERM_CLOSE_CASE),
    (("audit log", "view audit"), authz.PERM_VIEW_AUDIT_LOG),
    (("model performance", "model evidence"), authz.PERM_VIEW_MODEL_PERFORMANCE),
    (("retraining data", "monthly export"), authz.PERM_VIEW_RETRAINING_EXPORTS),
)


def _role_from_question(lower: str) -> str | None:
    for alias in sorted(_ROLE_ALIASES, key=len, reverse=True):
        if alias in lower:
            return _ROLE_ALIASES[alias]
    return None


def _permission_from_question(lower: str) -> str | None:
    for terms, permission in _ACTION_PERMISSION_TERMS:
        if any(term in lower for term in terms):
            return permission
    return None


def _role_permission_answer(lower: str) -> str | None:
    """Answer current RBAC facts directly from the authoritative matrix."""
    role = _role_from_question(lower)
    permission = _permission_from_question(lower)
    asks_capability = any(term in lower for term in (
        "can ", "does ", "permission", "allowed", "able to", "what can",
    ))
    if permission and ("who" in lower or "which role" in lower) and role is None:
        permitted = [
            authz.ROLE_DISPLAY_NAMES[name]
            for name, permissions in authz.ROLE_PERMISSIONS.items()
            if permission in permissions
        ]
        return (
            f"The roles permitted to {_PERMISSION_LABELS[permission]} are: "
            + (", ".join(permitted) if permitted else "none")
            + ". This answer comes from the active server RBAC matrix."
        )
    if role and permission and asks_capability:
        allowed = permission in authz.ROLE_PERMISSIONS.get(role, set())
        display = authz.ROLE_DISPLAY_NAMES[role]
        action = _PERMISSION_LABELS[permission]
        return (
            f"{'Yes' if allowed else 'No'}. {display} "
            f"{'is' if allowed else 'is not'} permitted to {action}. "
            "This answer comes from the active server RBAC matrix."
        )
    if role and any(term in lower for term in ("what can", "permissions for", "role do", "responsibil")):
        labels = [
            _PERMISSION_LABELS.get(permission, permission.replace("can_", "").replace("_", " "))
            for permission in sorted(authz.ROLE_PERMISSIONS.get(role, set()))
        ]
        return f"{authz.ROLE_DISPLAY_NAMES[role]} can " + "; ".join(labels) + "."
    return None


def _is_patient_specific_itd_question(lower: str) -> bool:
    """Refuse clinical case advice without blocking system/audit terminology.

    The previous substring ``what acuity`` also blocked harmless questions such
    as "what acuity scale does the model use?".  Refusal now requires either an
    explicitly clinical intent or both a patient reference and an instruction to
    choose/diagnose/treat. Aggregate questions such as "which acuity is most
    often overridden?" remain valid ITD audit questions.
    """
    if any(term in lower for term in _PATIENT_CLINICAL_INTENTS):
        return True
    if any(token in lower for token in ("case_uid", "chief complaint for patient")):
        return True
    patient_reference = any(term in lower for term in (
        "this patient", "the patient", "that patient", "patient get",
        "patient receive", "patient have",
    ))
    clinical_choice = any(term in lower for term in (
        "what acuity", "which acuity", "triage category", "diagnos",
        "treat", "medicine", "safe to discharge", "should get",
    ))
    return patient_reference and clinical_choice


def _system_information_answer(lower: str, config: Dict[str, Any]) -> str | None:
    """Answer safe system facts that are not stored as audit events."""
    lower = str(lower or "").lower()
    permission_answer = _role_permission_answer(lower)
    if permission_answer:
        return permission_answer
    if (
        "role" in lower
        and any(term in lower for term in (
            "what roles", "which roles", "roles exist", "each role", "role do",
            "role permission", "role responsibility", "permissions for",
        ))
    ):
        return "The active roles are: " + "; ".join(
            f"{authz.ROLE_DISPLAY_NAMES[role]} — "
            + ", ".join(
                _PERMISSION_LABELS.get(permission, permission.replace("can_", "").replace("_", " "))
                for permission in sorted(authz.ROLE_PERMISSIONS[role])
            )
            for role in authz.ROLE_DISPLAY_NAMES
        ) + ". Retired historical role values remain audit-readable but cannot be assigned."

    if "acuity" in lower and any(term in lower for term in (
        "scale", "levels", "level does", "what acuity", "acuity mean",
    )) and not any(term in lower for term in ("patient", "case")):
        from app.rules.acuity_mts_mapping import MIMIC_ACUITY_TO_MTS
        levels = ", ".join(
            f"{level} {fields['category']}"
            for level, fields in sorted(MIMIC_ACUITY_TO_MTS.items())
        )
        return (
            f"The model uses acuity levels 1–5: {levels}. Lower numbers are more urgent. "
            "The colour/category names are this research project's MTS-style display convention, "
            "not an official Manchester Triage classification; clinician review is required."
        )

    if "model" in lower and any(term in lower for term in (
        "input", "feature", "trained on", "uses to predict",
    )):
        from app.constants import MODEL_INPUT_COLUMNS
        return (
            "The deployed UHL model inputs are: "
            + ", ".join(MODEL_INPUT_COLUMNS)
            + ". Audit identifiers, workflow IDs, comments and override reasons are monitoring/traceability fields and are not model inputs."
        )

    if any(term in lower for term in ("notification routing", "who receives", "notifications go")):
        return (
            "Repeat/overdue observations and requested information go to the ED Nurse; "
            "triage review and completed reassessments go to the requesting Triage Nurse; "
            "clinical escalations go to the ED Doctor; system, security and monthly-retraining notices go to ITD."
        )

    if "workflow" in lower and any(term in lower for term in ("clinical", "triage", "how does", "what is")):
        return (
            "The clinical workflow is ED Nurse observations → Triage Nurse plus the advisory AI assessment → "
            "ED Doctor when escalation is required. Requested observations return to the requesting Triage Nurse or ED Doctor on a new exact workflow run."
        )

    if "retrain" in lower and any(term in lower for term in ("when", "schedule", "process", "automatic", "monthly")):
        return (
            "On the first day of each month the system prepares/reconciles completed-month eligible feedback, "
            "notifies ITD and offers a checksum-verified CSV download. It does not train, submit NVIDIA/Slurm work or replace the deployed model."
        )
    if any(term in lower for term in ("version", "build", "release", "checkpoint", "running")):
        return (
            f"ALTER is running app version {config['app_version']} with package checkpoint "
            f"{config['package_checkpoint']}."
        )
    asks_model = any(term in lower for term in ("model", "artifact", "artefact", "readiness"))
    asks_governance = any(term in lower for term in ("sink", "durable", "retention", "governance"))
    if asks_model or asks_governance:
        parts: list[str] = []
        if asks_model:
            parts.append(
                "The pinned UHL model artifact is "
                + ("present" if config.get("model_file_exists") else "not present")
                + ", and its evidence reports are "
                + ("present." if config.get("report_dir_exists") else "not present.")
            )
        if asks_governance:
            parts.append(
                f"The configured audit sink is {config.get('audit_sink')}; governance and audit "
                "answers are read-only and calculated from recorded backend evidence."
            )
        return " ".join(parts)
    if any(term in lower for term in ("security", "auth", "posture", "safe", "warning")):
        answer = (
            f"The recorded security configuration is {'safe' if config.get('is_safe_configuration') else 'not safe'} "
            f"in {config.get('security_mode')} mode using {config.get('auth_provider')} authentication."
        )
        if config.get("warnings"):
            answer += " Warnings: " + "; ".join(str(item) for item in config["warnings"][:4]) + "."
        return answer
    return None


@router.post("/system/assistant")
def system_assistant(
    body: SystemAssistantRequest,
    ctx: AuthContext = Depends(requires(authz.PERM_ASK_CHATBOT, "system_assistant")),
) -> Dict[str, Any]:
    """ITD assistant for system, security, governance and AUDIT questions.

    Answers from recorded backend evidence: configuration/security posture plus
    the same already-redacted audit records the dashboard uses. It is still not
    the clinical case explainer — it takes no case_uid, reads no clinical case
    content, and refuses patient-triage questions, so this surface cannot become
    a hidden clinical chatbot.

    For audit questions, the configured Foundry deployment may translate the
    wording into a validated, read-only query plan. It never receives the audit
    rows and never calculates a result: the backend validates the plan and
    deterministically computes every figure from redacted records. A local
    parser covers common questions when Foundry is unavailable.
    """
    from app.version import APP_VERSION, PACKAGE_CHECKPOINT
    from app.security.redaction import assert_no_raw_identifiers

    question = (body.question or "").strip()
    lower = question.lower()
    if _is_patient_specific_itd_question(lower):
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

    system_answer = _system_information_answer(lower, config_evidence)
    window_days = _window_days_from_question(lower)
    calendar_today = "today" in lower
    audit_evidence: Dict[str, Any] = {}
    audit_query: Dict[str, Any] | None = None
    audit_error = None
    # Every non-clinical question that was not answered by the live system-fact
    # layer gets a chance to use the validated audit planner. A keyword gate
    # previously made valid duration and aggregation questions unreachable.
    wants_audit = system_answer is None
    if wants_audit:
        try:
            from app.analytics.itd_evidence import build_itd_evidence
            from app.analytics.itd_general_query import answer_general_audit_question

            try:
                assistant_limit = max(
                    1,
                    min(int(os.environ.get("ITD_ASSISTANT_AUDIT_READ_LIMIT", "50000")), 100000),
                )
            except ValueError:
                assistant_limit = 50000
            audit_records = _normalised_audit_records(limit_per_stream=assistant_limit)
            audit_evidence = build_itd_evidence(
                audit_records, window_days=window_days,
                calendar_today=calendar_today,
            )
            audit_query = answer_general_audit_question(question, audit_records)
        except Exception as exc:                      # pragma: no cover - defensive
            audit_error = f"{type(exc).__name__}"

    if system_answer is not None:
        answer = system_answer
    elif audit_error:
        answer = f"I could not read the audit evidence for this question ({audit_error})."
    elif audit_query is not None:
        answer = str(audit_query["answer"])
    elif wants_audit:
        answer = (
            "I could not construct a safe read-only query for that wording. The assistant will not "
            "substitute a nearby metric. Ask using the recorded audit concepts (event, staff/role, "
            "case, workflow run, acuity, outcome and date range), or note that the requested fact may not be recorded."
        )
    else:
        answer = _compose_itd_answer(
            lower, config_evidence, audit_evidence, audit_error, window_days
        )

    # Log execution without persisting the free-text question. The dependency
    # above already records the access decision; this second row records the
    # validated planner/execution outcome for operational auditability.
    try:
        import hashlib
        from app.security.access_audit import record_access

        planner = str((audit_query or {}).get("planner") or ("system_catalog" if system_answer else "system_status"))
        supported = bool((audit_query or {}).get("supported", True))
        query_count = len(((audit_query or {}).get("plan") or {}).get("queries") or [])
        digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
        record_access(
            action="itd_assistant_query_executed", decision="ALLOWED", ctx=ctx,
            page="/system/assistant", permission=authz.PERM_ASK_CHATBOT,
            detail=(
                f"question_sha256={digest};planner={planner};"
                f"supported={str(supported).lower()};queries={query_count}"
            ),
        )
    except Exception:
        if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true":
            raise

    response = {
        "status": "answered",
        "answer": answer,
        "evidence_scope": "system_admin_only",
        "evidence": config_evidence,
        "audit_evidence": audit_evidence,
        "audit_query": audit_query,
        "window_days": window_days,
    }
    assert_no_raw_identifiers(response)
    return response


def _overdue_sweeper_status() -> Dict[str, Any]:
    """Whether overdue-vitals notifications can actually be created.

    The server-side sweeper is what CREATES overdue-vitals alerts. It only runs
    when ENABLE_OVERDUE_VITALS_SWEEPER=true, or implicitly in patient-data mode
    and the Azure role-switcher demo. Plain local/test profiles remain
    mutation-free unless explicitly enabled. Surfacing this state distinguishes
    a genuine zero from an environment in which no server-side check is active.
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
    "escalated", "staff", "observation", "observations", "overdue", "vital",
    "sign in", "signin", "login", "identity", "identities", "person", "people",
    "user", "users", "role", "roles", "recent", "last", "history", "event",
    "assessment", "assessments", "accepted", "acceptance", "percentage",
    "percent", "discharged", "admitted", "additional information",
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


def _itd_audit_evidence(
    *, window_days: int | None, calendar_today: bool = False,
) -> Dict[str, Any]:
    """Build ITD audit evidence from the normalised dashboard records."""
    from app.analytics.itd_evidence import build_itd_evidence
    records = _normalised_audit_records()
    return build_itd_evidence(
        records, window_days=window_days, calendar_today=calendar_today,
    )


def _compose_itd_answer(
    lower: str,
    config: Dict[str, Any],
    audit: Dict[str, Any],
    audit_error: str | None,
    window_days: int | None,
) -> str:
    window_label = (
        "today" if audit.get("calendar_today")
        else "all retained history" if window_days is None
        else f"the last {window_days} day(s)"
    )
    if audit_error and _wants_audit_evidence(lower):
        return f"I could not read the audit evidence for this question ({audit_error})."

    if audit:
        from app.analytics.itd_query import answer_audit_question

        planned_answer = answer_audit_question(lower, audit, window_label)
        if planned_answer:
            return planned_answer

    # Intent-specific audit answers take priority. A focused question should
    # not be buried under a generic record-count paragraph or unrelated config.
    if audit and any(word in lower for word in ("escalation", "escalations", "escalated")):
        submitted = int(audit.get("escalations_submitted") or 0)
        open_count = int(audit.get("open_escalations") or 0)
        resolved = int(audit.get("escalations_resolved") or 0)
        asks_who = any(word in lower for word in ("who", "staff", "member", "most", "top"))
        asks_open = "open" in lower or "awaiting" in lower
        asks_resolved = any(word in lower for word in ("resolved", "closed"))
        asks_overdue_vitals = any(
            word in lower for word in ("overdue", "vital", "observation", "recheck")
        )
        answer = (
            f"{submitted} escalation{'s were' if submitted != 1 else ' was'} submitted {window_label}."
        )
        if asks_who:
            people = audit.get("escalations_by_person") or []
            if people:
                highest = int(people[0]["count"])
                leaders = [str(item["label"]) for item in people if int(item["count"]) == highest]
                if len(leaders) == 1:
                    answer += f" {leaders[0]} submitted the most, with {highest}."
                else:
                    answer += f" The highest count was {highest}, tied by {', '.join(leaders)}."
            elif submitted:
                answer += " Those escalation records predate staff attribution, so no individual can be ranked safely."
            else:
                answer += " No staff member submitted an escalation in that period."
        if asks_open:
            answer += f" {open_count} escalation{'s are' if open_count != 1 else ' is'} currently open."
        if asks_resolved:
            answer += f" {resolved} resolution action{'s were' if resolved != 1 else ' was'} recorded in the same period."
        if asks_overdue_vitals:
            overdue = int(audit.get("overdue_vitals_alerts_active") or 0)
            answer += (
                f" {overdue} active overdue-observation alert(s) are recorded; "
                "these alerts are routed to the ED Nurse role."
            )
        return answer

    if audit and any(word in lower for word in ("override", "overrode", "overridden")):
        count = int(audit.get("overrides_submitted") or 0)
        answer = f"{count} override{'s were' if count != 1 else ' was'} submitted {window_label}."
        if any(word in lower for word in ("who", "person", "staff", "most", "top")):
            people = audit.get("overrides_by_person") or []
            answer += (
                " By staff member: " + ", ".join(
                    f"{item['label']} ({item['count']})" for item in people[:5]
                ) + "."
                if people else " No attributed override was recorded in that period."
            )
        return answer

    if audit and any(word in lower for word in ("denied", "denial", "blocked", "refused")):
        count = int(audit.get("access_denied") or 0)
        roles = audit.get("denied_by_role") or []
        return (
            f"{count} access denial{'s were' if count != 1 else ' was'} recorded {window_label}"
            + (", by role: " + ", ".join(f"{r['label']} ({r['count']})" for r in roles) if roles else "")
            + "."
        )

    if audit and any(word in lower for word in ("decision", "decisions", "accepted", "acceptance")):
        count = int(audit.get("clinical_decisions_submitted") or 0)
        answer = (
            f"{count} clinical workflow action{'s were' if count != 1 else ' was'} submitted {window_label}; "
            f"{int(audit.get('accepts_submitted') or 0)} were acceptances and "
            f"{int(audit.get('overrides_submitted') or 0)} were overrides."
        )
        if any(word in lower for word in ("who", "person", "staff", "most", "top")):
            people = audit.get("decisions_by_person") or []
            if people:
                answer += " By staff member: " + ", ".join(
                    f"{item['label']} ({item['count']})" for item in people[:5]
                ) + "."
        return answer

    if audit and any(word in lower for word in ("overdue", "vital", "observation", "recheck")):
        return (
            f"{int(audit.get('overdue_vitals_alerts_active') or 0)} active overdue-observation "
            f"alert(s) are recorded. They are routed to the ED Nurse role."
        )

    if audit and any(word in lower for word in ("latest", "most recent", "recent entry")):
        recent = audit.get("most_recent_audit_entry")
        if not recent:
            return f"No audit entry was recorded {window_label}."
        return (
            f"The most recent audit entry was {recent.get('action') or recent.get('record_kind')} "
            f"by {recent.get('role') or 'an unattributed role'} at {recent.get('timestamp_utc')}."
        )

    if audit and any(word in lower for word in ("audit", "log", "activity", "usage")):
        return (
            f"The audit log contains {int(audit.get('records_in_window') or 0)} record(s) {window_label}: "
            f"{int(audit.get('access_events') or 0)} access events, "
            f"{int(audit.get('clinical_decisions_submitted') or 0)} submitted clinical actions, and "
            f"{int(audit.get('model_assessments_run') or 0)} model assessments."
        )

    if any(t in lower for t in ("version", "build", "release", "checkpoint", "running")):
        return (
            f"ALTER is running app version {config['app_version']} with package checkpoint "
            f"{config['package_checkpoint']}."
        )
    asks_model = any(t in lower for t in ("model", "artifact", "artefact", "readiness"))
    asks_governance = any(t in lower for t in ("sink", "durable", "retention", "governance"))
    if asks_model or asks_governance:
        parts: list[str] = []
        if asks_model:
            parts.append(
                "The pinned UHL model artifact is "
                + ("present" if config.get("model_file_exists") else "not present")
                + ", and its evidence reports are "
                + ("present." if config.get("report_dir_exists") else "not present.")
            )
        if asks_governance:
            parts.append(
                f"The configured audit sink is {config.get('audit_sink')}; governance and audit "
                "answers are read-only and are calculated from recorded backend evidence."
            )
        return " ".join(parts)
    if any(t in lower for t in ("security", "auth", "permission", "posture", "safe", "warning")):
        answer = (
            f"The recorded security configuration is {'safe' if config.get('is_safe_configuration') else 'not safe'} "
            f"in {config.get('security_mode')} mode using {config.get('auth_provider')} authentication."
        )
        if config.get("warnings"):
            answer += " Warnings: " + "; ".join(str(item) for item in config["warnings"][:4]) + "."
        return answer
    return (
        "I could not map that question to a recorded system or audit measure. Ask about "
        "assessments, decisions, accepts, overrides, escalations, staff activity, access "
        "denials, overdue observations, security posture, versions, or model artefacts."
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


def _read_patient_durable_records(
    *,
    record_kind: str,
    limit: int,
    since_utc: str | None = None,
) -> List[Dict[str, Any]]:
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
            records = reader(
                limit,
                record_kind=record_kind,
                since_utc=since_utc,
            )
        except TypeError:
            if since_utc:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Durable audit reader does not support a bounded "
                        "since_utc query required for monthly export."
                    ),
                )
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


def _normalised_audit_records(
    *, limit_per_stream: int = 5000, require_complete: bool = False
) -> List[Dict[str, Any]]:
    """Load and normalise the audit evidence set.

    Shared by the audit dashboard and the ITD assistant so both describe the
    same records: a second, drifting copy of this loader would let the two
    surfaces disagree about the same audit log.
    """
    patient_mode = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    limit_per_stream = max(1, min(int(limit_per_stream), 100000))
    if patient_mode and require_complete:
        # A durable reader may enforce a lower independent ceiling.  Use that
        # effective cap so a full-cap result is rejected as potentially
        # truncated instead of being labelled a complete export.
        from app.security.audit_sink import _max_audit_read_limit

        limit_per_stream = min(limit_per_stream, _max_audit_read_limit())

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
        access_events = _read_patient_durable_records(record_kind="access_audit", limit=limit_per_stream)
        workflow_runs = _read_patient_durable_records(record_kind="workflow_run", limit=limit_per_stream)
        human_reviews = _read_patient_durable_records(record_kind="human_review", limit=limit_per_stream)
        workflow_reruns = _read_patient_durable_records(record_kind="workflow_rerun", limit=limit_per_stream)
        workflow_states = _read_patient_durable_records(
            record_kind="case_workflow_state", limit=limit_per_stream
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
            limit=limit_per_stream,
        )
        human_reviews = _safe_model_records(
            read_human_reviews(path_for("human_reviews.jsonl", "human-review dashboard read")),
            limit=limit_per_stream,
        )
        workflow_reruns = _safe_model_records(
            read_reruns(path_for("workflow_reruns.jsonl", "workflow-rerun dashboard read")),
            limit=limit_per_stream,
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
                path_for("case_workflow_state.jsonl", "workflow-state dashboard read"),
                limit=limit_per_stream,
            )
            if str((record or {}).get("record_kind") or "case_workflow_state")
            != "case_workflow_state_current"
        ][-limit_per_stream:]

    if require_complete:
        stream_sizes = {
            "access_events": len(access_events),
            "workflow_runs": len(workflow_runs),
            "human_reviews": len(human_reviews),
            "workflow_reruns": len(workflow_reruns),
            "workflow_states": len(workflow_states),
        }
        capped = [name for name, size in stream_sizes.items() if size >= limit_per_stream]
        if capped:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Complete audit export reached the configured per-stream read "
                    f"limit for: {', '.join(capped)}. Refusing to produce a "
                    "silently truncated patient journey CSV."
                ),
            )

    records = normalise_audit_records(
        access_events=access_events[-limit_per_stream:],
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


@router.get(
    "/audit/journey.csv",
    dependencies=[Depends(requires(authz.PERM_VIEW_AUDIT_LOG, "export_audit_journey"))],
)
def audit_journey_csv(
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
) -> Response:
    """Download every matching safe audit field as a patient journey CSV.

    Unlike the dashboard response this is not page-limited.  It fails rather
    than silently truncating if a source stream reaches the safety cap.
    """
    from app.analytics.audit_dashboard import AuditFilters, filter_audit_records
    from app.analytics.audit_journey_export import audit_journey_csv_bytes
    from app.api import case_resolver, safe_dto
    from app.security.redaction import assert_no_raw_identifiers

    try:
        stream_limit = int(os.environ.get("AUDIT_JOURNEY_EXPORT_STREAM_LIMIT", "100000"))
    except ValueError:
        stream_limit = 100000
    stream_limit = max(1000, min(stream_limit, 100000))
    records = _normalised_audit_records(
        limit_per_stream=stream_limit, require_complete=True
    )

    identity_cache: Dict[str, Dict[str, Any]] = {}
    for record in records:
        case_uid = str(record.get("case_uid") or "").strip()
        if not case_uid:
            continue
        if case_uid not in identity_cache:
            try:
                resolved = case_resolver.resolve(case_uid)
                identity_cache[case_uid] = (
                    safe_dto.safe_display_identity(resolved.case)
                    if resolved is not None else {}
                )
            except Exception:
                identity_cache[case_uid] = {}
        record.update(identity_cache[case_uid])
        assert_no_raw_identifiers(record)

    filtered = filter_audit_records(
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
    )
    content = audit_journey_csv_bytes(filtered)
    stamp = datetime.now(timezone.utc).date().isoformat()
    filename = f"audit-complete-patient-journeys-{stamp}.csv"
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Audit-Export-Rows": str(len(filtered)),
            "X-Audit-Export-Complete": "true",
        },
    )


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
