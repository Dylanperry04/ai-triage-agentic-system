"""
Security-status, audit-events, and model-performance endpoints.

  GET /security/status   (RBAC: view_security_status)
  GET /audit/events      (RBAC: view_audit_log)
  GET /model/performance (RBAC: view_model_performance)

All enforce requires(permission), audit the access, and never expose secrets,
the full-MIMIC path, or raw identifiers.
"""
from __future__ import annotations

import csv
import math
import os
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth_dependencies import requires
from app.security import authz
from app.security.security_status import build_security_status
from app.config import settings

router = APIRouter()


class SystemAssistantRequest(BaseModel):
    question: str = Field(default="", max_length=1000)


_PATIENT_QUESTION_TERMS = {
    "diagnose this", "diagnosis for", "treatment for", "what acuity",
    "assign acuity", "which triage category", "what triage category",
    "heart attack", "stroke", "medicine for", "symptom means",
}


@router.post("/system/assistant",
             dependencies=[Depends(requires(authz.PERM_ASK_CHATBOT, "system_assistant"))])
def system_assistant(body: SystemAssistantRequest) -> Dict[str, Any]:
    """ITD-only deterministic assistant for system/governance questions.

    This is deliberately not the clinical case explainer. It does not accept a
    case_uid, does not read clinical case evidence, and refuses patient-triage
    questions so the Ask surface cannot become a hidden clinical chatbot.
    """
    from app.data_pipeline.mimic_full_loader import full_mimic_diagnostic
    from app.version import APP_VERSION, PACKAGE_CHECKPOINT
    from app.security.redaction import assert_no_raw_identifiers

    question = (body.question or "").strip()
    lower = question.lower()
    patient_specific = any(term in lower for term in _PATIENT_QUESTION_TERMS)
    patient_specific = patient_specific or any(
        token in lower for token in ("case_uid", "chief complaint for patient")
    )
    if patient_specific:
        response = {
            "status": "refused_patient_context",
            "answer": (
                "This ITD assistant is limited to system, security, governance, "
                "deployment, audit, and model-artifact status. It cannot answer "
                "patient-specific triage, diagnosis, treatment, or acuity questions."
            ),
            "evidence_scope": "system_admin_only",
        }
        assert_no_raw_identifiers(response)
        return response

    security = build_security_status()
    mimic = full_mimic_diagnostic()
    report_raw = (
        os.environ.get("MIMIC_FULL_MODEL_REPORT_DIR", "").strip()
        or os.environ.get("MIMIC_FULL_REPORT_DIR", "").strip()
        or os.environ.get("MIMIC_FULL_OUTPUT_DIR", "").strip()
    )
    model_raw = os.environ.get("MIMIC_FULL_MODEL_PATH", "").strip()
    model_path_exists = bool(model_raw and Path(model_raw).expanduser().exists())
    report_dir_exists = bool(report_raw and Path(report_raw).expanduser().exists())
    evidence = {
        "app_version": APP_VERSION,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "security_mode": security.get("current_mode"),
        "auth_provider": security.get("auth_provider"),
        "is_safe_configuration": security.get("is_safe"),
        "mimic_full_loadable": mimic.get("full_mimic_loadable"),
        "mimic_full_reason": mimic.get("reason"),
        "model_path_configured": bool(model_raw),
        "model_file_exists": model_path_exists,
        "report_dir_configured": bool(report_raw),
        "report_dir_exists": report_dir_exists,
        "audit_sink": security.get("audit_sink"),
        "warnings": security.get("unsafe_combinations") or [],
    }
    topic_bits = []
    if any(term in lower for term in ("model", "artifact", "artefact", "readiness")):
        topic_bits.append(
            f"Model artifact configured={evidence['model_path_configured']} "
            f"and file_exists={evidence['model_file_exists']}."
        )
    if any(term in lower for term in ("audit", "log", "governance")):
        topic_bits.append(
            f"Audit sink={evidence['audit_sink']}; governance warnings="
            f"{len(evidence['warnings'])}."
        )
    if any(term in lower for term in ("security", "auth", "role", "permission", "itd")):
        topic_bits.append(
            f"Security mode={evidence['security_mode']}; auth provider="
            f"{evidence['auth_provider']}; safe_config={evidence['is_safe_configuration']}."
        )
    if any(term in lower for term in ("vital", "notification", "overdue", "escalation", "workflow")):
        topic_bits.append(
            "Workflow/vitals evidence is held in backend workflow-state and "
            "audit-dashboard records; this assistant can describe configuration "
            "but does not inspect an individual patient case."
        )
    if any(term in lower for term in ("deployment", "azure", "runtime", "report")):
        topic_bits.append(
            f"Reports configured={evidence['report_dir_configured']} and "
            f"report_dir_exists={evidence['report_dir_exists']}."
        )
    if not topic_bits:
        topic_bits.append(
            "Ask about system status, audit logging, governance evidence, role "
            "permissions, deployment settings, model artifact status, or training "
            "report availability."
        )
    answer = (
        " ".join(topic_bits)
        + " This assistant is read-only, system/admin-only, and does not inspect "
        "individual cases or assign triage."
    )
    response = {
        "status": "answered",
        "answer": answer,
        "evidence_scope": "system_admin_only",
        "evidence": evidence,
    }
    assert_no_raw_identifiers(response)
    return response


@router.get("/security/status",
            dependencies=[Depends(requires(authz.PERM_VIEW_SECURITY_STATUS, "view_security_status"))])
def security_status() -> Dict[str, Any]:
    return build_security_status()


@router.get("/audit/events",
            dependencies=[Depends(requires(authz.PERM_VIEW_AUDIT_LOG, "view_audit_events"))])
def audit_events(limit: int = 200) -> Dict[str, Any]:
    """Return recent access-audit events. Records are already redacted (no raw
    identifiers; pseudonymous case_uid only).

    In patient-data mode, events are read from the durable audit sink (local JSONL
    is not an acceptable audit source for patient data); if the durable sink is not
    configured, this fails closed."""
    import os
    limit = max(1, min(limit, 1000))

    if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true":
        from app.security.audit_sink import (
            AuditSinkReadError,
            get_audit_sink,
            LocalJsonlAuditSink,
        )
        from app.security.access_audit import AccessAuditError
        sink = get_audit_sink(Path("data/processed") / "access_audit.jsonl")
        if isinstance(sink, LocalJsonlAuditSink):
            raise AccessAuditError(
                "Audit reads require a durable audit sink in patient-data mode "
                "(local JSONL is not an acceptable patient-data audit source).")
        reader = getattr(sink, "read_recent", None)
        if reader is None:
            raise AccessAuditError(
                "Durable audit sink does not support reads; cannot serve "
                "/audit/events in patient-data mode.")
        try:
            try:
                events = reader(limit, record_kind="access_audit")
            except TypeError:
                events = reader(limit)
        except AuditSinkReadError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"count": len(events), "events": events, "source": "durable"}

    # Demo/local credentialed mode: read the same guarded audit path used for
    # writes. In local credentialed mode this is outside the repo or it raises.
    from app.security.access_audit import _audit_path
    from app.storage.jsonl_io import read_jsonl_dicts
    path = _audit_path()
    events: List[Dict[str, Any]] = read_jsonl_dicts(path)
    events = events[-limit:]
    source = (
        "local_credentialed"
        if os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        else "local"
    )
    return {"count": len(events), "events": events, "source": source}


def _safe_model_records(records: List[Any], *, limit: int) -> List[Dict[str, Any]]:
    from app.security.redaction import redact_for_log, assert_no_raw_identifiers

    out: List[Dict[str, Any]] = []
    for record in records[-limit:]:
        if hasattr(record, "model_dump"):
            data = record.model_dump(mode="json")
        else:
            data = dict(record)
        safe = redact_for_log(data)
        assert_no_raw_identifiers(safe)
        out.append(safe)
    return out


def _read_patient_durable_records(*, record_kind: str, limit: int) -> List[Dict[str, Any]]:
    from app.security.audit_sink import AuditSinkReadError, LocalJsonlAuditSink, get_audit_sink

    sink = get_audit_sink(settings.processed_dir / f"{record_kind}.jsonl")
    if isinstance(sink, LocalJsonlAuditSink):
        raise HTTPException(
            status_code=503,
            detail=(
                "Patient-data audit reads require AUDIT_SINK=durable with a "
                "read-capable durable audit client."
            ),
        )
    reader = getattr(sink, "read_recent", None)
    if reader is None:
        raise HTTPException(
            status_code=503,
            detail="Durable audit sink does not support read_recent.",
        )
    try:
        try:
            records = reader(limit, record_kind=record_kind)
        except TypeError:
            records = reader(limit)
    except AuditSinkReadError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Durable audit read failed.") from exc
    return _safe_model_records(list(records), limit=limit)


@router.get("/audit/records",
            dependencies=[Depends(requires(authz.PERM_VIEW_AUDIT_LOG, "view_audit_records"))])
def audit_records(limit: int = 200) -> Dict[str, Any]:
    """Return recent workflow/review/rerun audit records through the backend.

    This complements /audit/events. In local credentialed research mode the same
    outside-repo guarded paths used for writes are used for reads. In formal
    patient-data mode, detailed record reads require a real durable audit client
    with read support; this repository only provides that deployment seam.
    """
    import os

    limit = max(1, min(limit, 1000))
    if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true":
        workflow_runs = _read_patient_durable_records(
            record_kind="workflow_run", limit=limit
        )
        human_reviews = _read_patient_durable_records(
            record_kind="human_review", limit=limit
        )
        workflow_reruns = _read_patient_durable_records(
            record_kind="workflow_rerun", limit=limit
        )
        return {
            "source": "durable",
            "limit": limit,
            "counts": {
                "workflow_runs": len(workflow_runs),
                "human_reviews": len(human_reviews),
                "workflow_reruns": len(workflow_reruns),
            },
            "workflow_runs": workflow_runs,
            "human_reviews": human_reviews,
            "workflow_reruns": workflow_reruns,
        }

    from app.security.local_paths import credentialed_artifact_path
    from app.storage.workflow_run_repository import read_workflow_runs
    from app.storage.human_review_repository import read_human_reviews
    from app.storage.rerun_repository import read_reruns

    def path_for(filename: str, purpose: str) -> Path:
        return credentialed_artifact_path(settings.processed_dir / filename, purpose=purpose)

    workflow_runs = read_workflow_runs(path_for("workflow_runs.jsonl", "workflow-run audit read"))
    human_reviews = read_human_reviews(path_for("human_reviews.jsonl", "human-review audit read"))
    workflow_reruns = read_reruns(path_for("workflow_reruns.jsonl", "workflow-rerun audit read"))
    source = (
        "local_credentialed"
        if os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        else "local"
    )
    safe_runs = _safe_model_records(workflow_runs, limit=limit)
    safe_reviews = _safe_model_records(human_reviews, limit=limit)
    safe_reruns = _safe_model_records(workflow_reruns, limit=limit)
    return {
        "source": source,
        "limit": limit,
        "counts": {
            "workflow_runs": len(workflow_runs),
            "human_reviews": len(human_reviews),
            "workflow_reruns": len(workflow_reruns),
        },
        "workflow_runs": safe_runs,
        "human_reviews": safe_reviews,
        "workflow_reruns": safe_reruns,
    }


@router.get("/audit/dashboard",
            dependencies=[Depends(requires(authz.PERM_VIEW_AUDIT_LOG, "view_audit_dashboard"))])
def audit_dashboard(
    limit: int = 1000,
    start_utc: str | None = None,
    end_utc: str | None = None,
    patient_or_case: str | None = None,
    triage_level: str | None = None,
    reviewer_role: str | None = None,
    decision_type: str | None = None,
    action_type: str | None = None,
    escalation_status: str | None = None,
    override_status: str | None = None,
    source_dataset: str | None = None,
) -> Dict[str, Any]:
    """Return filtered audit-dashboard data from existing redacted evidence.

    This endpoint intentionally works from already-redacted audit/workflow
    artifacts. It never reads raw MIMIC tables and strips raw source records from
    the response after normalisation.
    """
    limit = max(1, min(limit, 5000))
    patient_mode = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"

    from app.analytics.audit_dashboard import (
        AuditFilters,
        dashboard_payload,
        normalise_audit_records,
    )
    from app.security.access_audit import _audit_path
    from app.security.local_paths import credentialed_artifact_path
    from app.security.redaction import assert_no_raw_identifiers, redact_for_log
    from app.storage.case_state_repository import read_case_states
    from app.storage.human_review_repository import read_human_reviews
    from app.storage.rerun_repository import read_reruns
    from app.storage.workflow_run_repository import read_workflow_runs
    from app.storage.jsonl_io import read_jsonl_dicts

    if patient_mode:
        access_events = _read_patient_durable_records(record_kind="access_audit", limit=5000)
        workflow_runs = _read_patient_durable_records(record_kind="workflow_run", limit=5000)
        human_reviews = _read_patient_durable_records(record_kind="human_review", limit=5000)
        workflow_reruns = _read_patient_durable_records(record_kind="workflow_rerun", limit=5000)
        workflow_states = _read_patient_durable_records(
            record_kind="case_workflow_state", limit=5000
        )
    else:
        def path_for(filename: str, purpose: str) -> Path:
            return credentialed_artifact_path(settings.processed_dir / filename, purpose=purpose)

        access_events: List[Dict[str, Any]] = []
        access_path = _audit_path()
        for row in read_jsonl_dicts(access_path):
            access_events.append(redact_for_log(row))

        workflow_runs = _safe_model_records(
            read_workflow_runs(path_for("workflow_runs.jsonl", "workflow-run dashboard read")),
            limit=5000,
        )
        human_reviews = _safe_model_records(
            read_human_reviews(path_for("human_reviews.jsonl", "human-review dashboard read")),
            limit=5000,
        )
        workflow_reruns = _safe_model_records(
            read_reruns(path_for("workflow_reruns.jsonl", "workflow-rerun dashboard read")),
            limit=5000,
        )
        workflow_states = [
            redact_for_log(record)
            for record in read_case_states(
                path_for("case_workflow_state.jsonl", "workflow-state dashboard read")
            )
        ][-5000:]

    records = normalise_audit_records(
        access_events=access_events[-5000:],
        workflow_runs=workflow_runs,
        human_reviews=human_reviews,
        workflow_reruns=workflow_reruns,
        workflow_states=workflow_states,
    )
    from app.api import case_resolver, safe_dto

    identity_cache: Dict[str, Dict[str, Any]] = {}

    def _identity_for_case(case_uid: Any) -> Dict[str, Any]:
        key = str(case_uid or "").strip()
        if not key:
            return {}
        if key not in identity_cache:
            try:
                rc = case_resolver.resolve(key)
                identity_cache[key] = (
                    safe_dto.safe_display_identity(rc.case) if rc is not None else {}
                )
            except Exception:
                identity_cache[key] = {}
        return identity_cache[key]

    for record in records:
        record.update(_identity_for_case(record.get("case_uid")))

    payload = dashboard_payload(
        records,
        AuditFilters(
            start_utc=start_utc,
            end_utc=end_utc,
            patient_or_case=patient_or_case,
            triage_level=triage_level,
            reviewer_role=reviewer_role,
            decision_type=decision_type,
            action_type=action_type,
            escalation_status=escalation_status,
            override_status=override_status,
            source_dataset=source_dataset,
        ),
        limit=limit,
    )
    for row in (payload.get("aggregations") or {}).get("escalation_worklist") or []:
        row.update(_identity_for_case(row.get("case_uid")))
    payload["source"] = (
        "durable"
        if patient_mode
        else (
            "local_credentialed"
            if os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
            else "local"
        )
    )
    assert_no_raw_identifiers(payload)
    return payload


@router.get("/cost/estimate",
            dependencies=[Depends(requires(authz.PERM_VIEW_MODEL_PERFORMANCE, "view_cost_estimate"))])
def cost_estimate() -> Dict[str, Any]:
    """Return configurable Azure/runtime cost assumptions and scenario estimates.

    This endpoint does not query live Azure pricing. Null rates remain null and
    are labelled as pending confirmation.
    """
    from app.analytics.costing import build_cost_estimate
    from app.security.redaction import assert_no_raw_identifiers

    payload = build_cost_estimate()
    assert_no_raw_identifiers(payload)
    return payload


@router.get("/model/performance",
            dependencies=[Depends(requires(authz.PERM_VIEW_MODEL_PERFORMANCE, "view_model_performance"))])
def model_performance() -> Dict[str, Any]:
    """Return full-MIMIC aggregate model-performance artefacts, if generated on
    the credentialed/approved environment. No retired demo/KTAS files are read."""
    import hashlib
    import orjson
    import os
    from app.data_pipeline.mimic_full_loader import full_mimic_diagnostic
    from app.version import APP_VERSION, PACKAGE_CHECKPOINT

    diag = full_mimic_diagnostic()
    report_raw = (
        os.environ.get("MIMIC_FULL_MODEL_REPORT_DIR", "").strip()
        or os.environ.get("MIMIC_FULL_REPORT_DIR", "").strip()
        or os.environ.get("MIMIC_FULL_OUTPUT_DIR", "").strip()
    )
    report_dir = Path(report_raw).expanduser() if report_raw else settings.processed_dir
    model_raw = os.environ.get("MIMIC_FULL_MODEL_PATH", "").strip()
    model_path = Path(model_raw).expanduser() if model_raw else None
    model_file_exists = bool(model_path and model_path.exists())

    artefacts: Dict[str, Any] = {}
    artefact_presence: Dict[str, bool] = {}
    artefact_candidates = {
        "model_comparison": ["full_mimic_model_comparison.json"],
        "model_card": ["mimic_full_model_card.json"],
        "dataset_card": ["mimic_full_dataset_card.json"],
        "training_provenance": ["mimic_full_training_provenance.json"],
        "feature_schema": ["mimic_full_feature_schema.json"],
        "under_over_triage": [
            "full_mimic_under_over_triage_report.json",
            "full_mimic_under_over_triage.json",
        ],
        "calibration": [
            "full_mimic_calibration_report.json",
            "full_mimic_calibration.json",
        ],
        "confusion_matrix": ["full_mimic_confusion_matrix.json"],
        "subgroup_metrics": ["full_mimic_subgroup_metrics.json"],
        "class_distribution": ["full_mimic_class_distribution.json"],
        "binary_curve_report": ["selected_model_binary_curve_report.json"],
        "feature_importance": ["full_mimic_feature_importance.json"],
        "selected_feature_importance": ["selected_model_feature_importance.json"],
    }
    extra_expected_files = [
        "full_mimic_class_distribution.csv",
        "all_models_roc_auc_comparison.csv",
        "full_mimic_feature_importance.csv",
        "selected_model_roc_curve.csv",
        "selected_model_pr_curve.csv",
        "selected_model_roc_curve.png",
        "selected_model_pr_curve.png",
    ]

    def _read_curve_csv(
        path: Path,
        *,
        fields: tuple[str, str],
        max_points: int = 800,
    ) -> tuple[list[dict[str, Any]], int]:
        """Read aggregate curve CSV points for UI plotting.

        The on-disk CSV remains the complete record. The API response is
        downsampled so full-MIMIC threshold curves cannot make the status
        endpoint slow or oversized.
        """
        raw_points: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                point: dict[str, Any] = {}
                usable = True
                for field in fields:
                    try:
                        value = float(row.get(field, ""))
                    except (TypeError, ValueError):
                        usable = False
                        break
                    if not math.isfinite(value):
                        usable = False
                        break
                    point[field] = value
                if not usable:
                    continue
                threshold_raw = row.get("threshold")
                threshold = None
                if threshold_raw not in (None, ""):
                    try:
                        parsed_threshold = float(threshold_raw)
                    except (TypeError, ValueError):
                        parsed_threshold = None
                    if parsed_threshold is not None and math.isfinite(parsed_threshold):
                        threshold = parsed_threshold
                point["threshold"] = threshold
                raw_points.append(point)

        raw_count = len(raw_points)
        if raw_count > max_points and max_points > 1:
            step = (raw_count - 1) / float(max_points - 1)
            indexes = sorted({
                0,
                raw_count - 1,
                *{min(raw_count - 1, int(round(i * step))) for i in range(max_points)},
            })
            raw_points = [raw_points[i] for i in indexes]
        return raw_points, raw_count

    selected_artefact_files: Dict[str, str] = {}
    for key, names in artefact_candidates.items():
        found = None
        for fname in names:
            p = report_dir / fname
            artefact_presence[fname] = p.exists()
            if found is None and p.exists():
                found = p
        if found is not None:
            selected_artefact_files[key] = found.name
            try:
                artefacts[key] = orjson.loads(found.read_bytes())
            except Exception:
                artefacts[key] = {"error": "could not parse artefact"}
    for fname in extra_expected_files:
        artefact_presence[fname] = (report_dir / fname).exists()
    curve_specs = {
        "roc_curve": (
            "selected_model_roc_curve.csv",
            ("false_positive_rate", "true_positive_rate"),
            "acuity_1_2_high_acuity_vs_3_5_non_high_acuity",
        ),
        "pr_curve": (
            "selected_model_pr_curve.csv",
            ("recall", "precision"),
            "acuity_1_2_high_acuity_vs_3_5_non_high_acuity",
        ),
    }
    for key, (fname, fields, target) in curve_specs.items():
        p = report_dir / fname
        if not p.exists():
            continue
        selected_artefact_files[key] = fname
        try:
            points, raw_count = _read_curve_csv(p, fields=fields)
            artefacts[key] = {
                "source_file": fname,
                "binary_target": target,
                "points": points,
                "point_count": raw_count,
                "display_point_count": len(points),
                "downsampled": raw_count != len(points),
            }
        except Exception:
            artefacts[key] = {"error": "could not parse curve artefact", "source_file": fname}

    model_card = artefacts.get("model_card") if isinstance(artefacts.get("model_card"), dict) else {}
    model_comparison = (
        artefacts.get("model_comparison")
        if isinstance(artefacts.get("model_comparison"), dict)
        else {}
    )
    training_provenance = (
        artefacts.get("training_provenance")
        if isinstance(artefacts.get("training_provenance"), dict)
        else {}
    )
    dataset_card = (
        artefacts.get("dataset_card")
        if isinstance(artefacts.get("dataset_card"), dict)
        else {}
    )
    feature_schema = (
        artefacts.get("feature_schema")
        if isinstance(artefacts.get("feature_schema"), dict)
        else {}
    )
    try:
        import hashlib, json
        from ml_training.feature_engineering import FEATURE_NAMES
        current_feature_schema_hash = hashlib.sha256(
            json.dumps(list(FEATURE_NAMES), separators=(",", ":"), sort_keys=False).encode("utf-8")
        ).hexdigest()
    except Exception:
        current_feature_schema_hash = None
    expected_sha = os.environ.get("MIMIC_FULL_MODEL_SHA256", "").strip().lower()
    reported_sha = str(
        model_card.get("model_artifact_sha256")
        or model_comparison.get("model_artifact_sha256")
        or ""
    ).strip().lower()
    card_feature_hash = str(model_card.get("feature_schema_hash", "")).strip().lower()
    comparison_feature_hash = str(model_comparison.get("feature_schema_hash", "")).strip().lower()
    provenance_feature_hash = str(training_provenance.get("feature_schema_hash", "")).strip().lower()
    schema_feature_hash = str(feature_schema.get("feature_schema_hash", "")).strip().lower()
    card_run_id = str(model_card.get("training_run_id", "")).strip()
    comparison_run_id = str(model_comparison.get("training_run_id", "")).strip()
    provenance_run_id = str(training_provenance.get("training_run_id", "")).strip()

    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes"}

    provenance_issues = []
    model_readiness_issues = []
    model_artifact_status: Dict[str, Any] = {
        "model_path_env_set": bool(model_raw),
        "model_file_exists": model_file_exists,
        "expected_sha_configured": bool(expected_sha),
        "actual_sha256": "",
        "hash_verified": False,
        "artifact_compatible": False,
        "smoke_prediction_available": False,
        "smoke_prediction_model_name": "",
        "smoke_prediction_note": "",
    }
    if not model_raw:
        model_readiness_issues.append("MIMIC_FULL_MODEL_PATH is not configured")
    elif not model_file_exists:
        model_readiness_issues.append("configured MIMIC_FULL_MODEL_PATH does not exist")
    elif model_path is not None:
        try:
            actual_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
            model_artifact_status["actual_sha256"] = actual_sha
            if expected_sha:
                model_artifact_status["hash_verified"] = actual_sha == expected_sha
                if actual_sha != expected_sha:
                    model_readiness_issues.append(
                        "configured model SHA does not match the actual model file"
                    )
            else:
                model_readiness_issues.append(
                    "MIMIC_FULL_MODEL_SHA256 is not configured; model file is unpinned"
                )
        except Exception as exc:
            model_readiness_issues.append(f"configured model file could not be hashed: {exc}")
        if not model_readiness_issues:
            try:
                from ml_training.full_mimic.check_artifact_compatibility import check_artifact
                compat = check_artifact(model_path)
                model_artifact_status["artifact_compatible"] = bool(compat.get("compatible"))
                model_artifact_status["artifact_compatibility_reason"] = compat.get("reason")
                if not compat.get("compatible"):
                    model_readiness_issues.append(
                        "configured model artifact failed compatibility check: "
                        + str(compat.get("reason"))
                    )
            except Exception as exc:
                model_readiness_issues.append(
                    f"configured model artifact could not be deserialized/checked: {exc}"
                )
        if not model_readiness_issues:
            try:
                from app.agents.ml_prediction_agent import run_ml_prediction
                from app.schemas.internal import TriageTimeInput

                smoke = run_ml_prediction(
                    TriageTimeInput(
                        subject_id=1,
                        stay_id=1,
                        source_dataset="MIMIC-IV-ED-Full-v2.2",
                        gender="F",
                        arrival_transport="WALK IN",
                        chiefcomplaint="CHEST PAIN",
                        temperature=98.6,
                        temperature_unit="F",
                        heartrate=80,
                        resprate=18,
                        o2sat=98,
                        sbp=120,
                        dbp=80,
                        pain="0",
                    )
                )
                model_artifact_status["smoke_prediction_available"] = bool(
                    smoke.prediction_available
                )
                model_artifact_status["smoke_prediction_model_name"] = smoke.model_name
                model_artifact_status["smoke_prediction_note"] = smoke.model_note
                if not smoke.prediction_available:
                    model_readiness_issues.append(
                        "configured model artifact could not complete a smoke prediction: "
                        + str(smoke.model_note)
                    )
            except Exception as exc:
                model_readiness_issues.append(
                    f"configured model artifact smoke prediction raised: {exc}"
                )
    required_report_keys = [
        "model_comparison",
        "model_card",
        "dataset_card",
        "training_provenance",
        "feature_schema",
        "calibration",
        "confusion_matrix",
        "under_over_triage",
        "subgroup_metrics",
        "class_distribution",
        "binary_curve_report",
        "feature_importance",
    ]
    missing_expected_files = [
        fname for fname in extra_expected_files if not artefact_presence.get(fname)
    ]
    missing_required = [key for key in required_report_keys if key not in artefacts]
    if artefacts and missing_required:
        provenance_issues.append(
            "model artefacts incomplete; missing required report(s): "
            + ", ".join(missing_required)
        )
    if artefacts and missing_expected_files:
        provenance_issues.append(
            "model artefacts incomplete; missing expected plot/table file(s): "
            + ", ".join(missing_expected_files)
        )
    for key, payload in artefacts.items():
        if not isinstance(payload, dict):
            continue
        if any(
            _truthy(payload.get(flag))
            for flag in ("synthetic_data_used", "demo_fixture_used", "test_fixture_used")
        ):
            provenance_issues.append(
                f"{key} reports synthetic/demo/test fixture data use; refusing model-readiness display"
            )
        if payload.get("error"):
            provenance_issues.append(f"{key} could not be parsed")
    for key, payload in (
        ("comparison", model_comparison),
        ("model_card", model_card),
        ("dataset_card", dataset_card),
        ("training_provenance", training_provenance),
    ):
        if payload and payload.get("dataset_source") not in (None, "MIMIC-IV-ED-Full-v2.2"):
            provenance_issues.append(f"{key} dataset_source is not full MIMIC-IV-ED")
    for key, payload in (
        ("comparison", model_comparison),
        ("training_provenance", training_provenance),
    ):
        if payload and payload.get("patient_level_split") is not True:
            provenance_issues.append(f"{key} does not prove patient-level split")
        if payload and payload.get("test_set_used_for_model_selection") is not False:
            provenance_issues.append(f"{key} does not prove validation-only model selection")
        if payload and payload.get("preprocessing_inside_pipeline") is not True:
            provenance_issues.append(f"{key} does not prove preprocessing inside estimator pipeline")
        if payload and payload.get("leakage_audit_passed") is not True:
            provenance_issues.append(f"{key} does not prove leakage audit passed")
        if payload and payload.get("synthetic_audit_passed") is not True:
            provenance_issues.append(f"{key} does not prove synthetic/demo path audit passed")
    if model_comparison:
        candidates = model_comparison.get("candidates") or []
        for cand in candidates:
            name = cand.get("model_name", "unknown")
            uot = cand.get("under_over_triage") or {}
            ordinal = cand.get("ordinal_metrics") or {}
            har = cand.get("high_acuity_recall") or {}
            missing_metrics = []
            if "recall" not in har:
                missing_metrics.append("high_acuity_recall")
            for metric in (
                "under_triage_rate",
                "severe_under_triage_rate",
                "over_triage_rate",
            ):
                if metric not in uot:
                    missing_metrics.append(metric)
            for metric in (
                "mae",
                "quadratic_weighted_kappa",
                "within_1_acuity_level_accuracy",
            ):
                if metric not in ordinal:
                    missing_metrics.append(metric)
            if missing_metrics:
                provenance_issues.append(
                    f"candidate {name} missing safety metric(s): "
                    + ", ".join(missing_metrics)
                )
        test_metrics = model_comparison.get("untouched_test_metrics") or {}
        test_uot = test_metrics.get("under_over_triage") or {}
        test_ordinal = test_metrics.get("ordinal_metrics") or {}
        for metric in ("severe_under_triage_rate", "under_triage_rate"):
            if metric not in test_uot:
                provenance_issues.append(
                    f"untouched test metrics missing {metric}"
                )
        for metric in (
            "mae",
            "quadratic_weighted_kappa",
            "within_1_acuity_level_accuracy",
        ):
            if metric not in test_ordinal:
                provenance_issues.append(
                    f"untouched test metrics missing {metric}"
                )
    if expected_sha and reported_sha and expected_sha != reported_sha:
        provenance_issues.append("configured model SHA does not match report/model-card SHA")
    if not expected_sha:
        provenance_issues.append("MIMIC_FULL_MODEL_SHA256 is not configured; model/report freshness is unpinned")
    if model_readiness_issues:
        provenance_issues.extend(model_readiness_issues)
    if current_feature_schema_hash:
        hashes = [
            h for h in (
                card_feature_hash,
                comparison_feature_hash,
                provenance_feature_hash,
                schema_feature_hash,
            )
            if h
        ]
        if not hashes:
            provenance_issues.append("feature_schema_hash missing from report/model card")
        elif any(h != current_feature_schema_hash for h in hashes):
            provenance_issues.append("feature_schema_hash does not match current serving FEATURE_NAMES")
    if card_feature_hash and comparison_feature_hash and card_feature_hash != comparison_feature_hash:
        provenance_issues.append("model-card and comparison feature_schema_hash differ")
    run_ids = [r for r in (card_run_id, comparison_run_id, provenance_run_id) if r]
    if len(set(run_ids)) > 1:
        provenance_issues.append("model-card, comparison, and provenance training_run_id differ")
    if artefacts and (not card_run_id or not comparison_run_id or not provenance_run_id):
        provenance_issues.append("training_run_id missing from one or more model artefacts")
    stale_report_detected = bool(provenance_issues)
    model_readiness_valid = (
        bool(artefacts)
        and not stale_report_detected
        and not model_readiness_issues
    )

    if not artefacts:
        return {
            "status": "not_available",
            "app_version": APP_VERSION,
            "package_checkpoint": PACKAGE_CHECKPOINT,
            "reason": "Full-MIMIC metrics have not been generated in this environment",
            "data_dir_configured": bool(diag.get("mimic_full_dir_env_set")),
            "data_loadable": bool(diag.get("full_mimic_loadable")),
            "model_path_env_set": bool(model_raw),
            "model_file_exists": model_file_exists,
            "model_hash_configured": bool(expected_sha),
            "model_artifact_status": model_artifact_status,
            "report_dir_env_set": bool(report_raw),
            "report_artefacts_present": artefact_presence,
            "stale_report_detected": stale_report_detected,
            "model_readiness_valid": False,
            "model_provenance_status": "unknown" if provenance_issues else "not_available",
            "model_provenance_issues": provenance_issues,
            "current_feature_schema_hash": current_feature_schema_hash,
            "expected_report_dir_env": "MIMIC_FULL_MODEL_REPORT_DIR, MIMIC_FULL_REPORT_DIR, or MIMIC_FULL_OUTPUT_DIR",
            "expected_artefacts": [
                "full_mimic_model_comparison.json",
                "full_mimic_model_comparison.csv",
                "mimic_full_model_card.json",
                "mimic_full_dataset_card.json",
                "mimic_full_training_provenance.json",
                "mimic_full_feature_schema.json",
                "full_mimic_under_over_triage_report.json",
                "full_mimic_calibration_report.json",
                "full_mimic_confusion_matrix.json",
                "full_mimic_subgroup_metrics.json",
                "full_mimic_class_distribution.json",
                "full_mimic_class_distribution.csv",
                "all_models_roc_auc_comparison.csv",
                "full_mimic_feature_importance.json",
                "full_mimic_feature_importance.csv",
                "selected_model_feature_importance.json",
                "selected_model_binary_curve_report.json",
                "selected_model_roc_curve.csv",
                "selected_model_pr_curve.csv",
                "selected_model_roc_curve.png",
                "selected_model_pr_curve.png",
            ],
            "note": "Aggregate full-MIMIC research metrics only. Not clinically validated.",
        }

    return {
        "status": "available" if model_readiness_valid else "invalid_provenance",
        "app_version": APP_VERSION,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "dataset": "MIMIC-IV-ED-Full-v2.2",
        "data_dir_configured": bool(diag.get("mimic_full_dir_env_set")),
        "data_loadable": bool(diag.get("full_mimic_loadable")),
        "model_path_env_set": bool(model_raw),
        "model_file_exists": model_file_exists,
        "model_hash_configured": bool(expected_sha),
        "model_artifact_status": model_artifact_status,
        "report_dir_env_set": bool(report_raw),
        "report_artefacts_present": artefact_presence,
        "selected_artefact_files": selected_artefact_files,
        "stale_report_detected": stale_report_detected,
        "model_readiness_valid": model_readiness_valid,
        "model_provenance_status": "stale_or_unpinned" if provenance_issues else "verified",
        "model_provenance_issues": provenance_issues,
        "current_feature_schema_hash": current_feature_schema_hash,
        "artefacts": artefacts if model_readiness_valid else {},
        "reason": (
            "Model artefacts incomplete or invalid for model-readiness claim."
            if not model_readiness_valid else "ok"
        ),
        "note": "Aggregate full-MIMIC research metrics only. Not clinically validated.",
    }
