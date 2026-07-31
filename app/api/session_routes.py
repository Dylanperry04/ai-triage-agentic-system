"""Frontend session and UI-access helpers.

These endpoints let Streamlit render identity/permission state without importing
the backend security modules directly. Protected clinical actions still use their
own resource endpoints; this surface is only for presentation gating and
backend-authoritative audit of tab/page access.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.api.auth_dependencies import get_auth_context, requires
from app.security import authz
from app.security.access_audit import record_access
from app.security.identity import (
    ALL_ROLES,
    AuthContext,
    azure_supervisor_demo_mode,
    demo_role_switcher_allowed,
    local_credentialed_research_mode,
    tenant_authentication_configured,
)

router = APIRouter()


def _ctx_payload(ctx: AuthContext) -> Dict[str, Any]:
    return {
        "authenticated": bool(ctx.authenticated),
        "user_id": ctx.user_id,
        "display_name": ctx.display_name,
        "roles": list(ctx.roles or []),
        "display_roles": authz.display_roles_for(ctx),
        "source": ctx.source,
        "is_demo_identity": bool(ctx.is_demo_stub),
        "tenant_validated": bool(ctx.tenant_validated),
        "permissions": sorted(authz.permissions_for(ctx)),
        "visible_tabs": authz.visible_tabs_for(ctx),
        "role_display_names": {
            role: authz.role_display_name(role)
            for role in sorted(ALL_ROLES)
        },
    }


@router.get("/auth/session")
def auth_session(ctx: AuthContext = Depends(get_auth_context)) -> Dict[str, Any]:
    """Return the backend-resolved session/permission view for the frontend."""
    import os

    patient = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    auth_provider = os.environ.get("AUTH_PROVIDER", "demo").lower()
    local_research = local_credentialed_research_mode()
    azure_demo = azure_supervisor_demo_mode()
    tenant_auth = tenant_authentication_configured()
    demo_role_switcher_available = demo_role_switcher_allowed()
    if demo_role_switcher_available and azure_demo:
        demo_role_switcher_label = (
            "Demo role selector - not real authentication (Azure supervisor demo)"
        )
    elif demo_role_switcher_available:
        demo_role_switcher_label = "Demo role selector - not real authentication"
    elif local_research:
        demo_role_switcher_label = "Role selector disabled - local credentialed research"
    else:
        demo_role_switcher_label = "Demo role selector disabled"
    if patient:
        demo_role_switcher_reason = "disabled in PATIENT_DATA_MODE"
    elif local_research:
        demo_role_switcher_reason = (
            "Role switching disabled in local credentialed research mode. "
            "To change role, set LOCAL_RESEARCH_ROLE and restart the backend."
        )
    elif os.environ.get("TRUSTED_AUTH_PROXY", "").lower() == "true":
        demo_role_switcher_reason = "disabled behind trusted authentication proxy"
    elif demo_role_switcher_available:
        demo_role_switcher_reason = "enabled for public/supervisor demo only"
    else:
        demo_role_switcher_reason = "disabled by configuration"
    return {
        **_ctx_payload(ctx),
        "all_roles": sorted(ALL_ROLES),
        "all_role_options": [
            {"role": role, "label": authz.role_display_name(role)}
            for role in sorted(ALL_ROLES)
        ],
        "demo_role_switcher_available": demo_role_switcher_available,
        "demo_role_switcher_label": demo_role_switcher_label,
        "demo_role_switcher_reason": demo_role_switcher_reason,
        "local_role_change_instruction": (
            "Set LOCAL_RESEARCH_ROLE to triage_nurse, ed_doctor, "
            "clinical_supervisor, researcher, security_admin, or "
            "governance_auditor, then restart FastAPI and Streamlit."
            if local_research
            else ""
        ),
        "azure_supervisor_demo_mode": azure_demo,
        "real_authentication": bool(ctx.authenticated and not ctx.is_demo_stub),
        "platform_logout_path": (
            "/.auth/logout?post_logout_redirect_uri=%2F"
            if tenant_auth and ctx.authenticated and not ctx.is_demo_stub
            else None
        ),
        "current_mode": (
            "patient_data"
            if patient
            else "local_credentialed_research"
            if local_research
            else "azure_tenant_supervisor_demo"
            if azure_demo and tenant_auth
            else "azure_supervisor_demo"
            if azure_demo
            else "tenant_restricted"
            if tenant_auth
            else "public_demo"
        ),
    }


@router.get("/triage", include_in_schema=False)
def tenant_gated_triage_entry(
    ctx: AuthContext = Depends(
        requires(authz.PERM_VIEW_WORKFLOW_QUEUE, "tenant_triage_entry")
    ),
) -> RedirectResponse:
    """Canonical human-facing triage endpoint.

    Azure App Service Authentication performs single-tenant Entra sign-in before
    this request reaches FastAPI. The backend then enforces the operational
    workflow permission and audits the decision before redirecting to the UI.
    """
    del ctx  # dependency result is intentionally used only for enforcement/audit
    return RedirectResponse(
        url="/",
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/auth/triage-link")
def tenant_gated_triage_link(
    request: Request,
    ctx: AuthContext = Depends(
        requires(authz.PERM_VIEW_WORKFLOW_QUEUE, "tenant_triage_link")
    ),
) -> Dict[str, Any]:
    """Tenant/auth-aware entry point for the triage UI.

    Azure App Service Authentication should enforce Microsoft Entra sign-in
    before this request reaches FastAPI. The backend still performs its own RBAC
    check, audits the request, and returns the canonical UI/API endpoints without
    exposing secrets or raw patient identifiers.
    """
    import os

    base_url = str(request.base_url).rstrip("/")
    trusted_proxy = os.environ.get("TRUSTED_AUTH_PROXY", "").lower() == "true"
    auth_required = (
        os.environ.get("AUTH_REQUIRED", "").lower() == "true"
        or os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    )
    tenant_locked = (
        trusted_proxy
        and auth_required
        and not bool(ctx.is_demo_stub)
        and bool(ctx.tenant_validated)
    )
    return {
        "status": "ok",
        "triage_url": f"{base_url}/triage",
        "application_url": f"{base_url}/",
        "triage_queue_endpoint": f"{base_url}/workflow/queue",
        "session_endpoint": f"{base_url}/auth/session",
        "authenticated": bool(ctx.authenticated),
        "tenant_locked": tenant_locked,
        "trusted_auth_proxy": trusted_proxy,
        "auth_required": auth_required,
        "tenant_validated": bool(ctx.tenant_validated),
        "auth_source": ctx.source,
        "is_demo_identity": bool(ctx.is_demo_stub),
        "user_id": ctx.user_id,
        "display_name": ctx.display_name,
        "roles": list(ctx.roles or []),
        "display_roles": authz.display_roles_for(ctx),
        "required_permission": authz.PERM_VIEW_WORKFLOW_QUEUE,
        "access_model": (
            "Microsoft Entra / App Service Authentication"
            if tenant_locked
            else "demo_or_unlocked_profile"
        ),
    }


class UiAccessCheck(BaseModel):
    permission: Optional[str] = None
    action: str = Field(max_length=128)
    page: str = Field(max_length=128)
    detail: str = Field(default="", max_length=512)


@router.post("/auth/ui-access")
def ui_access_check(
    body: UiAccessCheck,
    ctx: AuthContext = Depends(get_auth_context),
) -> Dict[str, Any]:
    """Check and audit a frontend page/tab access decision on the backend."""
    if body.permission:
        allowed = bool(ctx.authenticated and authz.has_permission(ctx, body.permission))
        detail = body.detail or ("ui_permission_allowed" if allowed else "ui_permission_denied")
    else:
        allowed = bool(ctx.authenticated)
        detail = body.detail or ("ui_access_allowed" if allowed else "ui_access_denied")
    record_access(
        action=body.action,
        decision="ALLOWED" if allowed else "DENIED",
        ctx=ctx,
        page=body.page,
        permission=body.permission,
        detail=detail,
    )
    return {
        "allowed": allowed,
        "permission": body.permission,
        "action": body.action,
        "page": body.page,
        **_ctx_payload(ctx),
    }
