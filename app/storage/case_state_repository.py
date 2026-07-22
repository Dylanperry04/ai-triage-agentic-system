"""Persistent per-case workflow state.

Audit records remain append-only evidence of what happened. This repository is
the small, redacted state surface the app reads to decide what should be visible
after refresh/reload: accepted review, information requested, or pending
escalation.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List


_DEFAULT_WORKFLOW_READ_LIMIT = 50000
_MAX_WORKFLOW_READ_LIMIT = 50000


def append_case_state(path: Path, state: Dict[str, Any]) -> None:
    from app.storage.guarded_writer import write_record
    case_uid = str(state.get("case_uid") or "").strip()
    if case_uid:
        current = dict(state)
        current["record_kind"] = "case_workflow_state_current"
        current["state_id"] = case_uid
        # The current-state row is the operational source of truth used by queues
        # and case readback. Persist it before the append-only history record so
        # a later history-write failure cannot leave a discharged/escalated case
        # looking active.
        write_record(path, current, record_kind="case_workflow_state_current")
    write_record(path, state, record_kind="case_workflow_state")


def _patient_data_mode() -> bool:
    return os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"


def _current_record_from_state(record: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(record)
    out.pop("state_id", None)
    if out.get("record_kind") == "case_workflow_state_current":
        out["record_kind"] = "case_workflow_state"
    return out


def _read_patient_records(path: Path, *, record_kind: str, limit: int) -> List[Dict[str, Any]]:
    from app.security.audit_sink import AuditSinkReadError, LocalJsonlAuditSink, get_audit_sink
    from app.storage.guarded_writer import PatientDataStorageError

    sink = get_audit_sink(path)
    if isinstance(sink, LocalJsonlAuditSink):
        raise PatientDataStorageError(
            "Workflow-state reads require a durable audit sink in "
            "patient-data mode; local JSONL state is refused."
        )
    reader = getattr(sink, "read_recent", None)
    if reader is None:
        raise PatientDataStorageError(
            "Durable audit sink does not support read_recent; cannot read "
            "workflow state in patient-data mode."
        )
    try:
        try:
            records = reader(limit, record_kind=record_kind)
        except TypeError:
            records = reader(limit)
    except AuditSinkReadError:
        raise
    except Exception as exc:
        raise PatientDataStorageError("Durable workflow-state read failed.") from exc
    return [dict(record) for record in records]


def read_case_states(path: Path, *, limit: int = _DEFAULT_WORKFLOW_READ_LIMIT) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or _DEFAULT_WORKFLOW_READ_LIMIT), _MAX_WORKFLOW_READ_LIMIT))
    if _patient_data_mode():
        current = _read_patient_records(
            path,
            record_kind="case_workflow_state_current",
            limit=limit,
        )
        if current:
            return [_current_record_from_state(record) for record in current[-limit:]]
        records = _read_patient_records(path, record_kind="case_workflow_state", limit=limit)
        return [
            dict(record)
            for record in records[-limit:]
            if dict(record).get("record_kind") in (None, "case_workflow_state")
        ]

    from app.security.local_paths import credentialed_artifact_path
    path = credentialed_artifact_path(path, purpose="case_workflow_state read")
    if not path.exists():
        return []
    from app.storage.jsonl_io import read_jsonl_dicts

    return read_jsonl_dicts(path, limit=limit)


def latest_case_state(path: Path, case_uid: str) -> Dict[str, Any]:
    if _patient_data_mode():
        current = read_current_case_state(path, case_uid)
        if current:
            return current
    latest: Dict[str, Any] = {}
    for record in read_case_states(path):
        if record.get("case_uid") == case_uid:
            latest = record
    return latest


def read_current_case_state(path: Path, case_uid: str) -> Dict[str, Any]:
    case_uid = str(case_uid or "").strip()
    if not case_uid:
        return {}
    if _patient_data_mode():
        from app.security.audit_sink import LocalJsonlAuditSink, get_audit_sink, _from_table_entity
        from app.storage.guarded_writer import PatientDataStorageError

        sink = get_audit_sink(path)
        if isinstance(sink, LocalJsonlAuditSink):
            raise PatientDataStorageError(
                "Current workflow-state reads require a durable audit sink in "
                "patient-data mode; local JSONL state is refused."
            )
        client = getattr(sink, "client", None)
        if client is not None and hasattr(client, "get_entity"):
            try:
                try:
                    raw = client.get_entity(
                        partition_key="case_workflow_state_current",
                        row_key=case_uid,
                    )
                except TypeError:
                    raw = client.get_entity("case_workflow_state_current", case_uid)
                return _current_record_from_state(_from_table_entity(dict(raw)))
            except Exception as exc:
                if _is_not_found_exception(exc):
                    return {}
                raise PatientDataStorageError(
                    "Current workflow-state point read failed; refusing to "
                    "treat the case as having no state in patient-data mode."
                ) from exc
        for record in _read_patient_records(
            path,
            record_kind="case_workflow_state_current",
            limit=_MAX_WORKFLOW_READ_LIMIT,
        ):
            if str(record.get("case_uid") or "") == case_uid:
                return _current_record_from_state(record)
        return {}

    from app.security.local_paths import credentialed_artifact_path
    path = credentialed_artifact_path(path, purpose="current case_workflow_state read")
    if not path.exists():
        return {}
    latest: Dict[str, Any] = {}
    from app.storage.jsonl_io import read_jsonl_dicts

    for record in read_jsonl_dicts(path):
        if record.get("case_uid") == case_uid:
            latest = record
    return _current_record_from_state(latest) if latest else {}


def _is_not_found_exception(exc: Exception) -> bool:
    if isinstance(exc, KeyError):
        return True
    name = exc.__class__.__name__.lower()
    if "notfound" in name or "resourcenotfound" in name:
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    return status == 404
