"""
Authorization guard used by the UI: check a permission, AUDIT the decision
(allowed or denied), and return a boolean the caller uses to render/deny. This is
the single place that ties RBAC to the access audit log so every gated action is
logged consistently.
"""
from __future__ import annotations

from typing import Optional

from app.security import authz
from app.security.access_audit import record_access


def check_and_audit(ctx, permission: str, action: str, *, page: Optional[str] = None,
                    case_uid: Optional[str] = None) -> bool:
    """Return True if allowed (and audit ALLOWED); else audit DENIED and return
    False. Use to gate any sensitive action/control in the UI."""
    allowed = authz.has_permission(ctx, permission)
    record_access(
        action=action, decision="ALLOWED" if allowed else "DENIED", ctx=ctx,
        page=page, case_uid=case_uid, permission=permission,
    )
    return allowed
