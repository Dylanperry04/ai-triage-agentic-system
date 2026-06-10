"""
Audited multi-agent dry run.

This script runs a governed multi-agent review using verified Azure API outputs
and writes an audit trail to:

data/processed/agent_audit_log.jsonl

It does not call an LLM.
It does not assign a Manchester triage category.
It does not provide clinical advice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from app.governance.agent_audit import (
    AgentAuditRecord,
    append_agent_audit_record,
    new_run_id,
    normalise_safety_flags,
    utc_now_iso,
)


BASE_URL = "https://ai-triage-agentic-system-afcmdbdpcsana4h3.swedencentral-01.azurewebsites.net"
DEFAULT_STAY_ID = 30115213


def get_json(path: str) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def write_record(
    output_path: Path,
    run_id: str,
    case_id: int,
    source_dataset: str | None,
    agent_name: str,
    agent_status: str,
    evidence_source: str,
    input_reference: dict[str, Any],
    output_summary: dict[str, Any],
    safety_flags: Any,
    human_review_required: bool,
    governance_verdict: str | None,
    notes: str,
) -> None:
    record = AgentAuditRecord(
        run_id=run_id,
        timestamp_utc=utc_now_iso(),
        environment="azure_app_service_live_api",
        case_id=case_id,
        source_dataset=source_dataset,
        agent_name=agent_name,
        agent_status=agent_status,
        evidence_source=evidence_source,
        input_reference=input_reference,
        output_summary=output_summary,
        safety_flags=normalise_safety_flags(safety_flags),
        human_review_required=human_review_required,
        clinical_use_allowed=False,
        automated_manchester_triage_allowed=False,
        governance_verdict=governance_verdict,
        notes=notes,
    )

    append_agent_audit_record(output_path, record)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    output_path = project_root / "data" / "processed" / "agent_audit_log.jsonl"

    run_id = new_run_id()
    stay_id = DEFAULT_STAY_ID

    case = get_json(f"/triage/run/{stay_id}")
    governance = get_json("/governance/report")
    review_queue = get_json("/review/queue")

    triage_input = case.get("triage_input", {})
    data_validation = case.get("data_validation", {})
    safety_review = case.get("safety_review", {})
    manchester_decision = case.get("manchester_decision", {})

    source_dataset = triage_input.get("source_dataset")
    governance_verdict = governance.get("governance_verdict")

    dataset_output = {
        "chief_complaint": triage_input.get("chiefcomplaint"),
        "arrival_transport": triage_input.get("arrival_transport"),
        "validation_status": data_validation.get("validation_status"),
        "missing_required_fields": data_validation.get("missing_required_fields"),
    }

    write_record(
        output_path=output_path,
        run_id=run_id,
        case_id=stay_id,
        source_dataset=source_dataset,
        agent_name="Dataset Evidence Agent",
        agent_status="EVIDENCE_COLLECTED",
        evidence_source=f"{BASE_URL}/triage/run/{stay_id}",
        input_reference={"stay_id": stay_id},
        output_summary=dataset_output,
        safety_flags=[],
        human_review_required=bool(data_validation.get("requires_human_data_review")),
        governance_verdict=governance_verdict,
        notes="Collected verified case evidence from deployed Azure API.",
    )

    safety_output = {
        "requires_human_data_review": data_validation.get("requires_human_data_review"),
        "validation_status": data_validation.get("validation_status"),
        "safety_flags": safety_review.get("safety_flags"),
        "manchester_classification_status": manchester_decision.get("classification_status"),
        "manchester_category": manchester_decision.get("category"),
    }

    write_record(
        output_path=output_path,
        run_id=run_id,
        case_id=stay_id,
        source_dataset=source_dataset,
        agent_name="Safety Review Agent",
        agent_status="HUMAN_REVIEW_REQUIRED"
        if data_validation.get("requires_human_data_review")
        else "NO_BLOCKING_DATA_GAP_FOUND",
        evidence_source=f"{BASE_URL}/triage/run/{stay_id}",
        input_reference={"stay_id": stay_id},
        output_summary=safety_output,
        safety_flags=safety_review.get("safety_flags"),
        human_review_required=bool(data_validation.get("requires_human_data_review")),
        governance_verdict=governance_verdict,
        notes="Reviewed missing-data and Manchester-classification guardrails.",
    )

    governance_output = {
        "governance_verdict": governance.get("governance_verdict"),
        "clinical_use_status": governance.get("clinical_use_status"),
        "blocking_issues": governance.get("blocking_issues"),
        "review_queue": {
            "total_missing_cases": review_queue.get("total_missing_cases"),
            "reviewed_count": review_queue.get("reviewed_count"),
            "needs_review_count": review_queue.get("needs_review_count"),
        },
    }

    write_record(
        output_path=output_path,
        run_id=run_id,
        case_id=stay_id,
        source_dataset=source_dataset,
        agent_name="Governance Agent",
        agent_status=str(governance.get("governance_verdict")),
        evidence_source=f"{BASE_URL}/governance/report",
        input_reference={"stay_id": stay_id},
        output_summary=governance_output,
        safety_flags=governance.get("blocking_issues"),
        human_review_required=True,
        governance_verdict=governance_verdict,
        notes="Reviewed live governance verdict and human-review queue.",
    )

    supervisor_output = {
        "supervisor_status": "REQUEST_HUMAN_REVIEW",
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "human_review_required": True,
        "not_ready_for_clinical_use": governance_verdict == "NOT_READY_FOR_CLINICAL_USE",
    }

    write_record(
        output_path=output_path,
        run_id=run_id,
        case_id=stay_id,
        source_dataset=source_dataset,
        agent_name="Supervisor Agent",
        agent_status="REQUEST_HUMAN_REVIEW",
        evidence_source="multi-agent audit synthesis",
        input_reference={"stay_id": stay_id, "run_id": run_id},
        output_summary=supervisor_output,
        safety_flags=governance.get("blocking_issues"),
        human_review_required=True,
        governance_verdict=governance_verdict,
        notes="Final non-clinical multi-agent audit decision. No Manchester category assigned.",
    )

    final_report = {
        "run_id": run_id,
        "stay_id": stay_id,
        "audit_log_path": str(output_path),
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "governance_verdict": governance_verdict,
        "records_written": 4,
    }

    print("\nAUDITED MULTI-AGENT RUN")
    print("=" * 40)
    print(json.dumps(final_report, indent=2))
    print("=" * 40)
    print("Audit run complete. No clinical triage category was assigned.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())