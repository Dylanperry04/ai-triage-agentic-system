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

    # Functional checks: the deployment-critical behaviours, not just file existence.
    # The only live prediction path is full MIMIC-IV-ED (credentialed), referenced
    # via MIMIC_FULL_MODEL_PATH and absent from this repo. We therefore do NOT
    # require a loadable model in this environment; instead we verify (a) the
    # registry is full-MIMIC-only (no retired demo/KTAS entries), (b) the model
    # router fails closed for full-MIMIC without a model and for any other dataset,
    # and (c) the deterministic safety layer fires on critical physiology.
    registry_is_full_mimic_only = False
    model_router_fails_closed = False
    safety_layer_fires = False
    try:
        reg = json.load(open(settings.model_registry_path))
        registry_is_full_mimic_only = (
            reg.get("dataset") == "MIMIC-IV-ED-Full-v2.2"
            and not any("ktas" in k.lower() or "demo" in k.lower() for k in reg.keys())
        )
    except Exception:
        registry_is_full_mimic_only = False

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
    # Final architecture: the image dispatches on SERVICE_ROLE to run EITHER the
    # FastAPI backend or the Streamlit frontend (two-service deployment). The
    # actual server commands live in the startup scripts the CMD dispatches to.
    _backend_sh = (PROJECT_ROOT / "startup-backend.sh")
    _frontend_sh = (PROJECT_ROOT / "startup-frontend.sh")
    _backend_txt = _backend_sh.read_text(encoding="utf-8") if _backend_sh.exists() else ""
    _frontend_txt = _frontend_sh.read_text(encoding="utf-8") if _frontend_sh.exists() else ""
    dockerfile_two_service = (
        "SERVICE_ROLE" in _df_text
        and "startup-backend.sh" in _df_text
        and "startup-frontend.sh" in _df_text
        and "uvicorn app.main:app" in _backend_txt
        and '--host "${BACKEND_BIND_HOST}"' in _backend_txt
        and "streamlit run frontend/app.py" in _frontend_txt
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
        "model_router_fails_closed": model_router_fails_closed,
        "deterministic_safety_layer_fires": safety_layer_fires,
        # Final two-service architecture (Streamlit frontend + FastAPI backend)
        "dockerfile_is_two_service": dockerfile_two_service,
        "backend_startup_script_exists": backend_startup_exists,
        "frontend_startup_script_exists": frontend_startup_exists,
        # Patient-data safety: if NOT in patient-data mode this is trivially true;
        # if in patient-data mode, the config must be safe and legacy routes blocked.
        "patient_data_config_safe_or_demo": (not _patient) or (len(_unsafe) == 0),
        "legacy_raw_id_routes_blocked_in_patient_mode": legacy_blocked_in_patient_mode,
        "legacy_raw_id_routes_blocked_in_credentialed_mode": legacy_blocked_in_credentialed_mode,
    }
    optional_checks = {
        # AutoGen is optional: if imports are absent, chat/explanation degrades to
        # NOT_CONFIGURED; this must not fail the whole preflight.
        "autogen_importable": _autogen_importable(),
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
        "deployment_target": "two_service_streamlit_frontend_plus_fastapi_backend",
        "architecture": {
            "frontend": "streamlit (frontend/app.py), calls backend over HTTP via FASTAPI_BASE_URL",
            "backend": "fastapi (app.main:app), sole server-side enforcement boundary + ML workflow",
            "external_identifier": "pseudonymous case_uid (no raw stay_id/subject_id in the public API)",
            "patient_data_mode_requires": [
                "Entra auth via trusted proxy", "FASTAPI_BASE_URL on the frontend",
                "Key Vault secrets", "durable audit sink", "non-wildcard CORS",
                "legacy raw-ID routes disabled",
            ],
        },
        "patient_data_mode": _patient,
        "patient_data_unsafe_combinations": _unsafe,
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
