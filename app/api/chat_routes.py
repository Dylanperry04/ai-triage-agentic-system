"""
Retired raw-stay_id chat API routes.

The canonical LLM explanation surface is POST /cases/{case_uid}/explanations.
These legacy routes remain as explicit tombstones only when the compatibility
router is deliberately enabled.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth_dependencies import requires
from app.security import authz

router = APIRouter()


class ChatRequest(BaseModel):
    question: str


class TeamExplanationRequest(BaseModel):
    stay_id: int


async def run_single_question(*args, **kwargs):
    raise RuntimeError("legacy raw-stay_id chat route is retired")


async def run_team_explanation(*args, **kwargs):
    raise RuntimeError("legacy raw-stay_id team explanation route is retired")


@router.post("/chat/ask", dependencies=[Depends(requires(authz.PERM_ASK_CHATBOT, "chat_ask"))])
async def ask_clinician_chat_agent(request: ChatRequest):
    raise HTTPException(
        status_code=410,
        detail=(
            "Legacy raw-stay_id chat routes are retired. Use "
            "POST /cases/{case_uid}/explanations instead."
        ),
    )


@router.post("/chat/team-explanation", dependencies=[Depends(requires(authz.PERM_ASK_CHATBOT, "chat_team_explanation"))])
async def run_multi_agent_team_explanation(request: TeamExplanationRequest):
    raise HTTPException(
        status_code=410,
        detail=(
            "Legacy raw-stay_id team explanation routes are retired. Use "
            "POST /cases/{case_uid}/explanations instead."
        ),
    )
