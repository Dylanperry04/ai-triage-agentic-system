"""
LLM explanation API routes.

This endpoint exposes a safety-wrapped LLM explanation for verified public-demo
triage evidence.

Clinical safety stance:
- Not for clinical use.
- No automated Manchester triage classification.
- No diagnosis.
- No treatment advice.
- Human review required.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.agents.llm_explanation_agent import result_to_dict, run_llm_explanation
from app.agents.orchestrator import run_workflow
from app.config import settings
from app.schemas.internal import EDTriageCase
from app.storage.jsonl_repository import read_jsonl


router = APIRouter()


def _load_case_by_stay_id(stay_id: int) -> EDTriageCase:
    path = settings.processed_dir / "triage_cases_sample.jsonl"
    records = read_jsonl(path)

    for record in records:
        if int(record["stay_id"]) == stay_id:
            return EDTriageCase(**record)

    raise HTTPException(status_code=404, detail=f"stay_id not found: {stay_id}")


def _extract_missing_fields(workflow_result: dict[str, Any]) -> list[str]:
    """
    Returns the fields the Data Validation Agent already determined are
    missing for this case.

    BUG FIX (found during third-party code review, reproduced live before
    fixing): this previously searched data_quality_flags for a
    "MISSING_TRIAGE_FIELD:" prefix, but the real safety review agent never
    produces that prefix -- it produces "MISSING_CRITICAL_VITAL:{field}"
    and "MISSING_CHIEF_COMPLAINT" (see app/agents/safety_review_agent.py).
    The old code silently returned an empty list for every case, even ones
    genuinely missing critical vitals, hiding that fact from the LLM
    explanation evidence package. Fixed by reading the workflow's own
    data_validation.missing_required_fields directly, which the Data
    Validation Agent already computes correctly -- there is no need to
    re-infer missingness from a safety-review flag string at all.
    """
    data_validation = workflow_result.get("data_validation", {})
    missing_fields = data_validation.get("missing_required_fields", [])
    return list(missing_fields) if missing_fields else []


def build_verified_endpoint_evidence(stay_id: int) -> dict[str, Any]:
    """
    Builds verified evidence from local public-demo data and deterministic workflow output.

    This function does not use retrospective clinical outcome fields for triage.
    It does not assign Manchester triage.
    """

    case = _load_case_by_stay_id(stay_id)
    workflow = run_workflow(case).model_dump(mode="json")

    triage_input = workflow.get("triage_input", {})
    decision = workflow.get("manchester_decision") or workflow.get("decision") or {}
    safety_review = workflow.get("safety_review", {})
    missing_fields = _extract_missing_fields(workflow)

    return {
        "case_id": stay_id,
        "source_dataset": triage_input.get("source_dataset"),
        "chief_complaint": triage_input.get("chiefcomplaint"),
        "arrival_transport": triage_input.get("arrival_transport"),
        "missing_required_fields": missing_fields,
        "data_quality_flags": safety_review.get("data_quality_flags", []),
        "leakage_guard_passed": safety_review.get("leakage_guard_passed"),
        "requires_human_data_review": True,
        "governance_verdict": "NOT_READY_FOR_CLINICAL_USE",
        "clinical_use_status": "not_for_clinical_use",
        "blocking_issues": [
            "NO_CLINICIAN_APPROVED_MANCHESTER_RULESET",
            "AUTOMATED_MANCHESTER_CLASSIFICATION_BLOCKED",
            "HUMAN_REVIEW_REQUIRED",
        ],
        "manchester_classification_status": decision.get(
            "classification_status",
            "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED",
        ),
        "manchester_category": decision.get("category"),
        "manchester_priority": decision.get("priority"),
        "manchester_max_wait_minutes": decision.get("max_wait_minutes"),
        "human_review_required": True,
        "official_manchester_category_assigned": False,
        "provisional_mts_category_present": decision.get("category") is not None,
        "provisional_mts_category": decision.get("category"),
        "safety_instruction": (
            "The LLM must not create, assign, or change a triage category. "
            "Any Manchester-style category present is a PROVISIONAL, unvalidated "
            "research-ruleset output (not the official MTS, not clinically "
            "approved). The system is not for clinical use. Human review is "
            "required on every output."
        ),
    }


@router.get("/explain/llm/{stay_id}")
def explain_case_with_llm(stay_id: int):
    """
    Returns a safety-wrapped LLM explanation for a public-demo case.

    The deterministic wrapper controls clinical safety fields.
    The LLM only provides explanatory text.
    """

    evidence = build_verified_endpoint_evidence(stay_id)

    try:
        result = run_llm_explanation(evidence)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "LLM explanation unavailable because Azure OpenAI configuration is incomplete.",
                "error": str(exc),
                "clinical_use_allowed": False,
                "official_manchester_triage_allowed": False,
                "official_manchester_category_assigned": False,
                "human_review_required": True,
            },
        ) from exc

    result_dict = result_to_dict(result)

    if result.safety_failures:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "LLM explanation blocked by safety validator.",
                "case_id": stay_id,
                "clinical_use_allowed": False,
                "official_manchester_triage_allowed": False,
                "official_manchester_category_assigned": False,
                "human_review_required": True,
                "llm_explanation_status": result.explanation_status,
                "safety_failures": result.safety_failures,
                "llm_explanation": None,
            },
        )

    return {
        "case_id": stay_id,
        "endpoint": f"/explain/llm/{stay_id}",
        "input_evidence": evidence,
        "llm_explanation_result": result_dict,
        "llm_explanation": result.explanation_text,
        "safety_failures": [],
        "clinical_use_allowed": False,
        "official_manchester_triage_allowed": False,
        "official_manchester_category_assigned": False,
        "provisional_mts_category_present": (evidence.get("manchester_category") is not None),
        "provisional_mts_category": evidence.get("manchester_category"),
        "human_review_required": True,
        "clinical_safety_claim": "No clinical safety claim is made by this endpoint. Any category shown is provisional and unvalidated.",
    }