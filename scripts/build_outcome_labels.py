"""Compatibility wrapper.

The active phase uses Kaggle KTAS, not MIMIC-IV-ED outcomes. This script writes
`data/processed/outcome_labels.jsonl` with KTAS research labels so older docs or
commands still work safely.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from scripts.build_ktas_labels import build_ktas_labels


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build current active-dataset labels.")
    parser.add_argument("--cases", type=Path, default=settings.processed_dir / "triage_cases_sample.jsonl")
    parser.add_argument("--out", type=Path, default=settings.processed_dir / "outcome_labels.jsonl")
    parser.add_argument("--n", type=int, default=None)
    args = parser.parse_args()
    build_ktas_labels(args.cases, args.out, args.n)
