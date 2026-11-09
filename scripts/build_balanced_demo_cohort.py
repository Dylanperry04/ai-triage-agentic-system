"""Freeze a source-only demonstration cohort without consulting a trained model."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.constants import (BALANCED_DEMO_COHORT_ID, BALANCED_DEMO_COHORT_NOTICE,
                           BALANCED_DEMO_CORRECTED_CSV_SHA256, DATASET_SHA256, DATASET_SOURCE,
                           DEMO_COHORT_ROWS, DEMO_COHORT_SHA256)
from app.rules.mts_catalog import propose_flowchart
from ml_training.uhl_synthetic.dataset import (TARGET_COLUMN, VITAL_SOURCE_COLUMNS,
                                               load_uhl_dataset, sha256_file)

SELECTION_SEED = "uhl-balanced-demo-train-v1"
SPLIT_SHA256 = "498990bd6b0246b4842ccbc4c014d4b021de94c56ddd077c65b457d8c5a079d5"
CHART_GROUP = {"chest_pain": "chest_pain", "abdominal_pain_adults": "abdominal_pain",
               "shortness_of_breath_adults": "respiratory_symptoms",
               "limb_problems": "limb_or_wound", "wounds": "limb_or_wound"}
ABSTENTION_QUOTA = {"ambiguous_injury": 10, "unwell_adult": 5, "other_unsupported": 5}


def selection_stratum(complaint: str, year: int, chart: str | None, label: int):
    if chart in CHART_GROUP:
        return CHART_GROUP[chart], label
    if complaint == "INJURY":
        return "ambiguous_injury", 0
    if complaint == "UNWELL ADULT":
        return "unwell_adult", 0
    if (chart is None and complaint != "DOA"
            and not any(word in complaint for word in ("CHILD", "BABY", "NEWBORN"))
            and not (complaint == "QUERY COVID-19" and year < 2020)):
        return "other_unsupported", 0
    return None


def build_cohort(path: Path, legacy_path: Path) -> dict:
    if sha256_file(path) != DATASET_SHA256:
        raise ValueError("The immutable packaged source CSV does not match its expected SHA-256")
    dataset = load_uhl_dataset(path, seed=42)
    if dataset.metadata["split_assignment_sha256"] != SPLIT_SHA256:
        raise ValueError("The reproduced training split does not match the frozen split SHA-256")
    train_ids = {case_id for case_id, split in zip(dataset.row_ids, dataset.split) if split == "TRAIN"}
    if sha256_file(legacy_path) != DEMO_COHORT_SHA256:
        raise ValueError("The immutable legacy demo does not match its expected SHA-256")
    legacy_manifest = json.loads(legacy_path.read_text(encoding="utf-8"))
    legacy = {row["row_number"] for row in legacy_manifest["cases"]}
    expected = {(group, label): 4 for group in set(CHART_GROUP.values()) for label in range(1, 6)}
    expected.update({(group, 0): quota for group, quota in ABSTENTION_QUOTA.items()})
    candidates = {key: [] for key in expected}
    chart_cache = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        for number, row in enumerate(csv.DictReader(source), start=1):
            age = int(row["Age"])
            if age < 16 or number in legacy or row["AttendanceID"] not in train_ids:
                continue
            complaint = row["PresentingComplaint_base"]
            if complaint not in chart_cache:
                chart_cache[complaint] = propose_flowchart(complaint)["flowchart_id"]
            chart = chart_cache[complaint]
            label = int(row[TARGET_COLUMN])
            stratum = selection_stratum(complaint, int(row["year"]), chart, label)
            if stratum is None:
                continue
            bucket = candidates[stratum]
            rank = hashlib.sha256(f"{SELECTION_SEED}:{number}".encode()).digest()
            quota = expected[stratum]
            if len(bucket) == quota and rank >= bucket[-1][0]:
                continue
            record = {"row_number": number, "arrival_time": row["DATE"], "age": age,
                      "presenting_complaint": complaint, "selection_group": stratum[0],
                      "source_estimated_acuity": label, "proposed_flowchart": chart,
                      **{key: float(row[key]) for key in VITAL_SOURCE_COLUMNS}}
            bucket.append((rank, number, record))
            bucket.sort()
            del bucket[quota:]
    if any(len(candidates[stratum]) != quota for stratum, quota in expected.items()):
        raise ValueError("The TRAIN partition cannot fill the declared demonstration strata")
    records = sorted((record for bucket in candidates.values() for _, _, record in bucket),
                     key=lambda row: (row["arrival_time"], row["row_number"]))
    if len(records) != DEMO_COHORT_ROWS:
        raise ValueError("Incorrect demonstration size")
    return {
        "schema_version": 2, "cohort_id": BALANCED_DEMO_COHORT_ID,
        "dataset_source": DATASET_SOURCE, "training_dataset_sha256": DATASET_SHA256,
        "verified_corrected_attachment_sha256": BALANCED_DEMO_CORRECTED_CSV_SHA256,
        "selection": {
            "purpose": "STRATIFIED_SYNTHETIC_DEMONSTRATION",
            "notice": BALANCED_DEMO_COHORT_NOTICE,
            "source_inputs_unchanged": True,
            "synthetic_vitals": True,
            "clinical_reference_present": False,
            "selection_seed": SELECTION_SEED,
            "split_assignment_sha256": SPLIT_SHA256,
            "split": "TRAIN",
            "ranking": "Ascending SHA-256 of seed and one-based source row within strata",
            "legacy_demo_rows_excluded": True,
            "trauma_scope": "Source limb and wound complaints only; injury mechanism is not inferred",
            "age_scope": "Adult rows, age 16 or over; no age-specific performance inference",
            "row_counts_by_selection_group": dict(sorted(Counter(row["selection_group"] for row in records).items())),
            "row_counts_by_estimated_label": dict(sorted(Counter(str(row["source_estimated_acuity"]) for row in records).items())),
        },
        "cases": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data/uhl_dataset_final.csv.gz")
    parser.add_argument("--legacy", type=Path, default=ROOT / "data/uhl_demo_100.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/uhl_demo_balanced_100.json")
    args = parser.parse_args()
    cohort = build_cohort(args.source, args.legacy)
    payload = (json.dumps(cohort, indent=2, allow_nan=False) + "\n").encode("utf-8")
    args.output.write_bytes(payload)
    print(json.dumps({"cases": len(cohort["cases"]), "sha256": hashlib.sha256(payload).hexdigest()}))


if __name__ == "__main__":
    main()
