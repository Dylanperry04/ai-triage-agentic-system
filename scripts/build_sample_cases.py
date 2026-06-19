"""Build processed case JSONL files from the active dataset.

Default: Kaggle KTAS CSV at data/raw/kaggle_ktas/data.csv.
MIMIC support is retained for the later approved-data phase.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.data_pipeline.export import export_cases
from app.data_pipeline.ktas_adapter import load_ktas_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Build processed triage cases.")
    parser.add_argument("--n", type=int, default=None, help="Optional number of rows/cases to sample")
    parser.add_argument(
        "--dataset",
        choices=["kaggle_ktas", "ktas", "demo", "full"],
        default="kaggle_ktas",
        help="Dataset adapter to use. Current recommended value: kaggle_ktas.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=settings.raw_ktas_csv,
        help="Path to Kaggle KTAS data.csv",
    )
    args = parser.parse_args()

    dataset = "kaggle_ktas" if args.dataset == "ktas" else args.dataset

    if dataset == "kaggle_ktas":
        cases, report = load_ktas_cases(args.csv, n=args.n)
        export_cases(cases, settings.processed_dir)
        settings.processed_dir.mkdir(parents=True, exist_ok=True)
        with (settings.processed_dir / "schema_report.json").open("w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Built {len(cases)} Kaggle KTAS cases.")
        print(f"Wrote outputs to {settings.processed_dir}")
        return

    if dataset == "demo":
        from app.data_pipeline.mimic_adapter import load_mimic_demo_cases

        cases, report = load_mimic_demo_cases(settings.raw_demo_dir, n=args.n)
        export_cases(cases, settings.processed_dir)
        with (settings.processed_dir / "schema_report.json").open("w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"Built {len(cases)} MIMIC-IV-ED-Demo-v2.2 cases.")
        print(f"Wrote outputs to {settings.processed_dir}")
        return

    # Legacy path for the full (not-yet-credentialed) MIMIC-IV-ED dataset
    # only. NOT used for --dataset demo (see above) -- that real, verified
    # adapter is app/data_pipeline/mimic_adapter.py. This legacy path
    # predates that adapter and was found, during a later review pass, to
    # mislabel every case's source_dataset as "Kaggle-KTAS" regardless of
    # which dataset it actually came from. That mislabeling silently
    # defeats the follow-up comparison agent's cross-dataset consistency
    # warning (app/agents/followup_comparison_agent.py), since that check
    # relies entirely on source_dataset to detect a KTAS/MIMIC mismatch.
    # This path is kept only for the full dataset, once access is
    # approved, and must be re-verified (most importantly: its
    # source_dataset tagging) before it is trusted for that case either.
    from app.data_pipeline.loaders import load_all_tables
    from app.data_pipeline.validation import validate_loaded_tables
    from app.data_pipeline.mapping import build_cases

    raw_dir = settings.raw_ed_dir
    tables = load_all_tables(raw_dir)
    report = validate_loaded_tables(tables)
    cases = build_cases(tables, n=args.n)
    export_cases(cases, settings.processed_dir)
    with (settings.processed_dir / "schema_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Built {len(cases)} MIMIC cases from {dataset} (legacy path -- full dataset only).")


if __name__ == "__main__":
    main()
