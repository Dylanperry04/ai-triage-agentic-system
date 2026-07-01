"""
Access audit log: records every security-relevant access decision (ALLOWED and
DENIED), so there is a tamper-evident-by-append trail of who did what. Distinct
from the clinical workflow/review logs. Records carry user id, role(s), action,
page, case_uid, decision, and whether a demo (non-real) identity was used.

NO RAW PATIENT IDENTIFIERS are written here — only the pseudonymous case_uid
(source_dataset:stay_id) and the action metadata. See app/security/redaction.py.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import orjson
from pydantic import BaseModel, Field


class AccessAuditRecord(BaseModel):
    timestamp_utc: str
    user_id: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    action: str                       # e.g. "run_assessment", "view_audit_log"
    page: Optional[str] = None        # which tab/page
    case_uid: Optional[str] = None    # pseudonymous; never a raw identifier
    decision: str                     # "ALLOWED" | "DENIED"
    permission: Optional[str] = None  # the permission checked
    is_demo_identity: bool = False
    auth_source: Optional[str] = None # which provider established identity
    detail: str = ""


def _audit_path() -> Path:
    # Local JSONL by default; production should point this at encrypted durable
    # storage (Azure Table/Blob/Cosmos) via AUDIT_SINK (see audit_sink.py).
    from app.security.local_paths import (
        credentialed_output_dir,
        local_credentialed_research_mode,
        assert_safe_local_credentialed_output,
    )
    base_raw = os.environ.get("ACCESS_AUDIT_DIR", "").strip()
    if local_credentialed_research_mode() and not base_raw:
        base = credentialed_output_dir()
        if base is None:
            raise AccessAuditError(
                "LOCAL_CREDENTIALED_RESEARCH requires ACCESS_AUDIT_DIR or "
                "LOCAL_CREDENTIALED_OUTPUT_DIR outside the repo."
            )
        return base / "audit" / "access_audit.jsonl"
    base = Path(base_raw or "data/processed")
    return assert_safe_local_credentialed_output(
        base / "access_audit.jsonl", purpose="ACCESS_AUDIT_DIR"
    )


class AccessAuditError(RuntimeError):
    """Raised when an audit write fails in a sensitive mode (fail closed)."""


def record_access(action: str, decision: str, ctx, *, page: Optional[str] = None,
                  case_uid: Optional[str] = None, permission: Optional[str] = None,
                  detail: str = "") -> None:
    """Append one access-audit record via the configured audit sink. The sink
    redacts identifier keys and asserts no raw identifiers leak before writing.
    Raises in patient-data or local credentialed research mode so protected
    actions do not proceed unaudited."""
    try:
        rec = AccessAuditRecord(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            user_id=getattr(ctx, "user_id", None),
            roles=list(getattr(ctx, "roles", []) or []),
            action=action, page=page, case_uid=case_uid, decision=decision,
            permission=permission,
            is_demo_identity=bool(getattr(ctx, "is_demo_stub", False)),
            auth_source=getattr(ctx, "source", None),
            detail=detail,
        )
        # Route through the sink (local JSONL by default; durable encrypted store
        # in production). The sink enforces redaction + no-raw-identifier guard.
        from app.security.audit_sink import get_audit_sink, LocalJsonlAuditSink
        patient_mode = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
        local_research_mode = os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        sensitive_mode = patient_mode or local_research_mode
        sink = get_audit_sink(_audit_path())
        if patient_mode and isinstance(sink, LocalJsonlAuditSink):
            # Local JSONL is not an acceptable audit store for patient data.
            raise AccessAuditError(
                "Refusing to audit to local JSONL in patient-data mode. "
                "Set AUDIT_SINK=durable with a real client."
            )
        ok = sink.write(rec.model_dump(mode="json"))
        if sensitive_mode and not ok:
            raise AccessAuditError(
                "Audit write failed; refusing to proceed without an "
                "audit record in a sensitive mode."
            )
    except AccessAuditError:
        # In sensitive modes, audit failure MUST propagate so the calling action
        # fails closed (it must not proceed unaudited).
        raise
    except Exception:
        # Public/demo mode: audit must never break the user action.
        if (
            os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
            or os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        ):
            raise
        # else: non-fatal in demo mode.
        return


def read_access_audit(path: Optional[Path] = None) -> List[AccessAuditRecord]:
    p = path or _audit_path()
    if not p.exists():
        return []
    out: List[AccessAuditRecord] = []
    with p.open("rb") as f:
        for line in f:
            if line.strip():
                out.append(AccessAuditRecord(**orjson.loads(line)))
    return out
