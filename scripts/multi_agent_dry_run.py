"""
Multi-agent dry run for the AI Triage Agentic System.

This script simulates a safe multi-agent workflow using verified outputs from the
deployed Azure FastAPI backend.

It does not call an LLM.
It does not assign a Manchester triage category.
It does not produce clinical advice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests


BASE_URL = "https://ai-triage-agentic-system-afcmdbdpcsana4h3.swedencentral-01.azurewebsites.net"
DEFAULT_STAY_ID = 30115213


def get_json(path: str) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


@dataclass
class AgentResult:
    agent_name: str
    status: str
    findings: list[str]


class DatasetEvidenceAgent:
    name = "Dataset Evidence Agent"

    def run(self, stay_id: int) -> AgentResult:
        case = get_json(f"/triage/run/{stay_id}")
        triage_input = case.get("triage_input", {})
        data_validation = case.get("data_validation", {})

        findings = [
            f"Stay ID reviewed: {stay_id}",
            f"Source dataset: {triage_input.get('source_dataset')}",
            f"Chief complaint: {triage_input.get('chiefcomplaint')}",
            f"Arrival transport: {triage_input.get('arrival_transport')}",
            f"Data validation status: {data_validation.get('validation_status')}",
            f"Missing required fields: {data_validation.get('missing_required_fields')}",
        ]

        return AgentResult(
            agent_name=self.name,
            status="EVIDENCE_COLLECTED",
            findings=findings,
        )


class SafetyReviewAgent:
    name = "Safety Review Agent"

    def run(self, stay_id: int) -> AgentResult:
        case = get_json(f"/triage/run/{stay_id}")
        safety = case.get("safety_review", {})
        data_validation = case.get("data_validation", {})
        manchester = case.get("manchester_decision", {})

        findings = [
            f"Human data review required: {data_validation.get('requires_human_data_review')}",
            f"Safety flags: {safety.get('safety_flags')}",
            f"Manchester classification status: {manchester.get('classification_status')}",
            f"Manchester category assigned: {manchester.get('category')}",
            "No automated Manchester category should be assigned at this stage.",
        ]

        status = "HUMAN_REVIEW_REQUIRED" if data_validation.get("requires_human_data_review") else "NO_BLOCKING_DATA_GAP_FOUND"

        return AgentResult(
            agent_name=self.name,
            status=status,
            findings=findings,
        )


class GovernanceAgent:
    name = "Governance Agent"

    def run(self) -> AgentResult:
        governance = get_json("/governance/report")
        review_queue = get_json("/review/queue")

        findings = [
            f"Governance verdict: {governance.get('governance_verdict')}",
            f"Clinical use status: {governance.get('clinical_use_status')}",
            f"Blocking issues: {governance.get('blocking_issues')}",
            f"Missing-data review queue: total={review_queue.get('total_missing_cases')}, reviewed={review_queue.get('reviewed_count')}, needs_review={review_queue.get('needs_review_count')}",
        ]

        return AgentResult(
            agent_name=self.name,
            status=governance.get("governance_verdict", "UNKNOWN"),
            findings=findings,
        )


class SupervisorAgent:
    name = "Supervisor Agent"

    def run(self, results: list[AgentResult]) -> dict[str, Any]:
        blocking = []

        for result in results:
            if result.status in {"HUMAN_REVIEW_REQUIRED", "NOT_READY_FOR_CLINICAL_USE"}:
                blocking.append(result.agent_name)

        return {
            "supervisor_status": "REQUEST_HUMAN_REVIEW",
            "clinical_use_allowed": False,
            "automated_manchester_triage_allowed": False,
            "blocking_agents": blocking,
            "summary": (
                "The multi-agent dry run completed using verified Azure API outputs. "
                "The system remains not-for-clinical-use. No Manchester category is assigned. "
                "Human review remains required where triage inputs are missing."
            ),
            "agent_results": [
                {
                    "agent_name": result.agent_name,
                    "status": result.status,
                    "findings": result.findings,
                }
                for result in results
            ],
        }


def main() -> None:
    stay_id = DEFAULT_STAY_ID

    agents = [
        DatasetEvidenceAgent(),
        SafetyReviewAgent(),
    ]

    results = [agent.run(stay_id) for agent in agents]
    results.append(GovernanceAgent().run())

    supervisor_output = SupervisorAgent().run(results)

    print("\nMULTI-AGENT TRIAGE DRY RUN")
    print("=" * 50)
    print(json.dumps(supervisor_output, indent=2))
    print("=" * 50)
    print("Dry run complete. No clinical triage category was assigned.\n")


if __name__ == "__main__":
    main()
    