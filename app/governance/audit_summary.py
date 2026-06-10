"""
Audit summary utilities for the AI Triage Agentic System.

These utilities summarise agent audit logs for governance review.

They do not validate clinical safety.
They do not approve clinical use.
They do not assign Manchester triage categories.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from app.governance.agent_audit import load_agent_audit_records


def summarise_agent_audit_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "audit_records_found": False,
            "record_count": 0,
            "unique_run_count": 0,
            "latest_run_id": None,
            "agents": [],
            "governance_verdicts": {},
            "human_review_required_count": 0,
            "clinical_use_allowed_count": 0,
            "automated_manchester_triage_allowed_count": 0,
            "safety_flags": {},
            "clinical_safety_claim": "No clinical safety claim is made by this audit summary.",
        }

    run_ids = [record.get("run_id") for record in records if record.get("run_id")]
    latest_record = records[-1]

    agent_counts = Counter(
        str(record.get("agent_name"))
        for record in records
        if record.get("agent_name")
    )

    governance_verdict_counts = Counter(
        str(record.get("governance_verdict"))
        for record in records
        if record.get("governance_verdict")
    )

    safety_flag_counts: Counter[str] = Counter()

    for record in records:
        flags = record.get("safety_flags", [])
        if isinstance(flags, list):
            for flag in flags:
                safety_flag_counts[str(flag)] += 1

    human_review_required_count = sum(
        1 for record in records if record.get("human_review_required") is True
    )

    clinical_use_allowed_count = sum(
        1 for record in records if record.get("clinical_use_allowed") is True
    )

    automated_manchester_triage_allowed_count = sum(
        1
        for record in records
        if record.get("automated_manchester_triage_allowed") is True
    )

    return {
        "audit_records_found": True,
        "record_count": len(records),
        "unique_run_count": len(set(run_ids)),
        "latest_run_id": latest_record.get("run_id"),
        "latest_timestamp_utc": latest_record.get("timestamp_utc"),
        "latest_case_id": latest_record.get("case_id"),
        "agents": dict(agent_counts),
        "governance_verdicts": dict(governance_verdict_counts),
        "human_review_required_count": human_review_required_count,
        "clinical_use_allowed_count": clinical_use_allowed_count,
        "automated_manchester_triage_allowed_count": automated_manchester_triage_allowed_count,
        "safety_flags": dict(safety_flag_counts),
        "clinical_safety_claim": "No clinical safety claim is made by this audit summary.",
        "expected_status": {
            "clinical_use_allowed": False,
            "automated_manchester_triage_allowed": False,
            "expected_governance_verdict": "NOT_READY_FOR_CLINICAL_USE",
        },
    }


def summarise_agent_audit_file(input_path: Path) -> dict[str, Any]:
    records = load_agent_audit_records(input_path)
    return summarise_agent_audit_records(records)