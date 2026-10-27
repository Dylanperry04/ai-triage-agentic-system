"""Append-only repository for workflow-run audit records (JSONL)."""
from __future__ import annotations

from pathlib import Path
from typing import List

from app.schemas.workflow_run import WorkflowRunRecord


def append_workflow_run(path: Path, record: WorkflowRunRecord) -> None:
    """Persist a workflow-run record via the guarded writer (redacts identifiers;
    refuses local JSONL / fails closed in patient-data mode)."""
    from app.storage.guarded_writer import write_record
    write_record(path, record.model_dump(mode="json"), record_kind="workflow_run")


def read_workflow_runs(path: Path) -> List[WorkflowRunRecord]:
    if not path.exists():
        return []
    from app.storage.jsonl_io import read_jsonl_models

    return read_jsonl_models(path, WorkflowRunRecord)


def read_workflow_run(path: Path, run_id: str, case_uid: str) -> dict | None:
    """Read one exact assessment, using durable storage in patient-data mode.

    Absence from the bounded durable history is treated as unavailable; callers
    must never replace it with a freshly computed assessment.
    """
    import os
    if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true":
        from app.storage.case_state_repository import _read_patient_records
        from app.security.audit_sink import _max_audit_read_limit
        rows = _read_patient_records(path, record_kind="workflow_run", limit=_max_audit_read_limit())
    else:
        from app.security.local_paths import credentialed_artifact_path
        from app.storage.jsonl_io import read_jsonl_dicts
        path = credentialed_artifact_path(path, purpose="workflow-run explanation read")
        rows = read_jsonl_dicts(path)
    matches = [row for row in rows if row.get("workflow_run_id") == run_id
               and row.get("case_uid") == case_uid]
    # Ambiguous duplicate IDs must not silently select a different assessment.
    return matches[0] if len(matches) == 1 else None
