"""
Security-status, audit-events, and model-performance endpoints.

  GET /security/status   (RBAC: view_security_status)
  GET /audit/events      (RBAC: view_audit_log)
  GET /model/performance (RBAC: view_model_performance)

All enforce requires(permission), audit the access, and never expose secrets,
the full-MIMIC path, or raw identifiers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_dependencies import requires
from app.security import authz
from app.security.security_status import build_security_status
from app.config import settings

router = APIRouter()


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
    import orjson
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
    path = _audit_path()
    events: List[Dict[str, Any]] = []
    if path.exists():
        with path.open("rb") as f:
            for line in f:
                if line.strip():
                    events.append(orjson.loads(line))
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
        raise HTTPException(
            status_code=503,
            detail=(
                "Detailed audit-record reads require a wired durable audit client "
                "in patient-data mode; local JSONL reads are refused."
            ),
        )

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


@router.get("/model/performance",
            dependencies=[Depends(requires(authz.PERM_VIEW_MODEL_PERFORMANCE, "view_model_performance"))])
def model_performance() -> Dict[str, Any]:
    """Return full-MIMIC aggregate model-performance artefacts, if generated on
    the credentialed/approved environment. No retired demo/KTAS files are read."""
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
    model_file_exists = bool(model_raw and Path(model_raw).expanduser().exists())

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
    }
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
    ]
    missing_required = [key for key in required_report_keys if key not in artefacts]
    if artefacts and missing_required:
        provenance_issues.append(
            "model artefacts incomplete; missing required report(s): "
            + ", ".join(missing_required)
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
    model_readiness_valid = bool(artefacts) and not stale_report_detected

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
