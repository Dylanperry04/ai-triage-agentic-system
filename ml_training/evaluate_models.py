"""Generate KTAS research model evaluation report from registry.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


def _safe_float(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def generate_evaluation_report() -> dict:
    registry_path = settings.model_registry_path
    if not registry_path.exists():
        return {"status": "NO_MODELS_TRAINED", "message": "Run python scripts/run_ktas_pipeline.py first."}

    with registry_path.open("r", encoding="utf-8") as f:
        registry = json.load(f)

    ktas = registry.get("best_ktas_model") or registry.get("best_model", {})
    emergency = registry.get("best_emergency_model", {})
    ktas_metrics = ktas.get("metrics", {})
    emergency_metrics = emergency.get("metrics", {})

    return {
        "evaluation_report_version": "ktas-1.0",
        "generated_at": registry.get("created_at_utc"),
        "dataset": registry.get("dataset", "Kaggle-KTAS"),
        "training_data": registry.get("training_data"),
        "n_samples": registry.get("n_samples"),
        "research_note": (
            "Models are trained on public Kaggle KTAS data using KTAS_expert as the research target. "
            "KTAS is not Manchester triage. This is not a clinical validation result."
        ),
        "best_ktas_model": {
            "name": ktas.get("name"),
            "task": ktas.get("task"),
            "accuracy": _safe_float(ktas_metrics.get("accuracy")),
            "balanced_accuracy": _safe_float(ktas_metrics.get("balanced_accuracy")),
            "macro_f1": _safe_float(ktas_metrics.get("macro_f1")),
            "macro_auroc": _safe_float(ktas_metrics.get("macro_auroc")),
            "under_triage_rate": _safe_float(ktas_metrics.get("under_triage_rate")),
            "over_triage_rate": _safe_float(ktas_metrics.get("over_triage_rate")),
            "confusion_matrix": ktas_metrics.get("confusion_matrix"),
        },
        "best_emergency_model": {
            "name": emergency.get("name"),
            "task": emergency.get("task"),
            "accuracy": _safe_float(emergency_metrics.get("accuracy")),
            "balanced_accuracy": _safe_float(emergency_metrics.get("balanced_accuracy")),
            "macro_f1": _safe_float(emergency_metrics.get("macro_f1")),
            "macro_auroc": _safe_float(emergency_metrics.get("macro_auroc")),
            "false_negative_emergency_rate": _safe_float(emergency_metrics.get("false_negative_emergency_rate")),
            "confusion_matrix": emergency_metrics.get("confusion_matrix"),
        },
        "all_ktas_models": registry.get("all_ktas_models", []),
        "all_emergency_models": registry.get("all_emergency_models", []),
        "governance_flags": [
            "Not for clinical use.",
            "KTAS is not Manchester Triage Scale.",
            "No KTAS-to-Manchester mapping is implemented.",
            "Models trained on public Kaggle data only; UHL validation not performed.",
            "Do not use model outputs for autonomous patient triage.",
            "Human clinical review is required for every case.",
        ],
    }


if __name__ == "__main__":
    report = generate_evaluation_report()
    print(json.dumps(report, indent=2, default=str))
    output_path = settings.processed_dir / "model_evaluation_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nEvaluation report saved to: {output_path}")
