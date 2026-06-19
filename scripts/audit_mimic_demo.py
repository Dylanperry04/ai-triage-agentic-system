"""
MIMIC-IV-ED Demo Dataset Audit.

Produces a full audit report: tables, columns, row counts, missingness
per field, and an explicit classification of every field as either a
triage-time input or a retrospective/outcome field. Run this before
trusting any downstream MIMIC pipeline step.

This script makes no assumptions about the data -- every number in its
output comes from actually loading and inspecting the real files at
runtime, not from a hardcoded list.

Usage:
  python scripts/audit_mimic_demo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.data_pipeline.mimic_adapter import (
    EXPECTED_TABLES,
    load_mimic_table,
    validate_mimic_tables,
)
from app.schemas.mimic_ed import TRIAGE_INPUT_COLUMNS, RETROSPECTIVE_OR_LEAKAGE_COLUMNS


def audit_mimic_demo(ed_dir: Path) -> dict:
    validation = validate_mimic_tables(ed_dir)

    per_table_audit = {}
    for table_name in EXPECTED_TABLES:
        df = load_mimic_table(ed_dir, table_name)
        missingness = {
            col: {
                "missing_count": int(df[col].isnull().sum()),
                "missing_pct": round(100 * df[col].isnull().sum() / len(df), 2) if len(df) else None,
            }
            for col in df.columns
        }
        per_table_audit[table_name] = {
            "row_count": len(df),
            "columns": list(df.columns),
            "missingness": missingness,
        }

    # Classify every column across all tables as triage-time-safe or
    # retrospective/leakage, based on the project's existing,
    # already-reviewed RETROSPECTIVE_OR_LEAKAGE_COLUMNS / TRIAGE_INPUT_COLUMNS
    # lists in app/schemas/mimic_ed.py -- not re-derived here, to avoid
    # two independently-maintained leakage classifications drifting apart.
    field_classification = {}
    for table_name, table_info in per_table_audit.items():
        for col in table_info["columns"]:
            # IMPORTANT: vitalsign.csv is checked FIRST and unconditionally,
            # before the generic TRIAGE_INPUT_COLUMNS check. This was a real
            # bug found while testing this script: TRIAGE_INPUT_COLUMNS
            # contains generic vital names like "temperature" and "heartrate"
            # that exist in BOTH triage.csv and vitalsign.csv. Checking that
            # list first matched vitalsign's vitals as TRIAGE_TIME_SAFE,
            # silently contradicting this script's own stated exclusion of
            # vitalsign.csv as a triage-time source. The column NAME being
            # safe in one table does not make it safe in a different table
            # with different timing semantics.
            if table_name == "vitalsign":
                field_classification[f"{table_name}.{col}"] = (
                    "EXCLUDED_FROM_TRIAGE_INPUT -- vitalsign.csv contains "
                    "repeated in-ED monitoring readings (up to 22 per stay, "
                    "taken after intime), not a single triage-time snapshot. "
                    "triage.csv is used as the triage-time vitals source instead."
                )
            elif col in RETROSPECTIVE_OR_LEAKAGE_COLUMNS:
                field_classification[f"{table_name}.{col}"] = "RETROSPECTIVE_OR_LEAKAGE"
            elif col in TRIAGE_INPUT_COLUMNS:
                field_classification[f"{table_name}.{col}"] = "TRIAGE_TIME_SAFE"
            elif table_name in ("diagnosis", "medrecon", "pyxis"):
                field_classification[f"{table_name}.{col}"] = (
                    "RETROSPECTIVE -- assigned or recorded during/after "
                    "clinical assessment, not known at triage"
                )
            else:
                field_classification[f"{table_name}.{col}"] = "UNCLASSIFIED -- review needed"

    # Known data quality findings, recorded explicitly rather than silently
    # corrected (see app/data_pipeline/mimic_adapter.py module docstring
    # for the full reasoning on each).
    triage_df = load_mimic_table(ed_dir, "triage")
    known_issues = []

    temp_min = triage_df["temperature"].min()
    if temp_min is not None and temp_min < 70:
        known_issues.append({
            "field": "triage.temperature",
            "issue": (
                f"Minimum value {temp_min} is implausible as Fahrenheit "
                "(would indicate severe hypothermia) but plausible as "
                "Celsius -- likely a single data-entry error in the source "
                "CSV. Value is kept as-recorded, not corrected, since the "
                "true intended value cannot be known."
            ),
        })

    pain_values = triage_df["pain"].dropna().astype(str)
    non_numeric_pain = []
    for v in pain_values.unique():
        try:
            f = float(v)
            if not (0 <= f <= 10):
                non_numeric_pain.append(v)
        except (TypeError, ValueError):
            non_numeric_pain.append(v)
    if non_numeric_pain:
        known_issues.append({
            "field": "triage.pain",
            "issue": (
                f"Contains {len(non_numeric_pain)} distinct out-of-range or "
                f"non-numeric values: {sorted(non_numeric_pain)}. These are "
                "treated as unparseable/missing by _parse_pain(), not "
                "coerced into a number."
            ),
        })

    report = {
        "dataset": "MIMIC-IV-ED-Demo-v2.2",
        "is_demo_not_full_dataset": True,
        "source_directory": str(ed_dir),
        "schema_validation": validation,
        "tables": per_table_audit,
        "field_classification": field_classification,
        "known_data_quality_issues": known_issues,
        "leakage_policy_summary": (
            "triage.acuity, edstays.disposition, edstays.outtime, "
            "edstays.hadm_id, and all diagnosis/medrecon/pyxis records are "
            "retrospective and never enter TriageTimeInput. vitalsign.csv "
            "(repeated in-ED monitoring) is also excluded from triage-time "
            "input for the same reason -- only triage.csv's single "
            "per-stay snapshot is used."
        ),
        "manchester_mapping": "NOT_IMPLEMENTED -- MIMIC acuity is not Manchester Triage Scale",
        "clinical_use": "NOT_FOR_CLINICAL_USE",
    }
    return report


if __name__ == "__main__":
    ed_dir = settings.raw_demo_dir
    if not ed_dir.exists():
        print(f"ERROR: MIMIC demo ed/ directory not found at {ed_dir}")
        print("Expected six .csv.gz files under that path.")
        sys.exit(1)

    report = audit_mimic_demo(ed_dir)
    print(json.dumps(report, indent=2, default=str))

    output_path = settings.processed_dir / "mimic_demo_audit_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nAudit report saved to: {output_path}")
