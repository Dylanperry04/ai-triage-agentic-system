"""Append-only repository for workflow-rerun audit records (JSONL)."""
from __future__ import annotations
from pathlib import Path
from typing import List
import orjson
from app.schemas.rerun import WorkflowRerunRecord


def append_rerun(path: Path, record: WorkflowRerunRecord) -> None:
    """Persist a rerun record via the guarded writer (redacts identifiers; keeps
    pseudonymous case_uid; refuses local JSONL / fails closed in patient-data
    mode)."""
    from app.storage.guarded_writer import write_record
    write_record(path, record.model_dump(mode="json"), record_kind="workflow_rerun")


def read_reruns(path: Path) -> List[WorkflowRerunRecord]:
    if not path.exists():
        return []
    out: List[WorkflowRerunRecord] = []
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                out.append(WorkflowRerunRecord(**orjson.loads(line)))
    return out
