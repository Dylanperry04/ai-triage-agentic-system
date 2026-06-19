"""Append-only repository for workflow-run audit records (JSONL)."""
from __future__ import annotations

from pathlib import Path
from typing import List

import orjson

from app.schemas.workflow_run import WorkflowRunRecord


def append_workflow_run(path: Path, record: WorkflowRunRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as f:
        f.write(orjson.dumps(record.model_dump(mode="json")))
        f.write(b"\n")


def read_workflow_runs(path: Path) -> List[WorkflowRunRecord]:
    if not path.exists():
        return []
    records: List[WorkflowRunRecord] = []
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                records.append(WorkflowRunRecord(**orjson.loads(line)))
    return records
