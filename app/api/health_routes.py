from fastapi import APIRouter
from app.version import APP_VERSION, PACKAGE_CHECKPOINT

from app.rules.manchester_engine import get_approved_ruleset

router = APIRouter()


def _redacted_path_state(raw: str) -> str:
    return "redacted" if raw else "not_set"


def _report_dir() -> tuple[object, bool]:
    import os
    from pathlib import Path
    raw = (
        os.environ.get("MIMIC_FULL_MODEL_REPORT_DIR", "").strip()
        or os.environ.get("MIMIC_FULL_REPORT_DIR", "").strip()
        or os.environ.get("MIMIC_FULL_OUTPUT_DIR", "").strip()
    )
    return (Path(raw).expanduser() if raw else None, bool(raw))


@router.get("/health")
def health():
    ruleset = get_approved_ruleset()
    provisional_active = bool(ruleset) and ruleset.get(
        "validation_status"
    ) != "CLINICALLY_APPROVED"
    from app.data_pipeline.mimic_full_loader import full_mimic_status
    _fm = full_mimic_status()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "clinical_use": "not_for_clinical_use",
        "default_dataset": "MIMIC-IV-ED-Full-v2.2",
        "datasets_available": ["MIMIC-IV-ED-Full-v2.2"],
        "mimic_full_dir_configured": _fm["mimic_full_dir_configured"],
        "mimic_full_model_configured": _fm["mimic_full_model_configured"],
        "prediction_model_source": "MIMIC_FULL_MODEL_PATH",
        "patient_data_ready": False,
        "patient_data_ready_requires": [
            "PATIENT_DATA_MODE=true",
            "real Entra/SSO and trusted auth proxy",
            "Key Vault secret read and Managed Identity",
            "durable audit write/read",
            "private ingress/network controls",
            "non-wildcard CORS",
            "model artefact hash verification",
            "governance, DPIA, and security approval evidence",
        ],
        "official_manchester_triage": "not_implemented",
        "provisional_mts_mode": "enabled" if provisional_active else "disabled",
        "official_mts_ruleset": False,
        "clinically_approved_ruleset": False,
        "rules_status": (
            "PROVISIONAL_MTS_RESEARCH_RULESET_ACTIVE"
            if provisional_active
            else "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
        ),
        "human_review_required": True,
    }


@router.get("/status/full-mimic")
def full_mimic_status_endpoint():
    """Non-sensitive diagnostic of full-MIMIC configuration, for the frontend to
    display backend status (the frontend must NOT inspect its own local env in
    two-service mode). Returns the active profile, whether the dir/model are
    configured, whether full MIMIC is loadable, and a category reason. Never
    exposes the path value or any patient data."""
    from app.data_pipeline.mimic_full_loader import full_mimic_diagnostic
    return full_mimic_diagnostic()


@router.get("/status/llm")
def llm_status_endpoint():
    """Non-secret backend status for the explanation LLM/cloud-egress path.

    The frontend should display this backend view rather than reading its own
    environment, because the FastAPI process is the authority on whether LLM
    calls are configured and allowed.
    """
    import os

    from app.agents.autogen_team import load_azure_config
    from app.security.identity import (
        azure_supervisor_demo_mode,
        cloud_egress_allowed,
        credentialed_mimic_active_or_requested,
        local_credentialed_research_mode,
        patient_data_mode,
        real_mimic_azure_demo_mode,
    )

    required = [
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
    ]
    env_present = {name: bool(os.environ.get(name)) for name in required}
    egress = cloud_egress_allowed()
    credentials_detected = all(env_present.values())
    config_present = load_azure_config() is not None
    try:
        import autogen_core  # noqa: F401
        autogen_installed = True
    except Exception:
        autogen_installed = False
    try:
        import autogen_agentchat  # noqa: F401
        autogen_agentchat_installed = True
    except Exception:
        autogen_agentchat_installed = False
    try:
        import openai  # noqa: F401
        azure_openai_client_importable = True
    except Exception:
        azure_openai_client_importable = False
    credentialed_mimic_active = credentialed_mimic_active_or_requested()
    if not egress:
        if real_mimic_azure_demo_mode():
            reason = (
                "Azure OpenAI credentials are present, but cloud LLM is blocked "
                "because credentialed MIMIC data is active and approval flags are "
                "not set."
                if credentials_detected
                else "cloud LLM is blocked because credentialed MIMIC data is active."
            )
        else:
            reason = (
                "Azure OpenAI credentials are present but cloud LLM egress is blocked "
                "in LOCAL_CREDENTIALED_RESEARCH"
                if credentials_detected
                else "cloud LLM egress is blocked in LOCAL_CREDENTIALED_RESEARCH"
            )
    elif not all(env_present.values()):
        reason = "one or more Azure OpenAI environment variables are missing"
    elif not autogen_installed or not autogen_agentchat_installed:
        reason = "AutoGen dependency is missing"
    elif not azure_openai_client_importable:
        reason = "Azure OpenAI client package is missing"
    else:
        reason = "ok"
    blocked_by_local_research = (
        local_credentialed_research_mode()
        and not egress
    )
    active_profile = (
        "secured_research"
        if patient_data_mode()
        else "local_credentialed_research"
        if local_credentialed_research_mode()
        else "azure_supervisor_demo"
        if azure_supervisor_demo_mode()
        else "public_demo"
    )
    return {
        "active_profile": active_profile,
        "cloud_egress_allowed": egress,
        "azure_config_present": config_present,
        "azure_credentials_present": credentials_detected,
        "endpoint_present": env_present["AZURE_OPENAI_ENDPOINT"],
        "api_key_present": env_present["AZURE_OPENAI_API_KEY"],
        "deployment_present": env_present["AZURE_OPENAI_DEPLOYMENT"],
        "api_version_present": env_present["AZURE_OPENAI_API_VERSION"],
        "autogen_installed": autogen_installed,
        "autogen_agentchat_installed": autogen_agentchat_installed,
        "azure_openai_client_importable": azure_openai_client_importable,
        "credentialed_mimic_active": credentialed_mimic_active,
        "blocked_by_local_credentialed_research": blocked_by_local_research,
        "blocked_by_credentialed_mimic_cloud_policy": (
            credentialed_mimic_active and not egress
        ),
        "required_enable_flags": {
            "ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH": bool(
                os.environ.get("ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH", "").lower() == "true"
            ),
            "ALLOW_CLOUD_LLM_WITH_CREDENTIALED_MIMIC": bool(
                os.environ.get("ALLOW_CLOUD_LLM_WITH_CREDENTIALED_MIMIC", "").lower() == "true"
            ),
            "APPROVED_CLOUD_LLM_DATA_PROCESSING": bool(
                os.environ.get("APPROVED_CLOUD_LLM_DATA_PROCESSING", "").lower() == "true"
            ),
        },
        "required_env_present": env_present,
        "status": "configured" if config_present else "not_configured",
        "reason_if_disabled": reason,
        "next_steps": (
            "Cloud LLM is disabled for local credentialed MIMIC by default. Set "
            "both ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH=true and "
            "APPROVED_CLOUD_LLM_DATA_PROCESSING=true only after approval of "
            "zero-retention/no-training/no-human-review provider terms."
            if blocked_by_local_research
            else ""
        ),
    }


@router.get("/runtime/status")
def runtime_status_endpoint():
    """Precise, redacted runtime status for data/model/report wiring."""
    import hashlib
    import os
    from pathlib import Path

    from app.data_pipeline.mimic_full_loader import (
        EXPECTED_TABLE_NAMES,
        _table_file_path,
        full_mimic_diagnostic,
    )

    diag = full_mimic_diagnostic()
    ed_raw = os.environ.get("MIMIC_FULL_ED_DIR", "").strip()
    path_exists = False
    is_dir = False
    required_files_present = False
    permission_denied = False
    try:
        if ed_raw:
            ed = Path(ed_raw).expanduser()
            path_exists = ed.exists()
            is_dir = ed.is_dir()
            if is_dir:
                required_files_present = all(
                    _table_file_path(ed, t) is not None for t in ("edstays", "triage")
                )
    except PermissionError:
        permission_denied = True
    except Exception:
        pass

    if not ed_raw:
        mimic_state = "not configured"
    elif permission_denied:
        mimic_state = "permission denied"
    elif not path_exists:
        mimic_state = "path configured but not readable"
    elif not is_dir:
        mimic_state = "path configured but not a directory"
    elif not required_files_present:
        mimic_state = "path readable but no valid files found"
    elif diag.get("full_mimic_loadable"):
        mimic_state = "loaded successfully"
    else:
        mimic_state = "path readable but not loadable"

    case_count = None
    if diag.get("full_mimic_loadable"):
        try:
            from app.api import case_resolver
            case_count = case_resolver.count_cases("mimic_full")
        except Exception:
            case_count = None

    model_raw = os.environ.get("MIMIC_FULL_MODEL_PATH", "").strip()
    hash_raw = os.environ.get("MIMIC_FULL_MODEL_SHA256", "").strip().lower()
    model_exists = False
    model_hash_actual = ""
    hash_verified = False
    model_loadable = False
    model_state = "not configured"
    if model_raw:
        try:
            model_path = Path(model_raw).expanduser()
            model_exists = model_path.exists()
            if not model_exists:
                model_state = "path configured but file missing"
            elif not hash_raw:
                model_state = "file present but hash missing"
            else:
                model_hash_actual = hashlib.sha256(model_path.read_bytes()).hexdigest()
                hash_verified = model_hash_actual == hash_raw
                model_state = "loadable" if hash_verified else "hash mismatch"
                model_loadable = bool(hash_verified)
        except PermissionError:
            model_state = "permission denied"
        except Exception:
            model_state = "path configured but not readable"

    report_dir, report_env_present = _report_dir()
    report_files = {
        "comparison_report_present": "full_mimic_model_comparison.json",
        "model_card_present": "mimic_full_model_card.json",
        "dataset_card_present": "mimic_full_dataset_card.json",
        "feature_schema_present": "mimic_full_feature_schema.json",
        "calibration_report_present": "full_mimic_calibration_report.json",
        "confusion_matrix_present": "full_mimic_confusion_matrix.json",
        "under_over_triage_report_present": "full_mimic_under_over_triage_report.json",
        "subgroup_metrics_present": "full_mimic_subgroup_metrics.json",
        "training_provenance_present": "mimic_full_training_provenance.json",
    }
    reports = {
        "env_present": report_env_present,
        "path": _redacted_path_state(str(report_dir) if report_dir else ""),
    }
    for key, fname in report_files.items():
        reports[key] = bool(report_dir and (report_dir / fname).exists())

    return {
        "app_version": APP_VERSION,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "active_profile": diag.get("active_profile"),
        "mimic_full": {
            "env_present": bool(ed_raw),
            "path": _redacted_path_state(ed_raw),
            "path_exists": path_exists,
            "required_files_present": required_files_present,
            "expected_tables": EXPECTED_TABLE_NAMES,
            "loadable": bool(diag.get("full_mimic_loadable")),
            "state": mimic_state,
            "reason": diag.get("reason"),
            "case_count": case_count,
            "full_mimic_requested_for_azure_demo": bool(
                diag.get("full_mimic_requested_for_azure_demo")
            ),
            "real_mimic_demo_acknowledged": bool(
                diag.get("real_mimic_demo_acknowledged")
            ),
        },
        "model": {
            "env_present": bool(model_raw),
            "path": _redacted_path_state(model_raw),
            "file_exists": model_exists,
            "hash_present": bool(hash_raw),
            "hash_verified": hash_verified,
            "loadable": model_loadable,
            "state": model_state,
        },
        "reports": reports,
    }


@router.get("/status/assessment-cache-key")
def assessment_cache_key_endpoint():
    """Return a backend-generated cache key for assessment DTOs.

    Streamlit caches assessment responses per selected case. The key changes
    when the backend version, configured model artefact, or feature schema
    changes, so a running UI does not keep showing a stale assessment after a
    model swap.
    """
    import hashlib
    import json
    import os
    from pathlib import Path

    try:
        from ml_training.feature_engineering import FEATURE_NAMES
        feature_schema_hash = hashlib.sha256(
            json.dumps(
                list(FEATURE_NAMES),
                separators=(",", ":"),
                sort_keys=False,
            ).encode("utf-8")
        ).hexdigest()
    except Exception:
        feature_schema_hash = "feature-schema-unavailable"

    model_raw = os.environ.get("MIMIC_FULL_MODEL_PATH", "").strip()
    expected_sha = os.environ.get("MIMIC_FULL_MODEL_SHA256", "").strip().lower()
    model_component = "model:not-configured"
    if expected_sha:
        model_component = f"model-sha:{expected_sha}"
    elif model_raw:
        try:
            st = Path(model_raw).expanduser().stat()
            model_component = f"model-stat:{st.st_size}:{st.st_mtime_ns}"
        except Exception:
            model_component = "model:configured-but-not-stat-able"

    material = {
        "backend_version": APP_VERSION,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "feature_schema_hash": feature_schema_hash,
        "model_component": model_component,
    }
    cache_key = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "assessment_cache_key": cache_key,
        "backend_version": material["backend_version"],
        "package_checkpoint": material["package_checkpoint"],
        "feature_schema_hash": feature_schema_hash,
        "model_hash_configured": bool(expected_sha),
        "model_path_env_set": bool(model_raw),
    }
