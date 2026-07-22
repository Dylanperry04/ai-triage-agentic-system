"""Local/Azure preflight checks for the full-MIMIC research app."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


def _autogen_importable() -> bool:
    try:
        import autogen_agentchat  # noqa: F401
        import autogen_core  # noqa: F401
        import autogen_ext.models.openai  # noqa: F401
        return True
    except ImportError:
        return False


def main() -> int:
    from app.agents.autogen_team import load_azure_config
    from app.rules.manchester_engine import get_approved_ruleset
    from app.rules.provisional_mts_ruleset import register_provisional_ruleset

    # Mirror app startup so the reported provisional state matches the app.
    if settings.provisional_mts_mode:
        register_provisional_ruleset()
    ruleset = get_approved_ruleset()
    provisional_active = bool(ruleset) and ruleset.get(
        "validation_status"
    ) != "CLINICALLY_APPROVED"

    # Functional checks: the deployment-critical behaviours, not just file
    # existence. The only live prediction path is full MIMIC-IV-ED
    # (credentialed). If the registry/manifest claims a model is bundled, this
    # preflight verifies that exact file and hash; otherwise the router must fail
    # closed without a model.
    registry_is_full_mimic_only = False
    bundled_model_artifact_present_or_not_claimed = False
    model_router_fails_closed = False
    safety_layer_fires = False
    bundled_model_detail = {}
    try:
        reg = json.load(open(settings.model_registry_path))
        registry_is_full_mimic_only = (
            reg.get("dataset") == "MIMIC-IV-ED-Full-v2.2"
            and not any("ktas" in k.lower() or "demo" in k.lower() for k in reg.keys())
        )
        model_meta = reg.get("full_mimic_model") or {}
        bundled_rel = str(model_meta.get("bundled_relative_path") or "").strip()
        claims_bundled = (
            str(model_meta.get("status") or "").lower() == "bundled_in_azure_deployment_bundle"
            or bool(bundled_rel)
        )
        bundled_model_artifact_present_or_not_claimed = not claims_bundled
        if claims_bundled:
            import hashlib
            model_path = PROJECT_ROOT / bundled_rel
            expected_sha = str(model_meta.get("sha256") or "").lower()
            actual_sha = ""
            if model_path.exists():
                actual_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
            bundled_model_artifact_present_or_not_claimed = (
                model_path.exists()
                and (not expected_sha or actual_sha.lower() == expected_sha)
            )
            bundled_model_detail = {
                "bundled_relative_path": bundled_rel,
                "exists": model_path.exists(),
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
            }
    except Exception:
        registry_is_full_mimic_only = False
        bundled_model_artifact_present_or_not_claimed = False

    try:
        import os as _os
        _os.environ.pop("MIMIC_FULL_MODEL_PATH", None)
        from app.agents.ml_prediction_agent import run_ml_prediction
        from app.schemas.workflow import TriageTimeInput
        r_full = run_ml_prediction(TriageTimeInput(
            subject_id=1, stay_id=1, source_dataset="MIMIC-IV-ED-Full-v2.2",
            chiefcomplaint="x"))
        r_other = run_ml_prediction(TriageTimeInput(
            subject_id=1, stay_id=1, source_dataset="Some-Retired-Dataset", chiefcomplaint="x"))
        model_router_fails_closed = (
            r_full.prediction_available is False
            and r_other.prediction_available is False
            and r_other.model_name == "no_model_for_dataset"
        )
    except Exception:
        model_router_fails_closed = False

    try:
        from app.agents.orchestrator import run_workflow
        from app.schemas.internal import EDTriageCase
        # Synthetic MIMIC-shaped case with critical physiology (no demo data needed).
        synth = EDTriageCase(**{
            "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 1, "subject_id": 1,
            "edstay": {"subject_id": 1, "stay_id": 1, "gender": "F",
                       "arrival_transport": "AMBULANCE", "disposition": "HOME"},
            "triage": {"subject_id": 1, "stay_id": 1, "heartrate": 195.0,
                       "o2sat": 80.0, "sbp": 70.0, "chiefcomplaint": "COLLAPSE",
                       "acuity": None},
            "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
        })
        wf = run_workflow(synth)
        _dec = wf.decision.model_dump()
        _status = str(_dec.get("classification_status", ""))
        _reasons = " ".join(str(r) for r in (_dec.get("reason_codes") or []))
        # Critical physiology must be surfaced regardless of which ruleset string
        # is active (CRITICAL_PHYSIOLOGY_FLAGGED, or provisional-MTS status with
        # critical reason codes), and clinician review must be required.
        safety_layer_fires = (
            ("CRITICAL" in _status or "CRITICAL" in _reasons
             or "HYPOTENSION" in _reasons or "HYPOXIA" in _reasons)
            and wf.decision.requires_clinician_review is True)
    except Exception:
        safety_layer_fires = False

    dockerfile = PROJECT_ROOT / "Dockerfile"
    _df_text = dockerfile.read_text(encoding="utf-8") if dockerfile.exists() else ""
    # v18 architecture: ONE service. The FastAPI backend serves the built React
    # UI (frontend-react/dist) itself; the retired Streamlit frontend remains in
    # the repo for reference and is reported as an optional legacy check only.
    _backend_sh = (PROJECT_ROOT / "startup-backend.sh")
    _frontend_sh = (PROJECT_ROOT / "startup-frontend.sh")
    _backend_txt = _backend_sh.read_text(encoding="utf-8") if _backend_sh.exists() else ""
    _frontend_txt = _frontend_sh.read_text(encoding="utf-8") if _frontend_sh.exists() else ""
    _main_py = (PROJECT_ROOT / "app" / "main.py")
    _main_txt = _main_py.read_text(encoding="utf-8") if _main_py.exists() else ""
    react_ui_built = (PROJECT_ROOT / "frontend-react" / "dist" / "index.html").exists()
    backend_serves_react_ui = (
        "frontend-react" in _main_txt
        and "SERVE_REACT_UI" in _main_txt
        and "uvicorn app.main:app" in _backend_txt
        and '--host "${BACKEND_BIND_HOST}"' in _backend_txt
    )
    dockerfile_ships_react_ui = "frontend-react/dist" in _df_text
    dockerfile_ships_demo_and_model_artefacts = (
        "data/demo" in _df_text and "model_outputs/last_training" in _df_text
    )
    legacy_streamlit_service_intact = (
        "SERVICE_ROLE" in _df_text
        and "startup-frontend.sh" in _df_text
        and "streamlit run frontend/app.py" in _frontend_txt
    )
    dockerfile_installs_autogen = (
        "requirements-autogen.txt" in _df_text
        and "pip install --no-cache-dir -r requirements-autogen.txt" in _df_text
    )
    backend_startup_exists = (PROJECT_ROOT / "startup-backend.sh").exists()
    frontend_startup_exists = (PROJECT_ROOT / "startup-frontend.sh").exists()

    # Patient-data-mode safety posture (reported, and required to be safe IF in
    # patient-data mode). In demo mode these are informational.
    import os as _os
    from app.security.security_status import unsafe_combinations, build_security_status
    _ss = build_security_status()
    _patient = _ss["patient_data_mode"]
    _unsafe = unsafe_combinations()
    # Legacy raw-ID routes must NOT be registered in either credentialed-data mode.
    legacy_blocked_in_patient_mode = (not _patient) or (
        _os.environ.get("ALLOW_LEGACY_RAW_ID_ROUTES", "false").lower() != "true")
    _local_research = (
        _os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        and not _patient
    )
    legacy_blocked_in_credentialed_mode = (not (_patient or _local_research)) or (
        _os.environ.get("ALLOW_LEGACY_RAW_ID_ROUTES", "false").lower() != "true")

    required_checks = {
        "model_registry_exists": settings.model_registry_path.exists(),
        # The only prediction dataset is full MIMIC-IV-ED (credentialed). The
        # registry must be full-MIMIC-only and the router must fail closed.
        "registry_is_full_mimic_only": registry_is_full_mimic_only,
        "bundled_model_artifact_present_or_not_claimed": (
            bundled_model_artifact_present_or_not_claimed
        ),
        "model_router_fails_closed": model_router_fails_closed,
        "deterministic_safety_layer_fires": safety_layer_fires,
        # v18 single-service architecture: FastAPI serves the built React UI.
        "react_ui_built": react_ui_built,
        "backend_serves_react_ui": backend_serves_react_ui,
        "dockerfile_ships_react_ui": dockerfile_ships_react_ui,
        "dockerfile_ships_demo_and_model_artefacts": dockerfile_ships_demo_and_model_artefacts,
        "dockerfile_installs_autogen_runtime_deps": dockerfile_installs_autogen,
        "backend_startup_script_exists": backend_startup_exists,
        # Patient-data safety: if NOT in patient-data mode this is trivially true;
        # if in patient-data mode, the config must be safe and legacy routes blocked.
        "patient_data_config_safe_or_demo": (not _patient) or (len(_unsafe) == 0),
        "legacy_raw_id_routes_blocked_in_patient_mode": legacy_blocked_in_patient_mode,
        "legacy_raw_id_routes_blocked_in_credentialed_mode": legacy_blocked_in_credentialed_mode,
    }
    import os as _os_profile
    release_profile = _os_profile.environ.get("ALTER_RELEASE_PROFILE", "demo").strip().lower()
    agentic_release = release_profile == "agentic"
    autogen_ok = _autogen_importable()
    azure_openai_ok = load_azure_config() is not None

    if agentic_release:
        # Agentic release profile: the multi-agent explanation layer must be
        # genuinely deployable, so its runtime + config are REQUIRED. Use
        # ALTER_RELEASE_PROFILE=agentic only when you intend to ship the
        # multi-agent feature; the default demo profile keeps them optional.
        required_checks["agentic_autogen_runtime_importable"] = autogen_ok
        required_checks["agentic_azure_openai_configured"] = azure_openai_ok

    optional_checks = {
        # AutoGen is optional in the default (demo) profile: if imports are
        # absent, chat/explanation degrades to NOT_CONFIGURED and this must not
        # fail the whole preflight. In the agentic profile it is required above.
        "autogen_importable": autogen_ok,
        "azure_openai_configured": azure_openai_ok,
        # Retired Streamlit path kept in-repo for reference; informational only.
        "legacy_streamlit_service_intact": legacy_streamlit_service_intact,
        "legacy_frontend_startup_script_exists": frontend_startup_exists,
    }
    checks = {**required_checks, **optional_checks}
    result = {
        "status": "PASS" if all(required_checks.values()) else "FAIL",
        "checks": checks,
        "required_checks": required_checks,
        "optional_checks": optional_checks,
        "warnings": [
            name for name, ok in optional_checks.items() if not ok
        ],
        "release_profile": release_profile,
        "agentic_release_requirements_enforced": agentic_release,
        "deployment_target": "single_service_fastapi_serving_react_ui",
        "architecture": {
            "frontend": "react (frontend-react/, built to frontend-react/dist), served by the backend at '/'",
            "legacy_frontend": "streamlit (frontend/app.py) retired; kept in-repo for reference",
            "backend": "fastapi (app.main:app), sole server-side enforcement boundary + ML workflow; serves the UI",
            "external_identifier": "pseudonymous case_uid (no raw stay_id/subject_id in the public API)",
            "patient_data_mode_requires": [
                "Entra auth via trusted proxy", "FASTAPI_BASE_URL on the frontend",
                "Key Vault secrets", "durable audit sink", "non-wildcard CORS",
                "legacy raw-ID routes disabled",
            ],
        },
        "patient_data_mode": _patient,
        "patient_data_unsafe_combinations": _unsafe,
        "bundled_model_artifact": bundled_model_detail,
        "default_dataset": "MIMIC-IV-ED-Full-v2.2",
        "datasets_available": ["MIMIC-IV-ED-Full-v2.2"],
        "datasets_kept_separate": True,
        "clinical_use": "not_for_clinical_use",
        "official_manchester_triage": "not_implemented",
        "provisional_mts_mode": "enabled" if provisional_active else "disabled",
        "official_mts_ruleset": False,
        "clinically_approved_ruleset": False,
        "ktas_ml_applied_to_mimic": False,
        "full_credentialed_mimic_expected": False,
        "azure_openai_configured": load_azure_config() is not None,
        "azure_openai_note": (
            "Optional. If not configured, the AutoGen chat/explanation layer "
            "degrades to NOT_CONFIGURED; the rest of the app works."
        ),
    }
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
