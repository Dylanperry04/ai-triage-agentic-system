"""
Append-only workflow-run audit record.

One record is written per assessment run (every time a case is run through the
workflow in the UI). This is the persistent audit trail used for clinical review
and monitoring. For a local/demo deployment it is JSONL; for a hardened Azure
deployment it should be routed to Azure Blob/Table/Cosmos (the container
filesystem is ephemeral).

case_uid is the PSEUDONYMOUS, stable, dataset-qualified identifier produced by
app.security.redaction.pseudonymous_case_uid (e.g. "MIMIC-IV-ED-Full-v2.2~7181cfe22ed0a4c99301e").
It never contains the raw stay_id, so it is safe to put in audit records, API
responses, exports, and agent evidence. It is stable (same dataset+stay_id ->
same token), so it also works as the review-lookup key. The dataset prefix is
retained (it is not sensitive) so future/archived datasets cannot collide.
"""
from __future__ import annotations

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


def make_case_uid(source_dataset: Optional[str], stay_id: Any) -> str:
    """Central external case identifier. Delegates to the pseudonymous function
    so a raw stay_id is never embedded. Use this everywhere a case_uid is needed
    for audit/API/UI/review/export (raw stay_id stays in internal processing
    only)."""
    from app.security.redaction import pseudonymous_case_uid
    return pseudonymous_case_uid(source_dataset, stay_id)


class WorkflowRunRecord(BaseModel):
    """A single persisted workflow run."""
    model_config = {"protected_namespaces": ()}

    workflow_run_id: str
    timestamp_utc: str
    case_uid: str
    source_dataset: Optional[str] = None
    stay_id: Optional[int] = None

    # Triage-time input snapshot (what the workflow saw).
    input_snapshot: Dict[str, Any] = Field(default_factory=dict)
    input_schema: List[str] = Field(default_factory=list)

    # ML prediction summary (dataset-specific).
    prediction_scale: Optional[str] = None
    predicted_ktas_class: Optional[int] = None
    predicted_mimic_acuity: Optional[int] = None
    mapped_mts_category: Optional[str] = None
    mapped_mts_priority: Optional[int] = None

    # Final (override-adjusted) headline for MIMIC.
    final_acuity: Optional[int] = None
    final_category: Optional[str] = None
    override_applied: bool = False
    override_tier: Optional[str] = None

    # Deterministic safety + governance.
    rules_classification_status: Optional[str] = None
    safety_flags: List[str] = Field(default_factory=list)
    workflow_action: Optional[str] = None
    llm_status: Optional[str] = None
    clinician_review_required: bool = True
    human_review_status: str = "PENDING"

    # Versions for traceability.
    app_version: Optional[str] = None
    package_checkpoint: Optional[str] = None
    model_version: Optional[str] = None
    model_sha256: Optional[str] = None
    mapping_rule_version: Optional[str] = None
    override_rule_version: Optional[str] = None
    rules_version: Optional[str] = None

    # Actor that executed this real (non-preview) assessment. For a deliberate
    # clinician action this is that clinician. For an automatic post-observation
    # reassessment it is the ALTER system, with the authenticated trigger stored
    # separately below so the ED Nurse is not incorrectly recorded as having
    # exercised can_run_triage_assessment.
    performed_by_user_id: Optional[str] = None
    performed_by_display_name: Optional[str] = None
    performed_by_role: Optional[str] = None
    performed_by_identity_verified: bool = False
    performed_by_actor_type: str = "human"
    auth_source: Optional[str] = None
    assessment_triggered_by_user_id: Optional[str] = None
    assessment_triggered_by_display_name: Optional[str] = None
    assessment_triggered_by_role: Optional[str] = None
    assessment_triggered_by_identity_verified: bool = False
    assessment_trigger_auth_source: Optional[str] = None
    # Distinguish a clinician deliberately running an assessment from the
    # automatic assessment created after an ED Nurse saves new observations.
    assessment_execution_mode: str = "clinician_initiated"


def build_workflow_run_record(result, run_id: str, timestamp_utc: str,
                              app_version: Optional[str] = None, ctx=None,
                              assessment_execution_mode: str = "clinician_initiated") -> "WorkflowRunRecord":
    """Build a WorkflowRunRecord from a WorkflowResult (no I/O)."""
    ti = result.triage_input
    ml = result.ml_prediction
    fa = getattr(result, "final_acuity_assessment", None)
    dec = result.decision
    if app_version is None:
        from app.version import APP_VERSION
        app_version = APP_VERSION
    from app.version import PACKAGE_CHECKPOINT

    # Store the exact row consumed by the active UHL model rather than a loose
    # subset of the pre-model object. This captures derived Dublin calendar
    # fields and the Fahrenheit conversion performed by the serving contract.
    input_schema: list[str] = []
    input_snapshot: Dict[str, Any]
    try:
        from types import SimpleNamespace
        from app.constants import DATASET_SOURCE, MODEL_INPUT_COLUMNS, MODEL_SHA256
        if ti.source_dataset != DATASET_SOURCE:
            raise ValueError("not the active UHL serving source")
        from ml_training.uhl_synthetic.serving import uhl_dataframe_from_triage_inputs
        frame = uhl_dataframe_from_triage_inputs([SimpleNamespace(
            age=ti.age,
            arrival_time=ti.intime,
            presenting_complaint=ti.chiefcomplaint,
            temperature=ti.temperature,
            temperature_unit=ti.temperature_unit,
            heartrate=ti.heartrate,
            resprate=ti.resprate,
            o2sat=ti.o2sat,
            sbp=ti.sbp,
            dbp=ti.dbp,
            pain=ti.pain,
        )])
        row = frame.iloc[0]
        input_schema = list(MODEL_INPUT_COLUMNS)
        input_snapshot = {
            key: (row[key].item() if hasattr(row[key], "item") else row[key])
            for key in input_schema
        }
    except Exception:
        # Historical/non-UHL compatibility records remain readable, but the
        # monthly UHL exporter will reject this incomplete schema instead of
        # fabricating derived values.
        input_schema = []
        input_snapshot = {
            k: getattr(ti, k, None) for k in (
                "age", "chiefcomplaint", "heartrate", "resprate", "o2sat",
                "sbp", "dbp", "temperature", "temperature_unit", "pain",
            )
        }
    safety_flags = list(getattr(result.safety_review, "data_quality_flags", []) or [])

    return WorkflowRunRecord(
        workflow_run_id=run_id,
        timestamp_utc=timestamp_utc,
        case_uid=make_case_uid(ti.source_dataset, result.stay_id),
        source_dataset=ti.source_dataset,
        stay_id=result.stay_id,
        input_snapshot=input_snapshot,
        input_schema=input_schema,
        prediction_scale=ml.prediction_scale,
        predicted_ktas_class=ml.predicted_ktas_class,
        predicted_mimic_acuity=ml.predicted_mimic_acuity,
        mapped_mts_category=ml.mapped_mts_category,
        mapped_mts_priority=ml.mapped_mts_priority,
        final_acuity=getattr(fa, "final_acuity", None),
        final_category=getattr(fa, "category", None),
        override_applied=getattr(fa, "override_applied", False),
        override_tier=getattr(fa, "override_tier", None),
        rules_classification_status=getattr(dec, "classification_status", None),
        safety_flags=safety_flags,
        workflow_action=getattr(result, "workflow_action", None),
        llm_status=getattr(result.explanation, "status", None) if hasattr(result, "explanation") else None,
        clinician_review_required=getattr(dec, "requires_clinician_review", True),
        human_review_status="PENDING",
        app_version=app_version,
        package_checkpoint=PACKAGE_CHECKPOINT,
        model_version=getattr(ml, "model_version", None),
        model_sha256=MODEL_SHA256 if input_schema else None,
        mapping_rule_version=getattr(ml, "mapping_rule_version", None),
        override_rule_version=getattr(fa, "override_rule_version", None),
        rules_version=getattr(dec, "ruleset_id", None),
        performed_by_user_id=getattr(ctx, "user_id", None),
        performed_by_display_name=(
            getattr(ctx, "display_name", None) or getattr(ctx, "user_id", None)
        ),
        performed_by_role=(list(getattr(ctx, "roles", []) or []) or [None])[0],
        performed_by_identity_verified=bool(getattr(ctx, "authenticated", False))
        and not bool(getattr(ctx, "is_demo_stub", False)),
        performed_by_actor_type="human",
        auth_source=getattr(ctx, "source", None),
        assessment_execution_mode=assessment_execution_mode,
    )
