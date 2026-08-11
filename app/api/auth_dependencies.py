"""
FastAPI authentication & authorization dependencies — the SERVER-SIDE security
boundary.

Unlike the Streamlit UI (which is presentation), the FastAPI backend is where
patient-data access, RBAC, and audit are ENFORCED server-side. Every protected
route declares a permission via `requires(PERM_...)`; the dependency:

  1. Reads the verified identity from the real request headers
     (X-MS-CLIENT-PRINCIPAL injected by Entra/App Service auth), through the same
     pluggable identity layer the rest of the app uses.
  2. FAILS CLOSED — in patient-data mode, an unauthenticated request is 401; a
     request whose role lacks the permission is 403. Demo identity is only
     permitted outside patient-data mode.
  3. AUDITS the decision (allowed/denied) to the access log.

This means the backend is safe even if the Streamlit layer is bypassed: the API
itself rejects unauthenticated / unauthorised callers.
"""
from __future__ import annotations

from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from app.security.identity import resolve_auth_context, AuthContext
from app.security import authz
from app.security.access_audit import record_access


def get_auth_context(request: Request) -> AuthContext:
    """Resolve the verified identity from the real request headers. This is where
    the backend actually reads X-MS-CLIENT-PRINCIPAL (via the trusted-header
    provider, which only trusts it when TRUSTED_AUTH_PROXY=true)."""
    return resolve_auth_context(request_headers=dict(request.headers))


def requires(permission: str, action: str | None = None) -> Callable:
    """
    Build a FastAPI dependency that enforces `permission` and audits the decision.

    Usage:
        @router.get("/triage/cases", dependencies=[Depends(requires(PERM_VIEW_CASE))])
    or, to also receive the context in the handler:
        def handler(ctx: AuthContext = Depends(requires(PERM_VIEW_CASE))): ...
    """
    act = action or permission

    def _dep(request: Request, ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        path = request.url.path
        if not ctx.authenticated:
            record_access(action=act, decision="DENIED", ctx=ctx, page=path,
                          permission=permission, detail="unauthenticated")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not authz.has_permission(ctx, permission):
            record_access(action=act, decision="DENIED", ctx=ctx, page=path,
                          permission=permission, detail="insufficient_role")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role(s) {ctx.roles or 'none'} lack permission '{permission}'.",
            )
        record_access(action=act, decision="ALLOWED", ctx=ctx, page=path,
                      permission=permission)
        return ctx

    return _dep
