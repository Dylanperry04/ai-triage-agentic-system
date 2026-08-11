"""Explicit, audited reset of demo workflow state.

Why this exists instead of "restart the app clears everything":

* ``ALTER_DATA_ROOT`` deliberately points at ``/home/data`` on Azure App Service
  precisely BECAUSE ``/home`` survives restarts — that is what made writes durable
  at all. Tying a wipe to restart would fight the mechanism that makes the app
  work.
* App Service restarts on deploys, scale events, and platform maintenance. A
  restart-triggered wipe could therefore clear a live demo mid-session, with no
  operator having asked for it. A destructive action must be something a person
  chooses, not something the platform can trigger.

Two rules this module holds to:

1. **Archive, never delete.** Files are renamed to a timestamped archive, not
   removed. A demo starts clean, and nothing that was recorded is destroyed —
   including the access-audit trail, which a system with clinical-governance
   claims must not be able to erase on request.
2. **Refuse where records are load-bearing.** Patient-data mode uses a durable
   audit sink under retention obligations; the reset fails closed there rather
   than pretending it can clear it.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Runtime artefacts a demo reset should clear so the app starts "from the top".
# access_audit.jsonl is included so analytics start clean, but like everything
# else it is ARCHIVED, never deleted.
RESETTABLE_FILENAMES = (
    "case_workflow_state.jsonl",
    "human_reviews.jsonl",
    "workflow_runs.jsonl",
    "workflow_reruns.jsonl",
    "access_audit.jsonl",
)

# Files that live under settings.processed_dir. access_audit.jsonl is resolved
# separately: the audit writer does NOT use processed_dir, it uses
# ACCESS_AUDIT_DIR or a relative "data/processed" default, so it can sit
# somewhere else entirely whenever ALTER_DATA_ROOT is set (as it is on Azure).
# Assuming processed_dir here would archive a stale copy and silently leave the
# live audit log in place.
_PROCESSED_DIR_FILENAMES = (
    "case_workflow_state.jsonl",
    "human_reviews.jsonl",
    "workflow_runs.jsonl",
    "workflow_reruns.jsonl",
)


def resolve_audit_log_path() -> Path | None:
    """Where the access audit is ACTUALLY written, per the audit writer."""
    try:
        from app.security.access_audit import _audit_path
        return _audit_path()
    except Exception:
        return None

CONFIRMATION_PHRASE = "RESET DEMO DATA"


class DemoResetRefused(RuntimeError):
    """Raised when a reset must not proceed in this deployment profile."""


def _patient_data_mode() -> bool:
    return os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"


def _real_patient_data() -> bool:
    return os.environ.get("REAL_PATIENT_DATA", "").lower() == "true"


def assert_reset_allowed() -> None:
    """Fail closed where archived records are under a retention obligation."""
    if _patient_data_mode():
        raise DemoResetRefused(
            "Reset is disabled in PATIENT_DATA_MODE: the durable audit sink is "
            "the system of record and is subject to retention requirements. "
            "Clear a demo environment instead."
        )
    if _real_patient_data():
        raise DemoResetRefused(
            "Reset is disabled when REAL_PATIENT_DATA=true. Workflow and audit "
            "records for real patients must not be cleared from the application."
        )


def reset_demo_state(
    processed_dir: Path,
    *,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Archive runtime artefacts so the app restarts from an empty state.

    Returns a manifest of exactly what was moved and where, so the action is
    reversible by hand and reviewable after the fact.
    """
    assert_reset_allowed()

    # Second-resolution timestamps collide when two resets land in the same
    # second, and Path.replace() would then overwrite the first reset's archived
    # evidence -- silently destroying the very records the archive exists to
    # preserve. Microseconds plus a short random suffix make the directory
    # unique, and the loop refuses to reuse an existing one.
    for _ in range(50):
        stamp = "{}-{}".format(
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"),
            secrets.token_hex(3),
        )
        archive_dir = Path(processed_dir) / "_archived_resets" / stamp
        if not archive_dir.exists():
            break
    else:  # pragma: no cover - 50 collisions is not reachable in practice
        raise OSError("could not allocate a unique reset archive directory")
    archived: List[Dict[str, Any]] = []
    skipped: List[str] = []

    targets: List[Path] = [Path(processed_dir) / n for n in _PROCESSED_DIR_FILENAMES]
    audit_path = resolve_audit_log_path()
    if audit_path is not None:
        targets.append(audit_path)

    for source in targets:
        name = source.name
        if not source.exists():
            skipped.append(name)
            continue
        try:
            record_count = sum(
                1 for line in source.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except OSError:
            record_count = None
        entry = {
            "filename": name,
            "records": record_count,
            "archived_to": str(archive_dir / name),
        }
        if not dry_run:
            archive_dir.mkdir(parents=True, exist_ok=True)
            source.replace(archive_dir / name)
        archived.append(entry)

    manifest = {
        "status": "dry_run" if dry_run else "reset_complete",
        "reset_at_utc": datetime.now(timezone.utc).isoformat(),
        "actor_user_id": actor_user_id or "",
        "actor_role": actor_role or "",
        "archive_directory": str(archive_dir),
        "archived": archived,
        "not_present": skipped,
        "records_archived": sum(
            e["records"] or 0 for e in archived if e["records"] is not None
        ),
        "deleted_anything": False,
        "note": (
            "Files were archived, not deleted. The previous state remains on disk "
            "under the archive directory and can be restored by moving the files "
            "back into the processed directory."
        ),
    }
    return manifest
