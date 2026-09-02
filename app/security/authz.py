"""
App-level authorization (RBAC) — the SECOND security layer.

This decides what an ALREADY-AUTHENTICATED user (resolved by identity.py behind a
real identity provider) is permitted to do. It is not a substitute for
authentication or network controls; it has meaning only on top of them.

Permissions are coarse, action-level capabilities. The matrix is intentionally
explicit and conservative (least privilege): a role gets only what its clinical /
operational function needs. Unknown/!roleless users get nothing.
"""
from __future__ import annotations

from typing import Dict, Set

from app.security.identity import (
    AuthContext, ROLE_ED_NURSE, ROLE_TRIAGE_NURSE, ROLE_ED_DOCTOR,
    ROLE_RESEARCHER, ROLE_SECURITY_ADMIN, ROLE_GOVERNANCE_AUDITOR,
)

# ── Permissions ─────────────────────────────────────────────────────────────
PERM_VIEW_CASE = "can_view_case"                      # view a clinical case (patient content)
PERM_RECORD_VITALS = "can_record_vitals"
PERM_UPDATE_VITALS = "can_update_vitals"
PERM_PROVIDE_REQUESTED_INFORMATION = "can_provide_requested_information"
PERM_RUN_TRIAGE_ASSESSMENT = "can_run_triage_assessment"
PERM_REVIEW_AI_PREDICTION = "can_review_ai_prediction"
PERM_ACCEPT_ACUITY = "can_accept_acuity"
PERM_OVERRIDE_ACUITY = "can_override_acuity"
PERM_REQUEST_INFORMATION = "can_request_information"
PERM_ESCALATE_CASE = "can_escalate_case"
PERM_REVIEW_ESCALATION = "can_review_escalation"
PERM_RESOLVE_ESCALATION = "can_resolve_escalation"
PERM_CLOSE_CASE = "can_close_case"
PERM_ACKNOWLEDGE_OVERDUE_VITALS = "can_acknowledge_overdue_vitals"
PERM_MANAGE_CLINICAL_NOTIFICATIONS = "can_manage_clinical_notifications"
PERM_VIEW_WORKFLOW_QUEUE = "can_view_workflow_queue"
PERM_ASK_CHATBOT = "can_ask_chatbot"                  # ITD-only free-text system assistant
PERM_EXPLAIN_CASE_ACUITY = "can_explain_case_acuity"  # clinician multi-agent explanation of THIS case's acuity
PERM_VIEW_AUDIT_LOG = "can_view_audit_log"
PERM_VIEW_MODEL_PERFORMANCE = "can_view_model_performance"
# Export is split so a researcher never exports identifiable patient-level data:
PERM_EXPORT_DEIDENTIFIED = "can_export_deidentified"  # aggregate / de-identified research outputs
PERM_EXPORT_IDENTIFIABLE = "can_export_identifiable"  # patient-level identifiable export (tightly held)
# Clinical content vs security/config content are separated so an infra role
# (security_admin) can be excluded from patient clinical content by default:
PERM_VIEW_CLINICAL_CONTENT = "can_view_clinical_content"  # detailed case evidence / patient content
PERM_VIEW_SECURITY_STATUS = "can_view_security_status"    # security/config status + access logs
PERM_VIEW_RETRAINING_EXPORTS = "can_view_retraining_exports"
PERM_GENERATE_RETRAINING_EXPORTS = "can_generate_retraining_exports"

# Back-compat alias: older call sites used a single export permission. Keep the
# name pointing at the de-identified export (the safer default).
PERM_EXPORT_DATA = PERM_EXPORT_DEIDENTIFIED

ALL_PERMISSIONS = {
    PERM_VIEW_CASE, PERM_RECORD_VITALS, PERM_UPDATE_VITALS,
    PERM_PROVIDE_REQUESTED_INFORMATION, PERM_RUN_TRIAGE_ASSESSMENT,
    PERM_REVIEW_AI_PREDICTION, PERM_ACCEPT_ACUITY, PERM_OVERRIDE_ACUITY,
    PERM_REQUEST_INFORMATION, PERM_ESCALATE_CASE, PERM_REVIEW_ESCALATION,
    PERM_RESOLVE_ESCALATION, PERM_CLOSE_CASE,
    PERM_ACKNOWLEDGE_OVERDUE_VITALS, PERM_MANAGE_CLINICAL_NOTIFICATIONS,
    PERM_VIEW_WORKFLOW_QUEUE, PERM_ASK_CHATBOT, PERM_EXPLAIN_CASE_ACUITY,
    PERM_VIEW_AUDIT_LOG,
    PERM_VIEW_MODEL_PERFORMANCE,
    PERM_EXPORT_DEIDENTIFIED, PERM_EXPORT_IDENTIFIABLE,
    PERM_VIEW_CLINICAL_CONTENT, PERM_VIEW_SECURITY_STATUS,
    PERM_VIEW_RETRAINING_EXPORTS, PERM_GENERATE_RETRAINING_EXPORTS,
}

# ── Role → permission matrix (least privilege; UHL-adjusted) ────────────────
# UHL-aligned clinical responsibility boundaries:
#  - ed_nurse: observations and requested information only.
#  - triage_nurse: AI-supported triage review and escalation initiation.
#  - ed_doctor: final clinical escalation authority; never routine observations.
#  - researcher: pseudonymous case list/summary, aggregate/model-performance
#    review, and de-identified export. No individual case-level assessment/chat
#    unless a separate governance-approved role grants clinical content and
#    action permissions.
#  - security_admin: displayed as ITD in the UI; full controlled-system access
#    for this research/demo environment.
#  - governance_auditor: read-only oversight — audit logs, governance evidence,
#    review history, model performance. No clinical actions, no settings changes.
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    ROLE_ED_NURSE: {
        PERM_VIEW_CASE, PERM_VIEW_CLINICAL_CONTENT, PERM_VIEW_WORKFLOW_QUEUE,
        PERM_RECORD_VITALS, PERM_UPDATE_VITALS,
        PERM_PROVIDE_REQUESTED_INFORMATION, PERM_ACKNOWLEDGE_OVERDUE_VITALS,
    },
    ROLE_TRIAGE_NURSE: {
        PERM_VIEW_CASE, PERM_VIEW_CLINICAL_CONTENT, PERM_VIEW_WORKFLOW_QUEUE,
        PERM_RUN_TRIAGE_ASSESSMENT, PERM_REVIEW_AI_PREDICTION,
        PERM_ACCEPT_ACUITY, PERM_OVERRIDE_ACUITY, PERM_REQUEST_INFORMATION,
        PERM_ESCALATE_CASE,
        PERM_EXPLAIN_CASE_ACUITY,
    },
    ROLE_ED_DOCTOR: {
        PERM_VIEW_CASE, PERM_VIEW_CLINICAL_CONTENT, PERM_VIEW_WORKFLOW_QUEUE,
        PERM_REVIEW_AI_PREDICTION, PERM_REQUEST_INFORMATION, PERM_REVIEW_ESCALATION,
        PERM_RESOLVE_ESCALATION, PERM_CLOSE_CASE, PERM_VIEW_MODEL_PERFORMANCE,
        PERM_EXPLAIN_CASE_ACUITY,
    },
    ROLE_RESEARCHER: {
        PERM_VIEW_CASE, PERM_VIEW_MODEL_PERFORMANCE, PERM_EXPORT_DEIDENTIFIED,
    },
    ROLE_SECURITY_ADMIN: set(ALL_PERMISSIONS),
    ROLE_GOVERNANCE_AUDITOR: {
        PERM_VIEW_AUDIT_LOG, PERM_VIEW_WORKFLOW_QUEUE,
        PERM_VIEW_MODEL_PERFORMANCE,
    },
}


ROLE_DISPLAY_NAMES: Dict[str, str] = {
    ROLE_ED_NURSE: "ED Nurse",
    ROLE_TRIAGE_NURSE: "Triage Nurse",
    ROLE_ED_DOCTOR: "ED Doctor",
    ROLE_RESEARCHER: "Researcher",
    ROLE_SECURITY_ADMIN: "ITD",
    ROLE_GOVERNANCE_AUDITOR: "Governance Auditor",
}

TAB_TRIAGE_REVIEW = "triage_review"
TAB_FOLLOWUP = "followup_comparison"
TAB_EXPLAINABILITY = "explainability"
TAB_REVIEW_QUEUE = "review_queue"
TAB_AUDIT_DASHBOARD = "audit_dashboard"
TAB_GOVERNANCE = "governance"
TAB_MODEL_PERFORMANCE = "model_performance"
TAB_COST_RUNTIME = "cost_runtime"
TAB_SYSTEM_STATUS = "system_status"
TAB_MAINTAINABILITY = "maintainability"
TAB_ITD_ASK = "itd_ask_tools"

# Deprecated UI tabs retained as constants for backward-compatible imports only.
# They are intentionally excluded from ALL_TABS/ROLE_VISIBLE_TABS so the
# presentation app does not render separate Follow-Up, Cost, or raw System pages.
DEPRECATED_VISIBLE_TABS = {TAB_FOLLOWUP, TAB_COST_RUNTIME, TAB_SYSTEM_STATUS}

ALL_TABS = {
    TAB_TRIAGE_REVIEW,
    TAB_EXPLAINABILITY,
    TAB_REVIEW_QUEUE,
    TAB_AUDIT_DASHBOARD,
    TAB_GOVERNANCE,
    TAB_MODEL_PERFORMANCE,
    TAB_MAINTAINABILITY,
    TAB_ITD_ASK,
}

ROLE_VISIBLE_TABS: Dict[str, Set[str]] = {
    ROLE_ED_NURSE: {
        TAB_TRIAGE_REVIEW,
        TAB_REVIEW_QUEUE,
        TAB_MAINTAINABILITY,
    },
    ROLE_TRIAGE_NURSE: {
        TAB_TRIAGE_REVIEW,
        TAB_REVIEW_QUEUE,
        TAB_EXPLAINABILITY,
        TAB_MAINTAINABILITY,
    },
    ROLE_ED_DOCTOR: {
        TAB_TRIAGE_REVIEW,
        TAB_EXPLAINABILITY,
        TAB_REVIEW_QUEUE,
        # The role already holds read-only model-performance permission from
        # the original working application. Keep the backend/UI contract
        # aligned so supporting model evidence is actually reachable.
        TAB_MODEL_PERFORMANCE,
        TAB_MAINTAINABILITY,
    },
    ROLE_RESEARCHER: {
        TAB_EXPLAINABILITY,
        TAB_MODEL_PERFORMANCE,
        TAB_MAINTAINABILITY,
    },
    ROLE_SECURITY_ADMIN: set(ALL_TABS),
    ROLE_GOVERNANCE_AUDITOR: {
        TAB_AUDIT_DASHBOARD,
        TAB_GOVERNANCE,
        TAB_MODEL_PERFORMANCE,
        TAB_MAINTAINABILITY,
    },
}


def permissions_for(ctx: AuthContext) -> Set[str]:
    """Union of permissions across the user's roles. Unauthenticated or roleless
    users get the empty set."""
    if not ctx or not ctx.authenticated:
        return set()
    perms: Set[str] = set()
    for role in ctx.roles:
        perms |= ROLE_PERMISSIONS.get(role, set())
    return perms


def role_display_name(role: str) -> str:
    return ROLE_DISPLAY_NAMES.get(role, role)


def display_roles_for(ctx: AuthContext) -> list[str]:
    if not ctx or not ctx.authenticated:
        return []
    return [role_display_name(role) for role in (ctx.roles or [])]


def visible_tabs_for(ctx: AuthContext) -> list[str]:
    if not ctx or not ctx.authenticated:
        return []
    tabs: Set[str] = set()
    for role in ctx.roles or []:
        tabs |= ROLE_VISIBLE_TABS.get(role, set())
    tabs -= DEPRECATED_VISIBLE_TABS
    return sorted(tabs)


def has_permission(ctx: AuthContext, permission: str) -> bool:
    return permission in permissions_for(ctx)


class AuthorizationError(PermissionError):
    """Raised when a user lacks a required permission."""


def require_permission(ctx: AuthContext, permission: str) -> None:
    """Enforce a permission, raising AuthorizationError if absent. Call at the
    top of every sensitive action."""
    if not has_permission(ctx, permission):
        who = ctx.user_id if (ctx and ctx.authenticated) else "unauthenticated"
        raise AuthorizationError(f"User '{who}' lacks permission '{permission}'.")


# Convenience predicates (used by the UI to show/hide and to gate actions).
def can_view_case(ctx): return has_permission(ctx, PERM_VIEW_CASE)
def can_view_clinical_content(ctx): return has_permission(ctx, PERM_VIEW_CLINICAL_CONTENT)
def can_record_vitals(ctx): return has_permission(ctx, PERM_RECORD_VITALS)
def can_update_vitals(ctx): return has_permission(ctx, PERM_UPDATE_VITALS)
def can_provide_requested_information(ctx): return has_permission(ctx, PERM_PROVIDE_REQUESTED_INFORMATION)
def can_run_assessment(ctx): return has_permission(ctx, PERM_RUN_TRIAGE_ASSESSMENT)
def can_review_ai_prediction(ctx): return has_permission(ctx, PERM_REVIEW_AI_PREDICTION)
def can_accept_acuity(ctx): return has_permission(ctx, PERM_ACCEPT_ACUITY)
def can_override_acuity(ctx): return has_permission(ctx, PERM_OVERRIDE_ACUITY)
def can_request_information(ctx): return has_permission(ctx, PERM_REQUEST_INFORMATION)
def can_escalate_case(ctx): return has_permission(ctx, PERM_ESCALATE_CASE)
def can_review_escalation(ctx): return has_permission(ctx, PERM_REVIEW_ESCALATION)
def can_resolve_escalation(ctx): return has_permission(ctx, PERM_RESOLVE_ESCALATION)
def can_close_case(ctx): return has_permission(ctx, PERM_CLOSE_CASE)
def can_acknowledge_overdue_vitals(ctx): return has_permission(ctx, PERM_ACKNOWLEDGE_OVERDUE_VITALS)
def can_view_workflow_queue(ctx): return has_permission(ctx, PERM_VIEW_WORKFLOW_QUEUE)
def can_ask_chatbot(ctx): return has_permission(ctx, PERM_ASK_CHATBOT)
def can_explain_case_acuity(ctx): return has_permission(ctx, PERM_EXPLAIN_CASE_ACUITY)
def can_view_audit_log(ctx): return has_permission(ctx, PERM_VIEW_AUDIT_LOG)
def can_view_model_performance(ctx): return has_permission(ctx, PERM_VIEW_MODEL_PERFORMANCE)
def can_view_security_status(ctx): return has_permission(ctx, PERM_VIEW_SECURITY_STATUS)
def can_export_deidentified(ctx): return has_permission(ctx, PERM_EXPORT_DEIDENTIFIED)
def can_export_identifiable(ctx): return has_permission(ctx, PERM_EXPORT_IDENTIFIABLE)
# Back-compat: generic export predicate = de-identified export.
def can_export_data(ctx): return has_permission(ctx, PERM_EXPORT_DEIDENTIFIED)
