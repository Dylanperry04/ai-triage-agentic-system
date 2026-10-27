"""Verify every demo input against unchanged records in the pinned source CSV.

This is an offline provenance check, not model evaluation. It does not rewrite
the source, generate readings, or run during application startup.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.config import settings
from app.constants import DATASET_SHA256, MODEL_SHA256, EXPECTED_SOURCE_ROWS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_values(original: dict, chosen: dict) -> None:
    pairs = (("DATE", "arrival_time"), ("PresentingComplaint_base", "presenting_complaint"))
    for source, target in pairs:
        if original[source] != chosen[target]:
            raise ValueError("Source value differs: " + source)
    for source, target in (("Age", "age"), *((key, key) for key in (
            "temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain"))):
        if float(original[source]) != chosen[target]:
            raise ValueError("Source value differs: " + source)
    if float(original["master_estimated_acuity"]) != chosen["selection_evidence"]["source_estimated_acuity"]:
        raise ValueError("Source estimated label differs")


def verify_demo_source(source: Path, cohort: Path) -> dict:
    source_hash = sha256(source)
    if source_hash != DATASET_SHA256:
        raise ValueError("Source CSV does not match the pinned Git/training data")
    if sha256(settings.uhl_model_path) != MODEL_SHA256:
        raise ValueError("Model artifact has changed")
    manifest = json.loads(cohort.read_text(encoding="utf-8"))
    selected = {row["row_number"]: row for row in manifest["cases"]}
    if len(selected) != 100 or len(manifest["cases"]) != 100:
        raise ValueError("Exactly 100 distinct source records are required")
    verified, labels, offset = [], Counter(), 0
    for chunk in pd.read_csv(source, chunksize=20000):
        for number in sorted(selected.keys() & set(range(offset + 1, offset + len(chunk) + 1))):
            original = chunk.iloc[number - offset - 1].to_dict()
            verify_values(original, selected[number])
            labels[int(original["master_estimated_acuity"])] += 1
            verified.append({"csv_data_record": number, "all_input_fields_equal": True})
        offset += len(chunk)
    if offset != EXPECTED_SOURCE_ROWS or len(verified) != 100:
        raise ValueError("Source scan or selected-record coverage is incomplete")
    return {"source_sha256": source_hash, "cohort_sha256": sha256(cohort),
            "model_sha256": MODEL_SHA256, "source_rows_scanned": offset,
            "selected_records_verified": len(verified), "source_values_generated_or_changed": False,
            "source_estimated_label_counts": dict(labels), "records": verified,
            "scope": "Exact input and estimated-label provenance; not an accuracy evaluation"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=settings.uhl_data_path)
    parser.add_argument("--cohort", type=Path, default=settings.serving_data_path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_demo_source(args.source, args.cohort)
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "records"}, indent=2))
