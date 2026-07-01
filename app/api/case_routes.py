"""
Canonical case_uid-keyed API (the final external surface).

All external identifiers are the PSEUDONYMOUS case_uid. Raw stay_id never appears
in a path or a response. Every protected route enforces requires(permission),
returns 401 unauthenticated / 403 unauthorised, audits the decision, and fails
closed in patient-data mode (via the auth dependency + the resolver + guarded
writes).

Routes:
  GET  /cases                          (list, RBAC: view_case)
  GET  /cases/{case_uid}               (one case, RBAC: view_case)
  POST /cases/{case_uid}/assessments   (run ML+rules workflow, RBAC: run_assessment)
  POST /cases/{case_uid}/explanations  (LLM explanation, RBAC: ask_chatbot)
  POST /cases/{case_uid}/multiagent-explanations (AutoGen explanation, RBAC: ask_chatbot)
  POST /cases/{case_uid}/reviews       (clinician review, RBAC: submit_review)
  POST /cases/{case_uid}/followups     (rerun/compare, RBAC: run_assessment)
  POST /cases/{case_uid}/followups/multiagent-explanations (explain reassessment)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.api.auth_dependencies import requires, get_auth_context
from app.api import case_resolver
from app.security import authz
from app.security.identity import AuthContext
from app.schemas.internal import EDTriageCase
from app.agents.orchestrator import run_workflow

router = APIRouter()


def _sensitive_audit_mode() -> bool:
    import os
    return (
        os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
        or os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
    )


def _patient_data_mode() -> bool:
    import os
    return os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"


def _resolve_or_404(case_uid: str) -> case_resolver.ResolvedCase:
    rc = case_resolver.resolve(case_uid)
    if rc is None:
        raise HTTPException(status_code=404, detail=f"case_uid not found: {case_uid}")
    return rc


def _public_case_view(rc: case_resolver.ResolvedCase, *, clinical: bool) -> Dict[str, Any]:
    """Build a response that never includes raw identifiers or retrospective data.
    Clinical content (triage-time vitals/chief complaint) only when the caller
    holds can_view_clinical_content; otherwise a minimal non-clinical summary."""
    from app.api import safe_dto
    if not clinical:
        out = safe_dto.safe_case_summary(rc.case_uid, rc.source_dataset)
        if rc.case.get("synthetic_demo"):
            out.update({
                "is_synthetic_demo": True,
                "demo_data_notice": (
                    rc.case.get("demo_data_notice")
                    or "Synthetic supervisor-demo case. Not real patient data."
                ),
            })
        return out
    return safe_dto.safe_clinical_case_view(rc.case_uid, rc.source_dataset, rc.case)


@router.get("/cases")
def list_cases(dataset: Optional[str] = None,
               limit: int = 50, offset: int = 0, q: Optional[str] = None,
               ctx: AuthContext = Depends(requires(authz.PERM_VIEW_CASE, "list_cases"))):
    """List pseudonymous case summaries, PAGINATED. limit is capped server-side
    (see case_resolver.MAX_PAGE_SIZE); offset selects the page. Returns the page
    plus pagination metadata so clients can page without ever requesting an
    unbounded result set."""
    if q and q.strip() and _patient_data_mode():
        raise HTTPException(
            status_code=503,
            detail=(
                "Unindexed /cases search is disabled in PATIENT_DATA_MODE. "
                "Wire a database/search-index-backed case query layer before "
                "enabling patient-data search."
            ),
        )
    clinical = authz.has_permission(ctx, authz.PERM_VIEW_CLINICAL_CONTENT)
    cases = case_resolver.list_cases(dataset, limit=limit, offset=offset, search=q)
    total = case_resolver.count_cases(dataset, search=q)
    search_meta = case_resolver.search_metadata(dataset, search=q)
    effective_limit = max(0, min(int(limit), case_resolver.MAX_PAGE_SIZE))
    effective_offset = max(0, int(offset))
    next_offset = effective_offset + effective_limit
    has_more = next_offset < total
    if q and search_meta.get("search_truncated"):
        has_more = True
    return {
        "cases": [_public_case_view(rc, clinical=clinical) for rc in cases],
        "pagination": {
            "total": total,
            "limit": effective_limit,
            "offset": effective_offset,
            "returned": len(cases),
            "has_more": has_more,
            "next_offset": next_offset if next_offset < total else None,
            "query": q or "",
            **search_meta,
        },
    }


@router.get("/cases/{case_uid}")
def get_case(case_uid: str,
             ctx: AuthContext = Depends(requires(authz.PERM_VIEW_CASE, "get_case"))):
    rc = _resolve_or_404(case_uid)
    clinical = authz.has_permission(ctx, authz.PERM_VIEW_CLINICAL_CONTENT)
    return _public_case_view(rc, clinical=clinical)


@router.post("/cases/{case_uid}/assessments")
def run_assessment(case_uid: str,
                   ctx: AuthContext = Depends(requires(authz.PERM_RUN_ASSESSMENT, "run_assessment"))):
    rc = _resolve_or_404(case_uid)
    case = EDTriageCase(**rc.case)
    result = run_workflow(case, include_llm_explanation=False)
    from app.api import safe_dto
    out = safe_dto.safe_assessment_response(
        rc.case_uid, rc.source_dataset, result.model_dump(mode="json"))
    safe_dto.assert_no_raw_identifiers(out)  # final guard
    # Persist the workflow-run audit record (redacted, guarded, fail-closed).
    try:
        import uuid as _uuid
        from datetime import datetime, timezone
        from app.schemas.workflow_run import build_workflow_run_record
        from app.storage.workflow_run_repository import append_workflow_run
        from app.config import settings as _settings
        _rec = build_workflow_run_record(
            result, run_id=str(_uuid.uuid4()),
            timestamp_utc=datetime.now(timezone.utc).isoformat())
        append_workflow_run(_settings.processed_dir / "workflow_runs.jsonl", _rec)
    except Exception:
        # In patient-data and local credentialed research modes the guarded
        # writer raises; let it surface so the action fails closed. In public
        # demo mode, audit-write issues are non-fatal.
        if _sensitive_audit_mode():
            raise
    return out


class ExplanationRequest(BaseModel):
    question: Optional[str] = None


@router.post("/cases/{case_uid}/explanations")
def explain_case(case_uid: str, body: ExplanationRequest = ExplanationRequest(),
                 ctx: AuthContext = Depends(requires(authz.PERM_ASK_CHATBOT, "explain_case"))):
    rc = _resolve_or_404(case_uid)
    case = EDTriageCase(**rc.case)
    from app.api import safe_dto
    # If the caller supplied a question, screen it through the agent gateway
    # (authorisation already checked above; this adds prompt-injection screening,
    # max-length, rate limiting, and audit). The LLM only EXPLAINS the already-
    # computed result; it cannot assign/alter triage.
    screened_question: Optional[str] = None
    if body.question:
        screened_question = body.question.strip()
        from app.security.agent_gateway import authorise_agent_call
        gate = authorise_agent_call(ctx, action="explain_case",
                                    case_uid=rc.case_uid, user_text=screened_question)
        if not gate.allowed:
            detail = {"reason": gate.reason}
            if gate.rate_limited:
                raise HTTPException(status_code=429, detail="rate limit exceeded")
            if gate.too_long:
                raise HTTPException(status_code=413, detail="question too long")
            raise HTTPException(status_code=400, detail=f"question blocked: {gate.reason}")
    result = run_workflow(
        case,
        include_llm_explanation=True,
        clinician_question=screened_question,
    )
    out = safe_dto.safe_explanation_response(
        rc.case_uid, rc.source_dataset, result.model_dump(mode="json"))
    safe_dto.assert_no_raw_identifiers(out)
    return out


@router.post("/cases/{case_uid}/multiagent-explanations")
async def multiagent_explain_case(
    case_uid: str,
    body: ExplanationRequest = ExplanationRequest(),
    ctx: AuthContext = Depends(requires(authz.PERM_ASK_CHATBOT, "multiagent_explain_case")),
):
    rc = _resolve_or_404(case_uid)
    case = EDTriageCase(**rc.case)
    from app.api import safe_dto

    screened_question: Optional[str] = None
    if body.question:
        screened_question = body.question.strip()

    result = run_workflow(case, include_llm_explanation=False)
    workflow_dict = result.model_dump(mode="json")
    evidence = safe_dto.safe_multiagent_evidence(
        rc.case_uid, rc.source_dataset, workflow_dict)
    safe_dto.assert_no_raw_identifiers(evidence)

    from app.security.agent_gateway import authorise_agent_call
    gate = authorise_agent_call(
        ctx,
        action="multiagent_explain_case",
        case_uid=rc.case_uid,
        user_text=screened_question or "",
        evidence=evidence,
    )
    if not gate.allowed:
        if gate.rate_limited:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        if gate.too_long:
            raise HTTPException(status_code=413, detail="question too long")
        raise HTTPException(status_code=400, detail=f"question blocked: {gate.reason}")

    from app.agents.autogen_multi_agent_team import run_case_uid_team_explanation
    team_result = await run_case_uid_team_explanation(
        rc.case_uid,
        gate.minimised_evidence or evidence,
        screened_question,
    )
    status = str(team_result.get("status") or "")
    if status == "SAFETY_FAIL":
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "multiagent_safety_filter_failed",
                "safety_failures": team_result.get("safety_failures") or [],
            },
        )
    if status == "ERROR":
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "multiagent_team_failed",
                "safety_failures": team_result.get("safety_failures") or [],
            },
        )

    out = safe_dto.safe_multiagent_explanation_response(
        rc.case_uid, rc.source_dataset, team_result)
    safe_dto.assert_no_raw_identifiers(out)
    return out


class ReviewBody(BaseModel):
    review_status: str
    review_comment: str = ""
    clinician_decision: Optional[str] = None
    clinician_override: Optional[str] = None
    override_reason: Optional[str] = None
    system_prediction: Optional[str] = None

    @field_validator("review_status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        from app.schemas.review import _ALLOWED_REVIEW_STATUSES
        up = (v or "").strip().upper()
        if up not in _ALLOWED_REVIEW_STATUSES:
            raise ValueError(
                f"Invalid review_status '{v}'. Allowed: {sorted(_ALLOWED_REVIEW_STATUSES)}")
        return up

    @field_validator("review_comment")
    @classmethod
    def _check_comment(cls, v: str) -> str:
        from app.schemas.review import _MAX_COMMENT_LEN
        from app.security.redaction import redact_text
        v = v or ""
        if len(v) > _MAX_COMMENT_LEN:
            raise ValueError(f"review_comment too long (max {_MAX_COMMENT_LEN})")
        return redact_text(v)

    @field_validator(
        "clinician_decision", "clinician_override", "override_reason",
        "system_prediction",
    )
    @classmethod
    def _check_short_text(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        from app.schemas.review import _MAX_OVERRIDE_LEN
        from app.security.redaction import redact_text
        if len(v) > _MAX_OVERRIDE_LEN:
            raise ValueError(f"review field too long (max {_MAX_OVERRIDE_LEN})")
        return redact_text(v)


@router.post("/cases/{case_uid}/reviews")
def submit_review(case_uid: str, body: ReviewBody,
                  ctx: AuthContext = Depends(requires(authz.PERM_SUBMIT_REVIEW, "submit_review"))):
    rc = _resolve_or_404(case_uid)
    # Reviewer identity comes from the AUTHENTICATED context, never the client.
    from app.schemas.review import HumanReviewRecord
    from app.storage.human_review_repository import append_human_review
    from app.config import settings
    from datetime import datetime, timezone
    from uuid import uuid4

    status = (body.review_status or "").upper()
    # A reason is required only for a genuine override or an explicitly uncertain
    # decision (item C). "Request more information" is a routing action, not an
    # override, so it does not require an override reason.
    override_like = (
        bool(body.clinician_override)
        or status in ("OVERRIDDEN", "OVERRIDE", "UNCERTAIN", "OVERRIDE_REQUIRED")
    )
    if override_like and not body.override_reason:
        raise HTTPException(status_code=422,
                            detail="override_reason is required for an override/uncertain decision")

    rec = HumanReviewRecord(
        review_id=str(uuid4()),
        stay_id=rc.stay_id,                       # redacted before persistence
        source_dataset=rc.source_dataset,
        case_uid=rc.case_uid,
        reviewer_user_id=getattr(ctx, "user_id", None),
        reviewer_email_or_pseudonym=getattr(ctx, "email", None) or getattr(ctx, "user_id", None),
        reviewer_roles=list(getattr(ctx, "roles", []) or []),
        auth_source=getattr(ctx, "source", None),
        reviewer_role=(list(getattr(ctx, "roles", []) or []) or [None])[0],
        review_status=body.review_status,
        review_comment=body.review_comment,
        system_prediction=body.system_prediction,
        clinician_decision=body.clinician_decision,
        clinician_override=body.clinician_override,
        override_reason=body.override_reason,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    append_human_review(settings.processed_dir / "human_reviews.jsonl", rec)
    return {"review_id": rec.review_id, "case_uid": rc.case_uid, "status": "recorded"}


# key -> (min, max) inclusive plausible ranges for triage-time vitals.
_ALLOWED_FOLLOWUP_VITALS = {
    "heartrate": (0.0, 350.0),
    "resprate": (0.0, 120.0),
    "o2sat": (0.0, 100.0),
    "sbp": (0.0, 400.0),
    "dbp": (0.0, 300.0),
    "temperature": (50.0, 115.0),   # Fahrenheit range (MIMIC default unit)
    "pain": (0.0, 10.0),
}


class FollowupBody(BaseModel):
    # A follow-up reruns the case with updated TRIAGE-TIME vitals to compare
    # acuity. Only an allow-list of vital keys is accepted, each numeric and
    # within a plausible physiological range; unknown keys or out-of-range values
    # are rejected rather than blindly merged onto the case.
    updated_vitals: Dict[str, Any] = {}

    @field_validator("updated_vitals")
    @classmethod
    def _validate_updated_vitals(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if not v:
            return {}
        clean: Dict[str, Any] = {}
        for key, value in v.items():
            if key not in _ALLOWED_FOLLOWUP_VITALS:
                raise ValueError(
                    f"Unknown vital '{key}'. Allowed: {sorted(_ALLOWED_FOLLOWUP_VITALS)}")
            try:
                num = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"Vital '{key}' must be numeric, got {value!r}")
            lo, hi = _ALLOWED_FOLLOWUP_VITALS[key]
            if not (lo <= num <= hi):
                raise ValueError(
                    f"Vital '{key}'={num} out of plausible range [{lo}, {hi}]")
            clean[key] = num
        return clean


class FollowupExplanationBody(FollowupBody):
    question: Optional[str] = None


def _changed_vital_summary(case_dict: Dict[str, Any],
                           updated_vitals: Dict[str, Any]) -> list[dict[str, Any]]:
    triage = case_dict.get("triage") or {}
    return [
        {"field": key, "previous": triage.get(key), "new": value}
        for key, value in updated_vitals.items()
    ]


def _run_followup_workflows(rc, updated_vitals: Dict[str, Any]):
    previous = EDTriageCase(**rc.case)
    prev_result = run_workflow(previous, include_llm_explanation=False)
    new_case_dict = dict(rc.case)
    if updated_vitals:
        triage = dict(new_case_dict.get("triage") or {})
        triage.update(updated_vitals)
        new_case_dict["triage"] = triage
    new_result = run_workflow(EDTriageCase(**new_case_dict), include_llm_explanation=False)
    return prev_result, new_result, _changed_vital_summary(rc.case, updated_vitals)


def _final_acuity_from_workflow(result) -> Any:
    faa = result.model_dump(mode="json").get("final_acuity_assessment") or {}
    return faa.get("final_acuity")


@router.post("/cases/{case_uid}/followups")
def followup_case(case_uid: str, body: FollowupBody,
                  ctx: AuthContext = Depends(requires(authz.PERM_RUN_ASSESSMENT, "followup_case"))):
    rc = _resolve_or_404(case_uid)
    prev_result, new_result, changed_vitals = _run_followup_workflows(
        rc, body.updated_vitals)
    prev_a, new_a = (
        _final_acuity_from_workflow(prev_result),
        _final_acuity_from_workflow(new_result),
    )
    from app.api import safe_dto
    out = safe_dto.safe_followup_response(
        rc.case_uid,
        prev_a,
        new_a,
        changed_fields=list(body.updated_vitals.keys()),
        changed_vitals=changed_vitals,
    )
    safe_dto.assert_no_raw_identifiers(out)
    # Persist a rerun audit record (redacted, guarded, fail-closed).
    try:
        import uuid as _uuid
        from datetime import datetime, timezone
        from app.schemas.rerun import WorkflowRerunRecord, VitalChange, compute_movement
        from app.storage.rerun_repository import append_rerun
        from app.config import settings as _settings
        changed = [VitalChange(field=k, previous=(rc.case.get("triage") or {}).get(k),
                               new=v) for k, v in body.updated_vitals.items()]
        _rec = WorkflowRerunRecord(
            rerun_id=str(_uuid.uuid4()),
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            case_uid=rc.case_uid, source_dataset=rc.source_dataset, stay_id=rc.stay_id,
            previous_final_acuity=prev_a, new_final_acuity=new_a,
            previous_category=None, new_category=None,
            changed_vitals=changed, movement=compute_movement(prev_a, new_a),
            reason=out["change"])
        append_rerun(_settings.processed_dir / "workflow_reruns.jsonl", _rec)
    except Exception:
        if _sensitive_audit_mode():
            raise
    return out


@router.post("/cases/{case_uid}/followups/multiagent-explanations")
async def followup_multiagent_explain_case(
    case_uid: str,
    body: FollowupExplanationBody,
    ctx: AuthContext = Depends(
        requires(authz.PERM_ASK_CHATBOT, "followup_multiagent_explain_case")
    ),
):
    if not body.updated_vitals:
        raise HTTPException(
            status_code=422,
            detail="updated_vitals is required before follow-up explanation.",
        )
    rc = _resolve_or_404(case_uid)
    from app.api import safe_dto

    prev_result, new_result, changed_vitals = _run_followup_workflows(
        rc, body.updated_vitals)
    evidence = safe_dto.safe_followup_multiagent_evidence(
        rc.case_uid,
        rc.source_dataset,
        prev_result.model_dump(mode="json"),
        new_result.model_dump(mode="json"),
        changed_fields=list(body.updated_vitals.keys()),
        changed_vitals=changed_vitals,
    )
    safe_dto.assert_no_raw_identifiers(evidence)

    screened_question = (
        body.question.strip()
        if body.question and body.question.strip()
        else "Why did the acuity change or stay the same after the edited vitals?"
    )
    from app.security.agent_gateway import authorise_agent_call
    gate = authorise_agent_call(
        ctx,
        action="followup_multiagent_explain_case",
        case_uid=rc.case_uid,
        user_text=screened_question,
        evidence=evidence,
    )
    if not gate.allowed:
        if gate.rate_limited:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        if gate.too_long:
            raise HTTPException(status_code=413, detail="question too long")
        raise HTTPException(status_code=400, detail=f"question blocked: {gate.reason}")

    from app.agents.autogen_multi_agent_team import run_case_uid_team_explanation
    team_result = await run_case_uid_team_explanation(
        rc.case_uid,
        gate.minimised_evidence or evidence,
        screened_question,
    )
    status = str(team_result.get("status") or "")
    if status == "SAFETY_FAIL":
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "multiagent_safety_filter_failed",
                "safety_failures": team_result.get("safety_failures") or [],
            },
        )
    if status == "ERROR":
        raise HTTPException(
            status_code=502,
            detail={
                "reason": "multiagent_team_failed",
                "safety_failures": team_result.get("safety_failures") or [],
            },
        )

    out = safe_dto.safe_multiagent_explanation_response(
        rc.case_uid, rc.source_dataset, team_result)
    safe_dto.assert_no_raw_identifiers(out)
    return out


def _old_followup_return():
    return None
