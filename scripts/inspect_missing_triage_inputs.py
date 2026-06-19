from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from app.config import settings
from app.storage.jsonl_repository import read_jsonl

TRIAGE_REQUIRED_FOR_REVIEW = [
    "chiefcomplaint", "age", "arrival_transport", "mental_code",
    "temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp",
]


def main():
    path = settings.processed_dir / "triage_input_only_sample.jsonl"
    if not path.exists():
        raise FileNotFoundError("Missing triage input file. Run: python scripts/run_ktas_pipeline.py")
    records = read_jsonl(path)
    missing_cases = []
    for record in records:
        missing_fields = [field for field in TRIAGE_REQUIRED_FOR_REVIEW if record.get(field) is None or record.get(field) == ""]
        if record.get("pain_present") == 1 and record.get("nrs_pain") is None and not record.get("pain"):
            missing_fields.append("nrs_pain")
        if missing_fields:
            missing_cases.append({
                "stay_id": record.get("stay_id"),
                "subject_id": record.get("subject_id"),
                "chiefcomplaint": record.get("chiefcomplaint"),
                "missing_fields": missing_fields,
            })
    report = {
        "dataset": "Kaggle-KTAS",
        "sample_size": len(records),
        "cases_with_missing_triage_inputs": len(missing_cases),
        "missing_case_percent": round((len(missing_cases) / len(records)) * 100, 2) if records else 0,
        "missing_cases": missing_cases,
        "note": "Missing data is made visible for human review; no values are fabricated.",
    }
    output_path = settings.processed_dir / "missing_triage_inputs_report.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Missing-input report written to: {output_path}")
    print(f"Cases with missing inputs: {report.get('cases_with_missing_triage_inputs')}")


if __name__ == "__main__":
    main()
