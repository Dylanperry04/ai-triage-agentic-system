"""Local/Azure preflight checks for the 22.4 UHL data/model-swap release."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from ml_training.uhl_synthetic.dataset import DATASET_SOURCE
from ml_training.uhl_synthetic.serving import validate_uhl_serving_bundle


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _autogen_importable() -> bool:
    try:
        import autogen_agentchat  # noqa: F401
        import autogen_core  # noqa: F401
        import autogen_ext.models.openai  # noqa: F401
        return True
    except ImportError:
        return False


def main() -> int:
    import joblib

    from app.agents.autogen_team import load_azure_config
    from app.security.security_status import build_security_status, unsafe_combinations

    dataset_path = settings.uhl_data_path
    model_path = settings.uhl_model_path
    report_dir = settings.uhl_report_dir

    registry_ok = False
    registry_detail: dict[str, object] = {}
    try:
        registry = json.loads(settings.model_registry_path.read_text(encoding="utf-8"))
        model_meta = registry.get("uhl_model") or {}
        registry_ok = (
            registry.get("dataset") == DATASET_SOURCE
            and model_meta.get("bundled_relative_path")
            == "artifacts/model/uhl_synthetic_acuity_selected.joblib"
            and model_meta.get("sha256") == settings.expected_model_sha256
            and model_meta.get("dataset_sha256") == settings.expected_dataset_sha256
        )
        registry_detail = {
            "dataset": registry.get("dataset"),
            "model": model_meta.get("bundled_relative_path"),
        }
    except Exception as exc:
        registry_detail = {"error": str(exc)}

    dataset_hash_ok = False
    model_hash_ok = False
    model_contract_ok = False
    model_contract_error = ""
    if dataset_path.is_file():
        dataset_hash_ok = _sha256(dataset_path) == settings.expected_dataset_sha256
    if model_path.is_file():
        model_hash_ok = _sha256(model_path) == settings.expected_model_sha256
        try:
            validate_uhl_serving_bundle(joblib.load(model_path), require_deployable=True)
            model_contract_ok = True
        except Exception as exc:
            model_contract_error = str(exc)

    dockerfile = PROJECT_ROOT / "Dockerfile"
    docker_text = dockerfile.read_text(encoding="utf-8") if dockerfile.exists() else ""
    backend_script = PROJECT_ROOT / "startup-backend.sh"
    backend_text = backend_script.read_text(encoding="utf-8") if backend_script.exists() else ""
    main_path = PROJECT_ROOT / "app" / "main.py"
    main_text = main_path.read_text(encoding="utf-8") if main_path.exists() else ""

    security_status = build_security_status()
    patient_mode = bool(security_status["patient_data_mode"])
    unsafe = unsafe_combinations()

    required_checks = {
        "model_registry_exists": settings.model_registry_path.is_file(),
        "registry_matches_uhl_release": registry_ok,
        "uhl_dataset_present": dataset_path.is_file(),
        "uhl_dataset_sha256_matches": dataset_hash_ok,
        "uhl_model_present": model_path.is_file(),
        "uhl_model_sha256_matches": model_hash_ok,
        "uhl_model_serving_contract_valid": model_contract_ok,
        "uhl_reports_present": (
            report_dir.is_dir()
            and (report_dir / "uhl_synthetic_training_provenance.json").is_file()
            and (report_dir / "uhl_synthetic_feature_schema.json").is_file()
        ),
        "react_ui_built": (PROJECT_ROOT / "frontend-react" / "dist" / "index.html").is_file(),
        "backend_serves_react_ui": (
            "frontend-react" in main_text
            and "SERVE_REACT_UI" in main_text
            and "uvicorn app.main:app" in backend_text
        ),
        "dockerfile_ships_uhl_assets": (
            "data/uhl_dataset_final.csv.gz" in docker_text
            and "COPY artifacts/" in docker_text
        ),
        "dockerfile_installs_autogen_runtime_deps": (
            "requirements-autogen.txt" in docker_text
            and "pip install --no-cache-dir -r requirements-autogen.txt" in docker_text
        ),
        "patient_data_config_safe_or_synthetic": (not patient_mode) or not unsafe,
    }

    release_profile = os.environ.get("ALTER_RELEASE_PROFILE", "demo").strip().lower()
    autogen_ok = _autogen_importable()
    azure_openai_ok = load_azure_config() is not None
    if release_profile == "agentic":
        required_checks["agentic_autogen_runtime_importable"] = autogen_ok
        required_checks["agentic_azure_openai_configured"] = azure_openai_ok

    optional_checks = {
        "autogen_importable": autogen_ok,
        "azure_openai_configured": azure_openai_ok,
    }
    result = {
        "status": "PASS" if all(required_checks.values()) else "FAIL",
        "required_checks": required_checks,
        "optional_checks": optional_checks,
        "warnings": [name for name, ok in optional_checks.items() if not ok],
        "release_profile": release_profile,
        "deployment_target": "single_service_fastapi_serving_react_ui",
        "default_dataset": DATASET_SOURCE,
        "datasets_available": [DATASET_SOURCE],
        "dataset_path": str(dataset_path),
        "model_path": str(model_path),
        "registry": registry_detail,
        "model_contract_error": model_contract_error or None,
        "clinical_use": "not_for_clinical_use",
        "official_manchester_triage": "not_implemented",
        "clinician_review_required": True,
        "patient_data_mode": patient_mode,
        "patient_data_unsafe_combinations": unsafe,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
