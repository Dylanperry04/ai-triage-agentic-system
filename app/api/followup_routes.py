"""
Follow-up triage comparison routes.

POST /followup/link                 — declare new_stay_id as a follow-up to previous_stay_id, run comparison, store it
GET  /followup/history/{stay_id}     — list all comparisons involving this stay_id (either side)

See app/schemas/followup.py and app/agents/followup_comparison_agent.py for
the full design rationale: this is a user-declared link, never an automatic
repeat-patient detector.
"""
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.agents.orchestrator import run_workflow
from app.agents.followup_comparison_agent import compare_follow_up
from app.api.triage_routes import _find_case
from app.schemas.followup import FollowUpLinkRequest
from app.storage.followup_repository import (
    append_followup_comparison,
    get_followup_history_for_stay,
)

router = APIRouter()

_FOLLOWUP_LOG_PATH = lambda: settings.processed_dir / "followup_comparisons.jsonl"


@router.post("/followup/link")
def link_followup(link: FollowUpLinkRequest):
    """
    Declare new_stay_id as a follow-up to previous_stay_id, run the
    deterministic comparison, store it in the audit log, and return it.

    Both stay_ids must already exist in the processed cases file. This
    endpoint does not search for or suggest candidate matches -- the
    caller must already know both stay_ids.
    """
    if link.previous_stay_id == link.new_stay_id:
        raise HTTPException(
            status_code=400,
            detail="previous_stay_id and new_stay_id must be different.",
        )

    previous_case = _find_case(link.previous_stay_id)
    new_case = _find_case(link.new_stay_id)

    previous_result = run_workflow(previous_case, include_llm_explanation=False)
    new_result = run_workflow(new_case, include_llm_explanation=False)

    comparison = compare_follow_up(link, previous_result, new_result)
    append_followup_comparison(_FOLLOWUP_LOG_PATH(), comparison)

    return comparison.model_dump(mode="json")


@router.get("/followup/history/{stay_id}")
def followup_history(stay_id: int):
    """
    List every stored follow-up comparison involving this stay_id, in
    either the previous or new position.
    """
    history = get_followup_history_for_stay(_FOLLOWUP_LOG_PATH(), stay_id)
    return [record.model_dump(mode="json") for record in history]
