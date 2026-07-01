"""
Retired raw-stay_id LLM explanation API route.

The canonical explanation surface is POST /cases/{case_uid}/explanations, which
uses a pseudonymous case_uid and passes screened clinician questions into the
workflow. This module remains only as an explicit 410 tombstone when the legacy
compatibility router is deliberately enabled.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_dependencies import requires
from app.security import authz

router = APIRouter()


def _extract_missing_fields(workflow_result: dict[str, Any]) -> list[str]:
    """
    Return the fields the Data Validation Agent already determined are missing.

    This intentionally reads data_validation.missing_required_fields directly
    instead of trying to reconstruct missingness from safety-review flag text.
    """
    data_validation = workflow_result.get("data_validation", {})
    missing_fields = data_validation.get("missing_required_fields", [])
    return list(missing_fields) if missing_fields else []


def build_verified_endpoint_evidence(stay_id: int) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail=(
            "Legacy raw-stay_id explanation evidence is retired. Use "
            "POST /cases/{case_uid}/explanations instead."
        ),
    )


@router.get("/explain/llm/{stay_id}", dependencies=[Depends(requires(authz.PERM_ASK_CHATBOT, "explain_llm"))])
def explain_case_with_llm(stay_id: int, question: Optional[str] = None):
    raise HTTPException(
        status_code=410,
        detail=(
            "Legacy raw-stay_id explanation routes are retired. Use "
            "POST /cases/{case_uid}/explanations instead."
        ),
    )
