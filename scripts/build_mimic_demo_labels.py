"""
Build MIMIC-IV-ED Demo training labels + triage-time features for the acuity model.

LEAKAGE DISCIPLINE (the central safety requirement):
  - The LABEL is `acuity` (the MIMIC/ESI triage level), taken from triage.csv.
  - FEATURES are ONLY triage-time fields, i.e. fields a nurse has AT triage:
        from triage.csv:  chiefcomplaint, temperature, heartrate, resprate,
                          o2sat, sbp, dbp, pain
        from edstays.csv: gender, race, arrival_transport
  - EXCLUDED as target/temporal leakage (proven by timing in the data):
        acuity-as-feature, disposition, outtime, hadm_id (edstays);
        the entire vitalsign.csv in-stay time-series (median ~203 min AFTER
        triage); diagnosis.csv (post-visit ICD); medrecon.csv / pyxis.csv
        (medication recon/dispensing during/after care).
  Every excluded source is also in RETROSPECTIVE_OR_LEAKAGE_COLUMNS; this script
  asserts no leakage column reaches the feature set before writing anything.

Rows with a missing acuity label are written to the features file but excluded
from the labelled training set (cannot supervise without a label).

Outputs (data/processed/):
  - mimic_demo_acuity_features.jsonl : per-stay triage-time features (+ label if present)
  - mimic_demo_acuity_labels.jsonl   : per-stay {stay_id, acuity} for labelled rows only
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from app.config import settings
from app.schemas.mimic_ed import RETROSPECTIVE_OR_LEAKAGE_COLUMNS

# Triage-time feature columns (the ONLY inputs the model may see).
TRIAGE_FEATURES_FROM_TRIAGE = [
    "chiefcomplaint", "temperature", "heartrate", "resprate",
    "o2sat", "sbp", "dbp", "pain",
]
TRIAGE_FEATURES_FROM_EDSTAYS = ["gender", "race", "arrival_transport"]
ALL_FEATURE_COLUMNS = TRIAGE_FEATURES_FROM_TRIAGE + TRIAGE_FEATURES_FROM_EDSTAYS

LABEL_COLUMN = "acuity"


def _assert_no_leakage(feature_columns: list[str]) -> None:
    """Hard gate: refuse to proceed if any feature is a known leakage column."""
    leaked = sorted(set(feature_columns) & set(RETROSPECTIVE_OR_LEAKAGE_COLUMNS))
    if leaked:
        raise ValueError(
            f"LEAKAGE GUARD TRIPPED: feature set contains leakage columns {leaked}. "
            "Refusing to build training data. Triage-time features only."
        )
    if LABEL_COLUMN in feature_columns:
        raise ValueError(
            "LEAKAGE GUARD TRIPPED: the label 'acuity' is in the feature set."
        )


def build() -> dict:
    ed_dir = settings.raw_demo_dir
    triage = pd.read_csv(ed_dir / "triage.csv.gz")
    edstays = pd.read_csv(ed_dir / "edstays.csv.gz")

    # Hard gate BEFORE we touch the data.
    _assert_no_leakage(ALL_FEATURE_COLUMNS)

    # Merge ONLY the safe edstays columns (never disposition/outtime/hadm_id).
    edstays_safe = edstays[["stay_id"] + TRIAGE_FEATURES_FROM_EDSTAYS].copy()
    df = triage.merge(edstays_safe, on="stay_id", how="left")

    out_dir = settings.processed_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    features_path = out_dir / "mimic_demo_acuity_features.jsonl"
    labels_path = out_dir / "mimic_demo_acuity_labels.jsonl"

    n_total = 0
    n_labelled = 0
    with open(features_path, "w", encoding="utf-8") as ff, \
         open(labels_path, "w", encoding="utf-8") as lf:
        for _, row in df.iterrows():
            n_total += 1
            features = {c: (None if pd.isna(row[c]) else row[c]) for c in ALL_FEATURE_COLUMNS}
            acuity = None if pd.isna(row[LABEL_COLUMN]) else int(row[LABEL_COLUMN])
            rec = {
                "stay_id": int(row["stay_id"]),
                "source_dataset": "MIMIC-IV-ED-Demo-v2.2",
                "features": features,
                "acuity_label": acuity,
            }
            ff.write(json.dumps(rec) + "\n")
            if acuity is not None:
                lf.write(json.dumps({"stay_id": int(row["stay_id"]), "acuity": acuity}) + "\n")
                n_labelled += 1

    summary = {
        "status": "OK",
        "rows_total": n_total,
        "rows_labelled": n_labelled,
        "rows_unlabelled_excluded_from_training": n_total - n_labelled,
        "feature_columns": ALL_FEATURE_COLUMNS,
        "label_column": LABEL_COLUMN,
        "leakage_columns_excluded": sorted(RETROSPECTIVE_OR_LEAKAGE_COLUMNS),
        "features_path": str(features_path),
        "labels_path": str(labels_path),
    }
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    build()
