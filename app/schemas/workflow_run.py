"""
Append-only workflow-run audit record.

One record is written per assessment run (every time a case is run through the
workflow in the UI). This is the persistent audit trail the supervisor review
asked for. For a local/demo deployment it is JSONL; for a hardened Azure
deployment it should be routed to Azure Blob/Table/Cosmos (the container
filesystem is ephemeral).

case_uid = source_dataset + ":" + stay_id  (e.g. "MIMIC-IV-ED-Demo-v2.2:37887480")
gives a globally-unique key across datasets, since KTAS and MIMIC stay_ids can
otherwise collide.
"""
from __future__ import annotations

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


def make_case_uid(source_dataset: Optional[str], stay_id: Any) -> str:
    return f"{source_dataset or 'UNKNOWN'}:{stay_id}"


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
    model_version: Optional[str] = None
    mapping_rule_version: Optional[str] = None
    override_rule_version: Optional[str] = None
    rules_version: Optional[str] = None


def build_workflow_run_record(result, run_id: str, timestamp_utc: str,
                              app_version: str = "2.0.0") -> "WorkflowRunRecord":
    """Build a WorkflowRunRecord from a WorkflowResult (no I/O)."""
    ti = result.triage_input
    ml = result.ml_prediction
    fa = getattr(result, "final_acuity_assessment", None)
    dec = result.decision

    input_snapshot = {
        k: getattr(ti, k, None) for k in (
            "chiefcomplaint", "heartrate", "resprate", "o2sat", "sbp", "dbp",
            "temperature", "temperature_unit", "pain", "gender", "arrival_transport",
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
        model_version=getattr(ml, "model_version", None),
        mapping_rule_version=getattr(ml, "mapping_rule_version", None),
        override_rule_version=getattr(fa, "override_rule_version", None),
        rules_version=getattr(dec, "ruleset_id", None),
    )
