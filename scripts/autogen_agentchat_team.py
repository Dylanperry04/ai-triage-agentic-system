"""
AutoGen AgentChat multi-agent prototype for the AI Triage Agentic System.

This uses real AutoGen AgentChat custom agents and a RoundRobinGroupChat team.

It does not call an LLM.
It does not assign a Manchester triage category.
It does not produce clinical advice.
It uses verified evidence from the deployed Azure FastAPI backend.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Sequence

import requests
from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.conditions import MaxMessageTermination
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core import CancellationToken


BASE_URL = "https://ai-triage-agentic-system-afcmdbdpcsana4h3.swedencentral-01.azurewebsites.net"
DEFAULT_STAY_ID = 30115213


def get_json(path: str) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def pretty_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


class DatasetEvidenceAgent(BaseChatAgent):
    def __init__(self, stay_id: int):
        super().__init__(
            name="dataset_evidence_agent",
            description="Collects verified case evidence from the deployed Azure triage API.",
        )
        self.stay_id = stay_id

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        case = get_json(f"/triage/run/{self.stay_id}")

        triage_input = case.get("triage_input", {})
        data_validation = case.get("data_validation", {})

        evidence = {
            "stay_id": self.stay_id,
            "source_dataset": triage_input.get("source_dataset"),
            "chief_complaint": triage_input.get("chiefcomplaint"),
            "arrival_transport": triage_input.get("arrival_transport"),
            "validation_status": data_validation.get("validation_status"),
            "missing_required_fields": data_validation.get("missing_required_fields"),
        }

        content = (
            "AGENT: Dataset Evidence Agent\n"
            "STATUS: EVIDENCE_COLLECTED\n"
            "VERIFIED_EVIDENCE:\n"
            f"{pretty_json(evidence)}"
        )

        return Response(chat_message=TextMessage(content=content, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        return None


class SafetyReviewAgent(BaseChatAgent):
    def __init__(self, stay_id: int):
        super().__init__(
            name="safety_review_agent",
            description="Checks missing-data, safety, and no-clinical-use guardrails.",
        )
        self.stay_id = stay_id

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        case = get_json(f"/triage/run/{self.stay_id}")

        data_validation = case.get("data_validation", {})
        safety_review = case.get("safety_review", {})
        manchester_decision = case.get("manchester_decision", {})

        evidence = {
            "requires_human_data_review": data_validation.get("requires_human_data_review"),
            "validation_status": data_validation.get("validation_status"),
            "safety_flags": safety_review.get("safety_flags"),
            "manchester_classification_status": manchester_decision.get("classification_status"),
            "manchester_category": manchester_decision.get("category"),
            "automated_manchester_triage_allowed": False,
        }

        status = (
            "HUMAN_REVIEW_REQUIRED"
            if data_validation.get("requires_human_data_review")
            else "NO_BLOCKING_DATA_GAP_FOUND"
        )

        content = (
            "AGENT: Safety Review Agent\n"
            f"STATUS: {status}\n"
            "SAFETY_EVIDENCE:\n"
            f"{pretty_json(evidence)}\n"
            "CONTROL: No automated Manchester triage category is permitted."
        )

        return Response(chat_message=TextMessage(content=content, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        return None


class GovernanceAgent(BaseChatAgent):
    def __init__(self):
        super().__init__(
            name="governance_agent",
            description="Reviews Responsible AI governance status from the deployed Azure API.",
        )

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        governance = get_json("/governance/report")
        review_queue = get_json("/review/queue")

        evidence = {
            "governance_verdict": governance.get("governance_verdict"),
            "clinical_use_status": governance.get("clinical_use_status"),
            "blocking_issues": governance.get("blocking_issues"),
            "human_review_queue": {
                "total_missing_cases": review_queue.get("total_missing_cases"),
                "reviewed_count": review_queue.get("reviewed_count"),
                "needs_review_count": review_queue.get("needs_review_count"),
            },
        }

        content = (
            "AGENT: Governance Agent\n"
            f"STATUS: {governance.get('governance_verdict')}\n"
            "GOVERNANCE_EVIDENCE:\n"
            f"{pretty_json(evidence)}"
        )

        return Response(chat_message=TextMessage(content=content, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        return None


class SupervisorAgent(BaseChatAgent):
    def __init__(self):
        super().__init__(
            name="supervisor_agent",
            description="Combines agent outputs into a final non-clinical review decision.",
        )
        self._history: list[BaseChatMessage] = []

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        self._history.extend(messages)

        full_history = "\n\n".join(
            message.content for message in self._history if isinstance(message.content, str)
        )

        human_review_required = "HUMAN_REVIEW_REQUIRED" in full_history
        not_ready = "NOT_READY_FOR_CLINICAL_USE" in full_history

        final_decision = {
            "supervisor_status": "REQUEST_HUMAN_REVIEW",
            "clinical_use_allowed": False,
            "automated_manchester_triage_allowed": False,
            "human_review_required": human_review_required,
            "not_ready_for_clinical_use": not_ready,
            "final_summary": (
                "AutoGen AgentChat team completed a governed multi-agent review using verified Azure API outputs. "
                "The system remains not-for-clinical-use. No Manchester triage category was assigned. "
                "Human review remains required for cases with missing triage inputs."
            ),
        }

        content = (
            "AGENT: Supervisor Agent\n"
            "STATUS: FINAL_STATUS\n"
            "FINAL_NON_CLINICAL_DECISION:\n"
            f"{pretty_json(final_decision)}"
        )

        return Response(chat_message=TextMessage(content=content, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self._history.clear()


async def main() -> None:
    stay_id = DEFAULT_STAY_ID

    team = RoundRobinGroupChat(
        participants=[
            DatasetEvidenceAgent(stay_id=stay_id),
            SafetyReviewAgent(stay_id=stay_id),
            GovernanceAgent(),
            SupervisorAgent(),
        ],
        termination_condition=MaxMessageTermination(max_messages=5),
    )

    task = (
        f"Run a governed multi-agent review for ED stay_id={stay_id}. "
        "Use only verified Azure API outputs. "
        "Do not assign a Manchester triage category. "
        "Do not provide clinical advice."
    )

    result = await team.run(task=task)

    print("\nAUTOGEN AGENTCHAT MULTI-AGENT TRIAGE REVIEW")
    print("=" * 60)

    for message in result.messages:
        if hasattr(message, "source") and hasattr(message, "content"):
            print(f"\n--- {message.source} ---")
            print(message.content)

    print("\n" + "=" * 60)
    print("AutoGen AgentChat team completed. No clinical triage category was assigned.\n")


if __name__ == "__main__":
    asyncio.run(main())