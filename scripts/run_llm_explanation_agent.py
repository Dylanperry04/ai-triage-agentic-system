"""
Runs the LLM Explanation Agent using verified Azure API evidence.

This script uses public-demo-style evidence from the deployed Azure FastAPI backend.
It does not use real patient data.
It does not use UHL data.
It does not assign Manchester triage.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import requests

from app.agents.llm_explanation_agent import result_to_dict, run_llm_explanation


BASE_URL = "https://ai-triage-agentic-system-afcmdbdpcsana4h3.swedencentral-01.azurewebsites.net"
DEFAULT_STAY_ID = 30115213


def get_json(path: str) -> dict[str, Any]:
    response = requests.get(f"{BASE_URL}{path}", timeout=30)
    response.raise_for_status()
    return response.json()


def build_verified_case_evidence(stay_id: int) -> dict[str, Any]:
    case = get_json(f"/triage/run/{stay_id}")
    governance = get_json("/governance/report")
    review_queue = get_json("/review/queue")

    triage_input = case.get("triage_input", {})
    data_validation = case.get("data_validation", {})
    manchester_decision = case.get("manchester_decision", {})

    return {
        "case_id": stay_id,
        "source_dataset": triage_input.get("source_dataset"),
        "chief_complaint": triage_input.get("chiefcomplaint"),
        "arrival_transport": triage_input.get("arrival_transport"),
        "data_validation_status": data_validation.get("validation_status"),
        "missing_required_fields": data_validation.get("missing_required_fields"),
        "requires_human_data_review": data_validation.get("requires_human_data_review"),
        "governance_verdict": governance.get("governance_verdict"),
        "clinical_use_status": governance.get("clinical_use_status"),
        "blocking_issues": governance.get("blocking_issues"),
        "manchester_classification_status": manchester_decision.get("classification_status"),
        "manchester_category": manchester_decision.get("category"),
        "human_review_queue": {
            "total_missing_cases": review_queue.get("total_missing_cases"),
            "reviewed_count": review_queue.get("reviewed_count"),
            "needs_review_count": review_queue.get("needs_review_count"),
        },
        "safety_instruction": (
            "No Manchester triage category may be assigned. "
            "The system is not for clinical use. Human review is required."
        ),
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    output_path = project_root / "data" / "processed" / "llm_explanation_report.json"

    stay_id = DEFAULT_STAY_ID
    evidence = build_verified_case_evidence(stay_id)

    result = run_llm_explanation(evidence)
    result_dict = result_to_dict(result)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": stay_id,
        "input_evidence": evidence,
        "llm_explanation_result": result_dict,
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "manchester_category_assigned": False,
        "clinical_safety_claim": "No clinical safety claim is made by this LLM explanation report.",
    }

    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nLLM EXPLANATION AGENT RUN")
    print("=" * 50)
    print(f"Output path: {output_path}")
    print(f"Explanation status: {result.explanation_status}")
    print(f"Model: {result.model}")
    print(f"Deployment: {result.deployment}")
    print(f"Clinical use allowed: {result.clinical_use_allowed}")
    print(f"Automated Manchester triage allowed: {result.automated_manchester_triage_allowed}")
    print(f"Manchester category assigned: {result.manchester_category_assigned}")

    print("\nEXPLANATION")
    print("=" * 50)
    print(result.explanation_text)
    print("=" * 50)

    if result.safety_failures:
        print("\nSAFETY FAILURES")
        for failure in result.safety_failures:
            print(f"- {failure}")
        return 1

    print("\nLLM Explanation Agent passed safety validation.")
    print("No clinical triage category was assigned.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())