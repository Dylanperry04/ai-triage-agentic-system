"""Nurse checklist, frozen decision traces and independent MTS research review."""
from datetime import datetime, timezone
from uuid import uuid4
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from app.api.auth_dependencies import requires
from app.api.case_routes import _serialized_case_transition
from app.security import authz
from app.schemas.mts import MTSChecklist, current_checklist
from app.rules.mts_catalog import CHARTS, SOURCE, VERSION, catalog_payload, catalog_hash, propose_flowchart
from app.rules.mts_assessment import evaluate_mts_checklist
from app.storage.mts_repository import read_records, append_record, require_demo_research

router = APIRouter()


def _research_mode():
    try:
        require_demo_research()
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def _case(case_uid):
    from app.api.case_routes import _resolve_or_404
    from app.constants import DATASET_SOURCE
    rc = _resolve_or_404(case_uid)
    if rc.source_dataset != DATASET_SOURCE:
        raise HTTPException(422, "The initial MTS research workflow supports the approved UHL research cohort only")
    age = (rc.case.get("triage") or {}).get("age")
    if age is None or float(age) < 16:
        raise HTTPException(422, "The initial MTS checklist supports age 16 and over only; use the appropriate clinician-led pathway")
    return rc


@router.get("/mts/catalog")
def get_catalog(ctx=Depends(requires(authz.PERM_VIEW_CASE, "view_mts_catalog"))):
    return catalog_payload()


@router.get("/cases/{case_uid}/mts-checklist")
def get_checklist(case_uid: str, ctx=Depends(requires(authz.PERM_VIEW_CLINICAL_CONTENT, "view_mts_checklist"))):
    _research_mode()
    from app.api.case_routes import _latest_workflow_state, _case_dict_with_workflow_updates
    rc = _case(case_uid)
    state = _latest_workflow_state(case_uid)
    case = _case_dict_with_workflow_updates(rc.case, state)
    return {"case_uid": case_uid, "catalog": catalog_payload(),
            "proposal": propose_flowchart((case.get("triage") or {}).get("chiefcomplaint")),
            "checklist": current_checklist(state), "revision": state.get("mts_checklist_revision"),
            "previous_checklist_requires_reassessment": bool(state.get("mts_checklist") and not current_checklist(state)),
            "observations_at": state.get("latest_triage_updates_at"),
            "observation_provenance": state.get("observation_provenance") or {},
            "recorded_triage": {k: v for k, v in (case.get("triage") or {}).items() if k in
                ("heartrate", "resprate", "o2sat", "sbp", "dbp", "temperature", "temperature_unit", "pain")},
            "source_triage": {k: v for k, v in (rc.case.get("triage") or {}).items() if k in
                ("heartrate", "resprate", "o2sat", "sbp", "dbp", "temperature", "temperature_unit", "pain")}}


@router.post("/cases/{case_uid}/mts-checklist")
@_serialized_case_transition
def save_checklist(case_uid: str, body: MTSChecklist,
                   ctx=Depends(requires(authz.PERM_RECORD_MTS_CHECKLIST, "record_mts_checklist"))):
    _research_mode()
    from app.api.case_routes import (_latest_workflow_state, _append_workflow_state, _case_dict_with_workflow_updates,
        _merge_workflow_state, _is_case_closed, _active_escalation_exists)
    rc = _case(case_uid)
    state = _latest_workflow_state(case_uid)
    if _is_case_closed(state):
        raise HTTPException(409, "A closed case cannot receive new checklist observations")
    if body.expected_revision != state.get("mts_checklist_revision"):
        raise HTTPException(409, "Another nurse updated this checklist; reload before saving")
    if body.expected_observations_at != state.get("latest_triage_updates_at"):
        raise HTTPException(409, "The observations changed; reload the checklist before saving")
    previous_checklist = current_checklist(state)
    content_exclusions = {"expected_revision", "expected_observations_at", "reassessed_measurements"}
    if previous_checklist and not body.reassessed_measurements and (
            MTSChecklist.model_validate(previous_checklist).model_dump(exclude=content_exclusions)
            == body.model_dump(exclude=content_exclusions)):
        return {"case_uid": case_uid, "revision": state["mts_checklist_revision"],
                "checklist": previous_checklist, "status": "unchanged",
                "assessment_required": bool(state.get("mts_checklist_requires_assessment"))}
    current_triage = _case_dict_with_workflow_updates(rc.case, state).get("triage") or {}
    proposal = propose_flowchart(current_triage.get("chiefcomplaint"))
    if body.flowchart_id != proposal["flowchart_id"] and not body.selection_reason.strip():
        raise HTTPException(422, "Record the clinical reason for choosing a different/unmapped flowchart")
    now, revision = datetime.now(timezone.utc).isoformat(), str(uuid4())
    updates = dict(state.get("latest_triage_updates") or {})
    # A source-recorded state is provenance, not an editable measurement.
    # Validate it against the actual current record, including unit conversion.
    import math
    from types import SimpleNamespace
    from app.rules.vitals import temperature_c
    for field, observation in body.observations.items():
        if observation.state in ("source_recorded", "recorded"):
            if observation.state == "source_recorded" and field in (state.get("observation_provenance") or {}):
                raise HTTPException(422, "A clinician-entered reading is not an original source measurement; reload its provenance")
            actual = current_triage.get(field)
            if field == "temperature" and actual is not None:
                actual = temperature_c(SimpleNamespace(temperature=actual, temperature_unit=current_triage.get("temperature_unit")))
            try:
                identical = actual is not None and math.isclose(float(actual), observation.value, rel_tol=0, abs_tol=1e-8)
            except (TypeError, ValueError):
                identical = False
            if not identical:
                raise HTTPException(422, "Source-recorded values must match the actual record; use assessed for a new measurement")
    for field in ("heartrate", "resprate", "o2sat", "sbp", "dbp", "temperature", "pain"):
        observation = body.observations.get(field)
        if observation and observation.state == "assessed":
            updates[field] = observation.value
            if field == "temperature":
                updates["temperature_unit"] = "C"
        elif observation and observation.state == "unavailable":
            updates[field] = None
    previous_sbp = updates.get("sbp", (rc.case.get("triage") or {}).get("sbp"))
    previous_dbp = updates.get("dbp", (rc.case.get("triage") or {}).get("dbp"))
    if previous_sbp is not None and previous_dbp is not None and previous_dbp > previous_sbp:
        raise HTTPException(422, "Diastolic pressure cannot exceed systolic pressure")
    values = body.model_dump(mode="json")
    values["expected_revision"] = revision
    values["expected_observations_at"] = now
    old_observations = (current_checklist(state) or {}).get("observations") or {}
    assessed_fields = [field for field, observation in body.observations.items()
        if observation.state == "assessed" and field in ("heartrate", "resprate", "o2sat", "sbp", "dbp", "temperature", "pain")
        and (field in body.reassessed_measurements or old_observations.get(field, {}).get("state") != "assessed"
             or old_observations.get(field, {}).get("value") != observation.value)]
    values["reassessed_measurements"] = []
    changed_findings = any(o.model_dump() != old_observations.get(field)
                           for field, o in body.observations.items())
    changed_notes = body.notes.strip() != str((previous_checklist or {}).get("notes") or "").strip()
    from app.services.observation_update import observation_transition
    transition = observation_transition(state, ctx=ctx, now=now, assessed_measurements=assessed_fields,
        answered=bool(assessed_fields or changed_findings or changed_notes))
    record = _merge_workflow_state(state, {"case_uid": case_uid, "source_dataset": rc.source_dataset,
        "updated_at_utc": now, "latest_triage_updates_at": now,
        "latest_triage_updates": updates, "mts_checklist": values,
        "mts_checklist_revision": revision, "mts_checklist_recorded_at": now,
        "last_action": "MTS_CHECKLIST_RECORDED", "mts_checklist_recorded_by": ctx.user_id,
        "latest_workflow_run_id": None,
        "mts_checklist_requires_assessment": True,
        "case_status": state.get("case_status", "new_unreviewed") if _active_escalation_exists(state) else "new_unreviewed",
        "review_status": "observations_updated_pending_assessment",
        "_reopen_for_reassessment": not _active_escalation_exists(state),
        "mts_checklist_recorded_roles": list(ctx.roles),
        "mts_selection_proposal": proposal, **transition})
    _append_workflow_state(record)
    return {"case_uid": case_uid, "revision": revision, "checklist": values,
            "assessment_required": True, "status": "observations_recorded_pending_triage_review"}


class MTSReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_uid: str = Field(min_length=1, max_length=200)
    workflow_run_id: str = Field(min_length=1, max_length=100)
    reference_flowchart: str = Field(max_length=80)
    reference_discriminators: list[str] | None = Field(default=None, max_length=100)
    reference_category: StrictInt = Field(ge=1, le=5)
    reference_method: Literal["independent_clinician_review", "unblinded_clinician_review"]
    reference_provenance: str = Field(min_length=5, max_length=2000)
    guideline_consistent: StrictBool | None = None
    explanation_id: str | None = Field(default=None, max_length=100)
    explanation_reviewed: StrictBool = False
    unsupported_claims: list[str] = Field(default_factory=list, max_length=100)
    acceptable_flowcharts: list[str] = Field(default_factory=list, max_length=8)
    supersedes_record_id: str | None = Field(default=None, max_length=100)
    correction_reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_reference(self):
        if len(self.reference_provenance.strip()) < 5:
            raise ValueError("Reference provenance must identify the actual reviewer/source and review method")
        if self.reference_flowchart not in CHARTS:
            raise ValueError("Unsupported reference flowchart")
        if not set(self.acceptable_flowcharts) <= set(CHARTS):
            raise ValueError("Acceptable alternatives must be supported charts")
        if self.supersedes_record_id and len(self.correction_reason.strip()) < 5:
            raise ValueError("An annotation correction requires a reason")
        allowed = {r["id"] for r in CHARTS[self.reference_flowchart]["rules"]}
        labels = self.reference_discriminators
        if labels is not None and (len(set(labels)) != len(labels) or not set(labels) <= allowed):
            raise ValueError("Reference discriminators must be unique and belong to the reference chart")
        if self.explanation_reviewed and not self.explanation_id:
            raise ValueError("An explanation review requires the exact saved explanation ID")
        if self.unsupported_claims and not self.explanation_reviewed:
            raise ValueError("Unsupported-claim annotations require an explanation review")
        if any(not text.strip() or len(text) > 500 for text in self.unsupported_claims):
            raise ValueError("Each unsupported claim requires a description of at most 500 characters")
        return self


@router.get("/cases/{case_uid}/mts-explanations")
def explanations(case_uid: str, workflow_run_id: str,
                 ctx=Depends(requires(authz.PERM_RECORD_MTS_REFERENCE, "review_mts_explanations"))):
    _research_mode()
    _case(case_uid)
    rows = [r for r in read_records("mts_explanation") if r.get("case_uid") == case_uid
            and r.get("workflow_run_id") == workflow_run_id]
    return {"explanations": rows}


@router.get("/cases/{case_uid}/mts-assessment")
def recorded_assessment(case_uid: str, workflow_run_id: str,
        ctx=Depends(requires(authz.PERM_VIEW_CLINICAL_CONTENT, "view_recorded_mts_assessment"))):
    run = _run_for_review(case_uid, workflow_run_id)
    return {"case_uid": case_uid, "workflow_run_id": workflow_run_id,
            "timestamp_utc": run.get("timestamp_utc"), "mts_assessment": run.get("mts_assessment") or {},
            "ml_acuity": run.get("predicted_mimic_acuity"), "final_acuity": run.get("final_acuity"),
            "system_identity": run.get("system_identity") or {}}


@router.get("/research/mts/assessments")
def reviewable_assessments(
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    cursor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    ctx=Depends(requires(authz.PERM_RECORD_MTS_REFERENCE, "list_mts_research_assessments")),
):
    _research_mode()
    from app.config import settings
    from app.storage.workflow_run_repository import read_workflow_runs
    from app.constants import DATASET_SOURCE
    rows = read_workflow_runs(settings.processed_dir / "workflow_runs.jsonl", strict=True)
    query = (q or "").strip().casefold()
    matches = []
    start = 0
    found_cursor = cursor is None
    for run in reversed(rows):
        if run.source_dataset != DATASET_SOURCE:
            continue
        complaint = (run.mts_assessment or {}).get("presenting_complaint")
        if query and not any(
            query in str(value or "").casefold()
            for value in (run.case_uid, run.workflow_run_id, complaint)
        ):
            continue
        matches.append({
            "case_uid": run.case_uid,
            "workflow_run_id": run.workflow_run_id,
            "timestamp_utc": run.timestamp_utc,
            "presenting_complaint": complaint,
        })
        if not found_cursor and run.workflow_run_id == cursor:
            found_cursor = True
            start = len(matches)
    if not found_cursor:
        raise HTTPException(409, "This assessment page is no longer available. Start again from the latest page.")
    page = matches[start:start + limit]
    more = start + limit < len(matches)
    return {
        "assessments": page,
        "limit": limit,
        "more_records_exist": more,
        "next_cursor": page[-1]["workflow_run_id"] if more else None,
        "matched_count": len(matches),
    }


@router.get("/research/mts/references")
def reference_history(case_uid: str,
        ctx=Depends(requires(authz.PERM_RECORD_MTS_REFERENCE, "view_mts_reference_history"))):
    _research_mode()
    _case(case_uid)
    return {"references": [r for r in read_records("mts_reference") if r.get("case_uid") == case_uid]}


def _run_for_review(case_uid, run_id):
    _research_mode()
    _case(case_uid)
    from app.config import settings
    from app.storage.workflow_run_repository import read_workflow_run
    run = read_workflow_run(settings.processed_dir / "workflow_runs.jsonl", run_id, case_uid, strict=True)
    if not run:
        raise HTTPException(409, "The exact recorded assessment is unavailable")
    return run


def _eligibility(row):
    from app.rules.decision_policy import system_identity
    current = system_identity()
    recorded = row.get("system_identity") or {}
    independent = row.get("reference_method") == "independent_clinician_review"
    input_ok = recorded.get("input_processing_sha256") == current["input_processing_sha256"]
    catalog_ok = row.get("catalog_sha256") == current["catalog_sha256"] and input_ok
    mts_ok = catalog_ok and recorded.get("mts_execution_sha256") == current["mts_execution_sha256"]
    ml_ok = input_ok and row.get("model_sha256") == current["model_sha256"] and recorded.get("serving_sha256") == current["serving_sha256"]
    final_ok = input_ok and recorded.get("system_policy_sha256") == current["system_policy_sha256"]
    flags = {"flowchart": independent and catalog_ok, "mts_category": independent and mts_ok,
             "ml_category": independent and ml_ok, "ml_safety_category": independent and ml_ok and recorded.get("safety_sha256") == current["safety_sha256"],
             "final_category": independent and final_ok}
    reasons = []
    if not input_ok:
        reasons.append("input_processing_version_mismatch")
    if not independent:
        reasons.append("reference_not_independent")
    if row.get("catalog_sha256") != current["catalog_sha256"]:
        reasons.append("catalog_version_mismatch")
    if not recorded:
        reasons.append("missing_executable_policy_identity")
    if independent and not mts_ok:
        reasons.append("mts_execution_version_mismatch")
    if independent and not ml_ok:
        reasons.append("model_or_serving_version_mismatch")
    if independent and not final_ok:
        reasons.append("hybrid_policy_version_mismatch")
    return flags, reasons


def _review_actor(ctx):
    return {"reviewer_user_id": ctx.user_id, "reviewer_roles": list(ctx.roles)}


class GuidelineReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_uid: str = Field(min_length=1, max_length=200)
    workflow_run_id: str = Field(min_length=1, max_length=100)
    reference_id: str = Field(min_length=1, max_length=100)
    target: Literal["mts_category", "final_category"] = "final_category"
    guideline_consistent: StrictBool
    review_note: str = Field(min_length=5, max_length=2000)


class ExplanationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_uid: str = Field(min_length=1, max_length=200)
    workflow_run_id: str = Field(min_length=1, max_length=100)
    explanation_id: str = Field(min_length=1, max_length=100)
    reference_id: str | None = Field(default=None, max_length=100)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=100)
    review_note: str = Field(min_length=5, max_length=2000)

    @field_validator("unsupported_claims")
    @classmethod
    def validate_claims(cls, values):
        if any(not v.strip() or len(v) > 500 for v in values):
            raise ValueError("Each claim requires 1-500 characters")
        return values


def _linked_reference(reference_id, case_uid, run_id):
    rows = [r for r in read_records("mts_reference") if r.get("record_id") == reference_id
            and r.get("case_uid") == case_uid and r.get("workflow_run_id") == run_id]
    if len(rows) != 1:
        raise HTTPException(409, "The exact reference annotation is unavailable for this assessment")
    return rows[0]


@router.post("/research/mts/guideline-reviews")
def record_guideline_review(body: GuidelineReview,
        ctx=Depends(requires(authz.PERM_RECORD_MTS_REFERENCE, "record_mts_guideline_review"))):
    run = _run_for_review(body.case_uid, body.workflow_run_id)
    output = (run.get("mts_assessment") or {}).get("priority") if body.target == "mts_category" else run.get("final_acuity")
    if output is None:
        raise HTTPException(409, "There is no completed decision for the selected adherence target")
    reference = _linked_reference(body.reference_id, body.case_uid, body.workflow_run_id)
    row = append_record("mts_guideline_review", {**body.model_dump(mode="json"), **_review_actor(ctx),
        "reference_method": reference["reference_method"], "system_identity": reference.get("system_identity")})
    return {"record_id": row["record_id"], "reference_id": body.reference_id}


@router.post("/research/mts/explanation-reviews")
def record_explanation_review(body: ExplanationReview,
        ctx=Depends(requires(authz.PERM_RECORD_MTS_REFERENCE, "record_mts_explanation_review"))):
    run = _run_for_review(body.case_uid, body.workflow_run_id)
    matches = [r for r in read_records("mts_explanation") if r.get("record_id") == body.explanation_id
               and r.get("case_uid") == body.case_uid and r.get("workflow_run_id") == body.workflow_run_id]
    if len(matches) != 1:
        raise HTTPException(409, "The exact explanation is unavailable for this assessment")
    if body.reference_id:
        _linked_reference(body.reference_id, body.case_uid, body.workflow_run_id)
    row = append_record("mts_explanation_review", {**body.model_dump(mode="json"), **_review_actor(ctx),
        "explanation_reviewed": True, "explanation_sha256": matches[0]["explanation_sha256"],
        "system_identity": run.get("system_identity") or {}})
    return {"record_id": row["record_id"], "explanation_id": body.explanation_id}


@router.post("/research/mts/references")
def record_reference(body: MTSReference,
        ctx=Depends(requires(authz.PERM_RECORD_MTS_REFERENCE, "record_mts_reference"))):
    return _record_reference_serialized(body.case_uid, body, ctx)


@_serialized_case_transition
def _record_reference_serialized(case_uid, body, ctx):
    run = _run_for_review(body.case_uid, body.workflow_run_id)
    mts = run.get("mts_assessment") or {}
    if mts.get("catalog_sha256") != catalog_hash():
        raise HTTPException(409, "Record an assessment using the current MTS catalog before adding its reference")
    # Validate all linked evidence before writing any part of a legacy combined request.
    if body.guideline_consistent is not None and run.get("final_acuity") is None:
        raise HTTPException(409, "There is no completed decision for the selected adherence target")
    if body.guideline_consistent is not None:
        read_records("mts_guideline_review")
    if body.explanation_reviewed:
        read_records("mts_explanation_review")
    if body.explanation_reviewed:
        matches = [r for r in read_records("mts_explanation") if r.get("record_id") == body.explanation_id
                   and r.get("case_uid") == body.case_uid and r.get("workflow_run_id") == body.workflow_run_id]
        if len(matches) != 1:
            raise HTTPException(409, "The exact explanation is unavailable for this assessment")
    clinical_keys = ("reference_flowchart", "reference_category", "reference_discriminators", "acceptable_flowcharts")
    previous = [r for r in read_records("mts_reference") if r.get("case_uid") == body.case_uid
                and r.get("reference_method") == body.reference_method]
    previous = previous[-1] if previous else None
    unchanged = previous and previous.get("workflow_run_id") == body.workflow_run_id and all(
        previous.get(k, [] if k == "acceptable_flowcharts" else None) == getattr(body, k) for k in clinical_keys)
    if previous and not unchanged and body.supersedes_record_id != previous["record_id"]:
        raise HTTPException(409, "An existing reference requires explicit supersedes_record_id and correction_reason")
    if body.supersedes_record_id and (not previous or body.supersedes_record_id != previous["record_id"]):
        raise HTTPException(409, "The reference correction is stale")
    if unchanged:
        record = previous
    else:
        values = body.model_dump(mode="json", exclude={"guideline_consistent", "explanation_reviewed", "explanation_id", "unsupported_claims"})
        record = append_record("mts_reference", {**values, **_review_actor(ctx),
            "catalog_version": VERSION, "catalog_sha256": mts["catalog_sha256"],
            "annotation_protocol_version": "independent_mts_reference_v1",
            "complaint_group": CHARTS[body.reference_flowchart]["complaint_group"],
            "model_sha256": run.get("model_sha256"), "system_identity": run.get("system_identity") or {},
            "ml_category": run.get("predicted_mimic_acuity"), "ml_safety_category": run.get("ml_safety_category"),
            "final_category": run.get("final_acuity"), "mts_category": mts.get("priority"),
            "mts_assessment_status": mts.get("status"),
            "proposed_flowchart": (mts.get("flowchart_proposal") or {}).get("flowchart_id"),
            "identified_discriminators": mts.get("matched_discriminators") if mts.get("selected_flowchart") else None})
    # Backward-compatible combined input is split into independent append-only streams.
    try:
        if body.guideline_consistent is not None:
            review = GuidelineReview(case_uid=body.case_uid, workflow_run_id=body.workflow_run_id,
                reference_id=record["record_id"], guideline_consistent=body.guideline_consistent,
                review_note=body.reference_provenance)
            if not any(all(r.get(k) == v for k, v in review.model_dump().items())
                       for r in read_records("mts_guideline_review")):
                record_guideline_review(review, ctx)
        if body.explanation_reviewed:
            review = ExplanationReview(case_uid=body.case_uid, workflow_run_id=body.workflow_run_id,
                reference_id=record["record_id"], explanation_id=body.explanation_id,
                unsupported_claims=body.unsupported_claims, review_note=body.reference_provenance)
            if not any(all(r.get(k) == v for k, v in review.model_dump().items())
                       for r in read_records("mts_explanation_review")):
                record_explanation_review(review, ctx)
    except OSError as exc:
        # JSONL is a single-process prototype store, not a cross-file transaction.
        # Expose the exact committed reference so a retry cannot silently lose it.
        raise HTTPException(503, {"code": "research_review_write_incomplete",
            "saved_reference_id": record["record_id"], "retry_safe": True,
            "message": "Reference saved; linked review write failed. Retry the same request."}) from exc
    flags, reasons = _eligibility(record)
    return {"record_id": record["record_id"], "workflow_run_id": body.workflow_run_id,
            "included_in_primary_metrics": any(flags.values()), "output_eligibility": flags,
            "exclusion_reasons": reasons}


def _evaluation_evidence_index(rows, label):
    from app.storage.jsonl_io import EvidenceIntegrityError
    records = {}
    for row in rows:
        key = row["record_id"]
        if key in records:
            raise EvidenceIntegrityError(f"Duplicate {label} record in research evidence")
        records[key] = row
    return records


def _require_review_evidence(records, review, field, label):
    from app.storage.jsonl_io import EvidenceIntegrityError
    identifier = review.get(field)
    parent = records.get(identifier) if isinstance(identifier, str) else None
    if not parent or any(parent[key] != review[key] for key in ("case_uid", "workflow_run_id")):
        raise EvidenceIntegrityError(f"Research review does not match its saved {label} record")
    return parent


@router.get("/research/mts/evaluation")
def evaluation(ctx=Depends(requires(authz.PERM_VIEW_MODEL_PERFORMANCE, "view_mts_evaluation"))):
    _research_mode()
    from app.analytics.mts_evaluation import evaluate_reference_pairs
    from app.rules.decision_policy import system_identity
    from app.storage.jsonl_io import EvidenceIntegrityError
    current_identity = system_identity()
    # Reviews are appended after their parents. Reading dependent streams
    # first avoids mistaking a concurrent, valid append for an orphan.
    guideline_rows = read_records("mts_guideline_review")
    explanation_review_rows = read_records("mts_explanation_review")
    rows = read_records("mts_reference")
    references = _evaluation_evidence_index(rows, "reference")
    explanations = _evaluation_evidence_index(read_records("mts_explanation"), "explanation")
    latest = {}
    for row in rows:
        latest[(row.get("case_uid"), row.get("reference_method"))] = row
    reviews = {}
    for row in guideline_rows:
        _require_review_evidence(references, row, "reference_id", "reference")
        reviews[(row.get("reference_id"), row.get("target", "final_category"))] = row
    pairs, excluded = [], []
    for row in latest.values():
        pair = dict(row)
        flags, reasons = _eligibility(row)
        pair["output_eligibility"] = flags
        for field in ("ml_category", "ml_safety_category", "mts_category", "final_category"):
            if not flags[field]:
                pair[field] = None
        if not any(flags.values()):
            excluded.append({"record_id": row["record_id"], "reasons": reasons})
            if row.get("reference_method") == "independent_clinician_review":
                continue
        for target in ("mts_category", "final_category"):
            judgement = reviews.get((row["record_id"], target))
            pair["guideline_consistent" if target == "final_category" else "mts_guideline_consistent"] = (
                judgement["guideline_consistent"] if judgement and flags[target] else None)
        pairs.append(pair)
    explanation_reviews = {}
    legacy_unlinked_explanation_reviews = 0
    for row in [*rows, *explanation_review_rows]:
        legacy = row["record_kind"] == "mts_reference"
        if row.get("explanation_reviewed") is not True:
            if not legacy:
                raise EvidenceIntegrityError("Research explanation review is missing its review status")
            continue
        if legacy and not row.get("explanation_id"):
            legacy_unlinked_explanation_reviews += 1
            continue
        explanation = _require_review_evidence(explanations, row, "explanation_id", "explanation")
        digest = row.get("explanation_sha256")
        if (not legacy or digest is not None) and digest != explanation["explanation_sha256"]:
            raise EvidenceIntegrityError("Research review does not match the saved explanation checksum")
        if not legacy and row.get("reference_id") is not None:
            _require_review_evidence(references, row, "reference_id", "reference")
        if ((row.get("system_identity") or {}).get("system_policy_sha256") == current_identity["system_policy_sha256"]
                and (explanation.get("system_identity") or {}).get("system_policy_sha256") == current_identity["system_policy_sha256"]):
            explanation_reviews[row["explanation_id"]] = row
    report = evaluate_reference_pairs(pairs, explanation_pairs=list(explanation_reviews.values()))
    generated = [r for r in explanations.values() if (r.get("system_identity") or {}).get(
        "system_policy_sha256") == current_identity["system_policy_sha256"]]
    report["unsupported_clinical_claims"].update(unit="latest clinician review per exact saved explanation",
        generated_explanations=len(generated),
        legacy_reviews_without_exact_link=legacy_unlinked_explanation_reviews,
        unreviewed_explanations=sum(r["record_id"] not in explanation_reviews for r in generated))
    report.update(catalog_version=VERSION, catalog_sha256=catalog_hash(), source=SOURCE,
        system_identity=current_identity, model_sha256=current_identity["model_sha256"],
        total_reference_records=len(rows), excluded_version_records=sum(
            any(reason != "reference_not_independent" for reason in item["reasons"]) for item in excluded),
        exclusions=excluded, repeated_case_reviews=len(rows)-len(latest))
    return report
