"""
AutoGen clinician chat API routes.

Exposes the AutoGen-based clinician chat agent (app/agents/autogen_team.py)
over HTTP, following the same pattern as explanation_routes.py:
- 503 if Azure OpenAI is not configured, with a clear message.
- 502 if the agent's reply fails the deterministic safety filter, with the
  failures explicitly listed rather than hidden.
- 200 only when the reply passed the safety filter.

The chat agent never assigns a clinical decision; it only explains evidence
already computed by the deterministic pipeline (see app/agents/autogen_team.py
for the full design rationale).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agents.autogen_team import run_single_question
from app.agents.autogen_multi_agent_team import run_team_explanation
from app.config import settings


router = APIRouter()


class ChatRequest(BaseModel):
    question: str


class TeamExplanationRequest(BaseModel):
    stay_id: int


@router.post("/chat/ask")
async def ask_clinician_chat_agent(request: ChatRequest):
    """
    Ask the AutoGen clinician chat agent a question. The question should
    reference a stay_id (e.g. "Tell me about stay 1") so the agent's tool
    can look up the relevant verified evidence.
    """
    cases_path = settings.processed_dir / "triage_cases_sample.jsonl"
    result = await run_single_question(request.question, cases_path=cases_path)

    if result["status"] == "NOT_CONFIGURED":
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Clinician chat agent unavailable because Azure OpenAI configuration is incomplete.",
                "reply_text": result["reply_text"],
                "human_review_required": True,
            },
        )

    if result["status"] in ("SAFETY_FAIL", "ERROR"):
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Chat agent reply blocked by safety validator." if result["status"] == "SAFETY_FAIL"
                else "Chat agent call failed.",
                "status": result["status"],
                "safety_failures": result["safety_failures"],
                "human_review_required": True,
            },
        )

    return {
        "status": result["status"],
        "reply_text": result["reply_text"],
        "safety_failures": result["safety_failures"],
        "human_review_required": True,
        "clinical_use_allowed": False,
        "clinical_safety_claim": "No clinical safety claim is made by this endpoint.",
    }


@router.post("/chat/team-explanation")
async def run_multi_agent_team_explanation(request: TeamExplanationRequest):
    """
    Runs the four-agent AutoGen team (IntakeAgent, ValidationAgent,
    SafetyReviewAgent, ExplanationAgent) for one stay_id. See
    app/agents/autogen_multi_agent_team.py for the full design rationale
    and safety boundary. This endpoint never assigns a clinical decision;
    the four agents only coordinate to explain evidence already computed
    by the deterministic pipeline.
    """
    cases_path = settings.processed_dir / "triage_cases_sample.jsonl"
    result = await run_team_explanation(request.stay_id, cases_path=cases_path)

    if result["status"] == "NOT_CONFIGURED":
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Multi-agent team unavailable because Azure OpenAI configuration is incomplete.",
                "final_explanation": result["final_explanation"],
                "human_review_required": True,
            },
        )

    if result["status"] in ("SAFETY_FAIL", "ERROR"):
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Team explanation blocked by safety validator." if result["status"] == "SAFETY_FAIL"
                else "Multi-agent team run failed.",
                "status": result["status"],
                "agent_turns": result["agent_turns"],
                "safety_failures": result["safety_failures"],
                "human_review_required": True,
            },
        )

    return {
        "status": result["status"],
        "agent_turns": result["agent_turns"],
        "final_explanation": result["final_explanation"],
        "safety_failures": result["safety_failures"],
        "human_review_required": True,
        "clinical_use_allowed": False,
        "clinical_safety_claim": "No clinical safety claim is made by this endpoint.",
    }
