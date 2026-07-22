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
