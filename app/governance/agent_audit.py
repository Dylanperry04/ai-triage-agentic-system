"""
Agent audit logging for the AI Triage Agentic System.

This module records evidence from multi-agent runs.

It does not approve clinical use.
It does not validate Manchester triage.
It does not store real patient data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class AgentAuditRecord:
    run_id: str
    timestamp_utc: str
    environment: str
    case_id: int | str | None
    source_dataset: str | None
    agent_name: str
    agent_status: str
    evidence_source: str
    input_reference: dict[str, Any]
    output_summary: dict[str, Any]
    safety_flags: list[str]
    human_review_required: bool
    clinical_use_allowed: bool
    automated_manchester_triage_allowed: bool
    governance_verdict: str | None
    notes: str


def new_run_id() -> str:
    return str(uuid4())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise_safety_flags(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [str(item) for item in value]

    return [str(value)]


def append_agent_audit_record(output_path: Path, record: AgentAuditRecord) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def load_agent_audit_records(input_path: Path) -> list[dict[str, Any]]:
    if not input_path.exists():
        return []

    records: list[dict[str, Any]] = []

    with input_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    return records
