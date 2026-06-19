"""Run the complete safe Kaggle KTAS pipeline."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from scripts.build_sample_cases import main as build_cases_main
from scripts.build_ktas_labels import build_ktas_labels
from ml_training.train_all_models import train_all_models
from ml_training.evaluate_models import generate_evaluation_report


def _run_script(script: str) -> None:
    subprocess.run([sys.executable, script], cwd=PROJECT_ROOT, check=True)


def main() -> None:
    if not settings.raw_ktas_csv.exists():
        raise FileNotFoundError(
            f"Missing Kaggle KTAS CSV: {settings.raw_ktas_csv}\n"
            "Place data.csv at data/raw/kaggle_ktas/data.csv."
        )

    # Build processed JSONL cases and schema report
    subprocess.run([
        sys.executable, "scripts/build_sample_cases.py",
        "--dataset", "kaggle_ktas",
        "--csv", str(settings.raw_ktas_csv),
    ], cwd=PROJECT_ROOT, check=True)

    labels_path = settings.processed_dir / "ktas_labels.jsonl"
    build_ktas_labels(settings.processed_dir / "triage_cases_sample.jsonl", labels_path)
    # Backward-compatible copy for commands expecting outcome_labels.jsonl
    outcome_path = settings.processed_dir / "outcome_labels.jsonl"
    outcome_path.write_text(labels_path.read_text(encoding="utf-8"), encoding="utf-8")

    train_all_models(labels_path, settings.models_dir, include_optional_boosters=False)

    report = generate_evaluation_report()
    with (settings.processed_dir / "model_evaluation_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)

    _run_script("scripts/audit_processed_sample.py")
    _run_script("scripts/inspect_missing_triage_inputs.py")

    print("\nKTAS pipeline complete.")
    print(f"Processed data: {settings.processed_dir}")
    print(f"Models: {settings.models_dir}")
    print("Clinical safety: NOT FOR CLINICAL USE. KTAS is not Manchester. Human review required.")


if __name__ == "__main__":
    main()
