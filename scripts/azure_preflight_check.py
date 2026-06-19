"""Local/Azure preflight checks for the KTAS research app."""
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

    mimic_demo_dir = settings.raw_demo_dir
    mimic_demo_files = [
        "triage.csv.gz", "edstays.csv.gz", "vitalsign.csv.gz",
        "diagnosis.csv.gz", "medrecon.csv.gz", "pyxis.csv.gz",
    ]
    mimic_demo_present = mimic_demo_dir.exists() and all(
        (mimic_demo_dir / f).exists() for f in mimic_demo_files
    )

    # Functional checks: the deployment-critical behaviours, not just file existence.
    mimic_acuity_model_loads = False
    mimic_case_produces_final_acuity = False
    ktas_case_has_no_mts_category = False
    try:
        import joblib
        reg = json.load(open(settings.model_registry_path))
        info = reg.get("best_mimic_acuity_model", {})
        mp = info.get("path", "")
        from app.agents.ml_prediction_agent import _resolve_model_path
        model_file = _resolve_model_path(mp)
        if model_file.exists():
            joblib.load(model_file)
            mimic_acuity_model_loads = True
    except Exception:
        mimic_acuity_model_loads = False

    try:
        from app.data_pipeline.mimic_adapter import load_mimic_demo_cases
        from app.data_pipeline.ktas_adapter import load_ktas_cases
        from app.agents.orchestrator import run_workflow
        mimic, _ = load_mimic_demo_cases(settings.raw_demo_dir, n=1)
        wf_m = run_workflow(mimic[0])
        mimic_case_produces_final_acuity = (
            wf_m.final_acuity_assessment.applicable
            and wf_m.final_acuity_assessment.category is not None
        )
        ktas, _ = load_ktas_cases(settings.raw_ktas_csv, n=1)
        wf_k = run_workflow(ktas[0])
        ktas_case_has_no_mts_category = (
            wf_k.decision.category is None
            and not wf_k.final_acuity_assessment.applicable
        )
    except Exception:
        pass

    dockerfile = PROJECT_ROOT / "Dockerfile"
    dockerfile_runs_streamlit = (
        dockerfile.exists() and "streamlit run" in dockerfile.read_text(encoding="utf-8")
    )

    checks = {
        # KTAS pipeline artifacts (KTAS is the secondary dataset)
        "raw_ktas_csv_exists": settings.raw_ktas_csv.exists(),
        "ktas_labels_exists": (settings.processed_dir / "ktas_labels.jsonl").exists(),
        "model_registry_exists": settings.model_registry_path.exists(),
        "schema_report_exists": (settings.processed_dir / "schema_report.json").exists(),
        "model_evaluation_report_exists": (settings.processed_dir / "model_evaluation_report.json").exists(),
        # MIMIC demo (the default dataset) must be present for the demo to run
        "mimic_demo_files_exist": mimic_demo_present,
        "mimic_acuity_model_loads": mimic_acuity_model_loads,
        "mimic_case_produces_final_acuity_assessment": mimic_case_produces_final_acuity,
        "ktas_case_has_no_mts_category": ktas_case_has_no_mts_category,
        # Streamlit deployment (Option A) -- the deployed app is the UI
        "dockerfile_runs_streamlit": dockerfile_runs_streamlit,
        # AutoGen importable (chat/explanation layer)
        "autogen_importable": _autogen_importable(),
    }
    result = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "deployment_target": "streamlit_azure_web_app (Option A)",
        "default_dataset": "MIMIC-IV-ED-Demo-v2.2",
        "datasets_available": ["MIMIC-IV-ED-Demo-v2.2", "Kaggle-KTAS"],
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
