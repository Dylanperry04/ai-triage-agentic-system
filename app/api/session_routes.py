"""Frontend session and UI-access helpers.

These endpoints let Streamlit render identity/permission state without importing
the backend security modules directly. Protected clinical actions still use their
own resource endpoints; this surface is only for presentation gating and
backend-authoritative audit of tab/page access.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth_dependencies import get_auth_context
from app.security import authz
from app.security.access_audit import record_access
from app.security.identity import (
    ALL_ROLES,
    AuthContext,
    azure_supervisor_demo_mode,
    demo_role_switcher_allowed,
    local_credentialed_research_mode,
)

router = APIRouter()


def _ctx_payload(ctx: AuthContext) -> Dict[str, Any]:
    return {
        "authenticated": bool(ctx.authenticated),
        "user_id": ctx.user_id,
        "display_name": ctx.display_name,
        "roles": list(ctx.roles or []),
        "source": ctx.source,
        "is_demo_identity": bool(ctx.is_demo_stub),
        "permissions": sorted(authz.permissions_for(ctx)),
    }


@router.get("/auth/session")
def auth_session(ctx: AuthContext = Depends(get_auth_context)) -> Dict[str, Any]:
    """Return the backend-resolved session/permission view for the frontend."""
    import os

    patient = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    auth_provider = os.environ.get("AUTH_PROVIDER", "demo").lower()
    local_research = local_credentialed_research_mode()
    azure_demo = azure_supervisor_demo_mode()
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
        "real_authentication": not bool(ctx.is_demo_stub),
        "current_mode": (
            "patient_data"
            if patient
            else "local_credentialed_research"
            if local_research
            else "azure_supervisor_demo"
            if azure_demo
            else "public_demo"
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
