"""
Final governance evidence package builder for the AI Triage Agentic System.

This module combines local governance artifacts and live Azure API evidence into
one review package.

It does not approve clinical use.
It does not validate Manchester triage.
It does not assign Manchester triage categories.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import requests


EXPECTED_CLINICAL_USE = "not_for_clinical_use"
EXPECTED_RULES_STATUS = "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
EXPECTED_GOVERNANCE_VERDICT = "NOT_READY_FOR_CLINICAL_USE"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "file_found": False,
            "path": str(path),
            "error": "File not found.",
        }

    try:
        return {
            "file_found": True,
            "path": str(path),
            "content": json.loads(path.read_text(encoding="utf-8")),
        }
    except Exception as exc:
        return {
            "file_found": True,
            "path": str(path),
            "error": str(exc),
        }


def fetch_azure_json(base_url: str, endpoint: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{endpoint}"

    try:
        response = requests.get(url, timeout=30)
        payload: Any

        try:
            payload = response.json()
        except Exception:
            payload = response.text

        return {
            "endpoint": endpoint,
            "url": url,
            "reachable": response.status_code == 200,
            "status_code": response.status_code,
            "payload": payload,
        }

    except Exception as exc:
        return {
            "endpoint": endpoint,
            "url": url,
            "reachable": False,
            "status_code": None,
            "error": str(exc),
        }


def evaluate_evidence_status(
    health_payload: dict[str, Any],
    governance_payload: dict[str, Any],
    review_queue_payload: dict[str, Any],
    policy_report: dict[str, Any],
    audit_summary_report: dict[str, Any],
) -> dict[str, Any]:
    policy_summary = policy_report.get("summary", {})
    audit_summary = audit_summary_report.get("summary", {})

    if isinstance(audit_summary, dict) and "summary" in audit_summary:
        audit_summary = audit_summary["summary"]

    clinical_use_guardrail_active = (
        health_payload.get("clinical_use") == EXPECTED_CLINICAL_USE
    )

    automated_manchester_triage_blocked = (
        health_payload.get("rules_status") == EXPECTED_RULES_STATUS
    )

    governance_not_ready_for_clinical_use = (
        governance_payload.get("governance_verdict") == EXPECTED_GOVERNANCE_VERDICT
    )

    policy_checks_passed = (
        isinstance(policy_summary, dict)
        and policy_summary.get("fail") == 0
    )

    audit_records_present = (
        isinstance(audit_summary, dict)
        and audit_summary.get("audit_records_found") is True
        and audit_summary.get("record_count", 0) > 0
    )

    audit_preserves_clinical_block = (
        isinstance(audit_summary, dict)
        and audit_summary.get("clinical_use_allowed_count") == 0
        and audit_summary.get("automated_manchester_triage_allowed_count") == 0
    )

    human_review_queue_present = (
        isinstance(review_queue_payload.get("total_missing_cases"), int)
        and isinstance(review_queue_payload.get("needs_review_count"), int)
    )

    required_controls = {
        "clinical_use_guardrail_active": clinical_use_guardrail_active,
        "automated_manchester_triage_blocked": automated_manchester_triage_blocked,
        "governance_not_ready_for_clinical_use": governance_not_ready_for_clinical_use,
        "policy_checks_passed": policy_checks_passed,
        "audit_records_present": audit_records_present,
        "audit_preserves_clinical_block": audit_preserves_clinical_block,
        "human_review_queue_present": human_review_queue_present,
    }

    all_required_controls_passed = all(required_controls.values())

    return {
        "overall_evidence_package_status": (
            "READY_FOR_RESEARCH_GOVERNANCE_REVIEW"
            if all_required_controls_passed
            else "INCOMPLETE_OR_UNSAFE_EVIDENCE_PACKAGE"
        ),
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "manchester_category_assigned": False,
        "clinical_safety_claim": "No clinical safety claim is made by this evidence package.",
        "required_controls": required_controls,
        "all_required_controls_passed": all_required_controls_passed,
        "recommended_decision": (
            "Continue research prototype only. Do not use clinically."
        ),
    }


def build_final_governance_evidence_package(
    project_root: Path,
    base_url: str,
) -> dict[str, Any]:
    processed_dir = project_root / "data" / "processed"

    local_files = {
        "policy_check_report": load_json_file(
            processed_dir / "policy_check_report.json"
        ),
        "agent_audit_summary": load_json_file(
            processed_dir / "agent_audit_summary.json"
        ),
        "responsible_ai_evidence_package": load_json_file(
            processed_dir / "responsible_ai_evidence_package.json"
        ),
        "dataset_audit_report": load_json_file(
            processed_dir / "dataset_audit_report.json"
        ),
        "missing_triage_inputs_report": load_json_file(
            processed_dir / "missing_triage_inputs_report.json"
        ),
    }

    azure_evidence = {
        "health": fetch_azure_json(base_url, "/health"),
        "governance_report": fetch_azure_json(base_url, "/governance/report"),
        "review_queue": fetch_azure_json(base_url, "/review/queue"),
        "dataset_audit": fetch_azure_json(base_url, "/audit/dataset-report"),
        "missing_triage_inputs": fetch_azure_json(
            base_url,
            "/audit/missing-triage-inputs",
        ),
    }

    health_payload = azure_evidence["health"].get("payload", {})
    governance_payload = azure_evidence["governance_report"].get("payload", {})
    review_queue_payload = azure_evidence["review_queue"].get("payload", {})

    if not isinstance(health_payload, dict):
        health_payload = {}

    if not isinstance(governance_payload, dict):
        governance_payload = {}

    if not isinstance(review_queue_payload, dict):
        review_queue_payload = {}

    policy_report = local_files["policy_check_report"].get("content", {})
    audit_summary_report = local_files["agent_audit_summary"].get("content", {})

    if not isinstance(policy_report, dict):
        policy_report = {}

    if not isinstance(audit_summary_report, dict):
        audit_summary_report = {}

    evidence_status = evaluate_evidence_status(
        health_payload=health_payload,
        governance_payload=governance_payload,
        review_queue_payload=review_queue_payload,
        policy_report=policy_report,
        audit_summary_report=audit_summary_report,
    )

    return {
        "generated_at_utc": utc_now_iso(),
        "package_name": "AI Triage Agentic System Final Governance Evidence Package",
        "package_version": "0.1",
        "project_stage": "research_prototype_public_demo_data",
        "base_url": base_url,
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "manchester_category_assigned": False,
        "clinical_safety_claim": "No clinical safety claim is made by this package.",
        "evidence_status": evidence_status,
        "local_evidence_files": local_files,
        "azure_runtime_evidence": azure_evidence,
        "limitations": [
            "This package does not validate clinical safety.",
            "This package does not approve emergency department deployment.",
            "This package does not contain clinician-approved Manchester triage rules.",
            "This package does not use UHL patient data.",
            "This package uses public demo data only at the current stage.",
            "A human clinician must remain responsible for final triage decisions.",
        ],
        "next_required_steps_before_clinical_use": [
            "Clinician-approved deterministic Manchester-style ruleset.",
            "Formal clinical safety review.",
            "Data protection and governance approval for any UHL data.",
            "Validation protocol for UHL data.",
            "Clinician-reviewed evaluation set.",
            "Red-team and unsafe-output testing.",
            "Human-in-the-loop workflow validation.",
            "Monitoring and incident-response plan.",
        ],
    }