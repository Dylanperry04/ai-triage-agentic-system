"""
Guarded record writer — the single safe path for persisting workflow/review/rerun
records.

Enforces, in one place, the patient-data-safety rules the security review
requires (items D/E/F):

  - REDACTION: identifier keys (subject_id, stay_id, mrn, ...) are stripped and
    free-text scrubbed before anything is written; a final no-raw-identifier guard
    raises if one slips through. So even records whose schema still carries a
    stay_id field never persist it.
  - PATIENT-DATA MODE: local JSONL is DEMO-ONLY. In patient-data mode, local JSONL
    writes are refused and the record must go to the durable (encrypted) audit
    sink; if the durable sink is unavailable, the write FAILS CLOSED (raises) so
    the calling action does not silently proceed without a durable record.
  - PUBLIC/DEMO MODE: local JSONL is fine; a sink failure is non-fatal.

Records keep a pseudonymous case_uid (never raw stay_id) for correlation.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import orjson

from app.security.redaction import redact_for_log, assert_no_raw_identifiers


class PatientDataStorageError(RuntimeError):
    """Raised when a write would be unsafe in patient-data mode."""


def _patient_data_mode() -> bool:
    return os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"


def write_record(path: Path, record: Dict[str, Any], *, record_kind: str) -> None:
    """Persist one record safely. `record` is a plain dict (already model_dump'd).

    In patient-data mode the record goes to the durable audit sink (and raises if
    that is not wired); local JSONL is refused. In demo mode it is redacted and
    appended to local JSONL."""
    # Always redact first, regardless of mode.
    safe = redact_for_log(record)
    assert_no_raw_identifiers(safe)
    safe.setdefault("record_kind", record_kind)
    from app.security.local_paths import credentialed_artifact_path
    path = credentialed_artifact_path(path, purpose=f"{record_kind} output")

    if _patient_data_mode():
        # Local JSONL is not allowed for patient data; require the durable sink.
        from app.security.audit_sink import get_audit_sink, LocalJsonlAuditSink
        sink = get_audit_sink(path)
        if isinstance(sink, LocalJsonlAuditSink):
            raise PatientDataStorageError(
                f"Refusing to write {record_kind} to local JSONL in patient-data "
                "mode. Set AUDIT_SINK=durable with a real client."
            )
        ok = sink.write(safe)  # EncryptedDurableAuditSink raises if no client wired
        if not ok:
            raise PatientDataStorageError(
                f"Durable write of {record_kind} failed; refusing to proceed "
                "without a durable record in patient-data mode."
            )
        return

    # Public/demo mode: redacted local JSONL append.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        f.write(orjson.dumps(safe))
        f.write(b"\n")
