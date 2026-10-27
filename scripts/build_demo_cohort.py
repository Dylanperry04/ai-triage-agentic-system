"""Build a disclosed 100-case demonstration from a pinned offline cohort scan.

The scan curates ML-first results with exactly 60 matched vital checks.
This is never a held-out accuracy evaluation. Outcomes are excluded; estimated
labels are used only for demo selection and never as model inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.constants import DATASET_SHA256, DATASET_SOURCE, MODEL_SHA256, DEMO_COHORT_CATEGORY_COUNTS, DEMO_COHORT_MATCHED_ROWS

PROFILE_ID = "ml_first_matched_60_v3"
MIN_CLASS_PROBABILITY = 0.5
VITAL_FIELDS = ("temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp")


def vital_distances(row: dict, profile: dict) -> tuple[float, float]:
    """Statistical distance from source medians; these are not clinical scores."""
    distances = [abs(float(row[field]) - profile[field]["median"]) / profile[field]["iqr"]
                 for field in VITAL_FIELDS]
    return max(distances), sum(distances) / len(distances)


def candidate_key(item: dict) -> tuple:
    return (item["peak_vital_distance"], item["mean_vital_distance"],
            -item["ml_probability"], item["row"]["source_row_number"])


def build_cohort(scan: dict) -> dict:
    if scan.get("source_rows_scanned") != 777176:
        raise ValueError("A complete pinned source-cohort scan is required")
    if (scan.get("training_dataset_sha256") != DATASET_SHA256
            or scan.get("model_sha256") != MODEL_SHA256
            or scan.get("rule_version") != "acuity_override_v1"):
        raise ValueError("Candidate scan provenance is incompatible")
    if scan.get("selection_profile") != PROFILE_ID:
        raise ValueError("A complete 60-matched-check candidate scan is required")
    profile = scan["vital_profile"]
    pain_ceiling = scan["pain_ceiling"]
    cases = []
    for acuity in range(1, 6):
        count = DEMO_COHORT_CATEGORY_COUNTS[acuity]
        candidates = scan["candidates"][str(acuity)]
        ranked = sorted(candidates, key=candidate_key)
        complaint_counts = Counter()
        selected = []
        for item in ranked:
            complaint = item["row"]["PresentingComplaint_base"]
            if complaint_counts[complaint] >= math.ceil(count / 3):
                continue
            selected.append(item)
            complaint_counts[complaint] += 1
            if len(selected) == count:
                break
        if len(selected) != count:
            raise ValueError(f"Not enough diverse matching candidates for acuity {acuity}")
        for item in selected:
            ml_acuity = item["ml_acuity"]
            floor = item["safety_floor_acuity"]
            if ml_acuity not in range(1, 6) or floor not in (None, 1, 2):
                raise ValueError("Invalid model result or safety floor")
            if item["final_acuity"] != acuity or min(ml_acuity, floor or 6) != acuity:
                raise ValueError("Final acuity must be the more urgent of ML and the safety floor")
            if item["has_matched_discriminator"] != (floor is not None):
                raise ValueError("Invalid matched-check evidence")
            if item["has_matched_discriminator"] != (acuity in (1, 2)):
                raise ValueError("Candidate does not belong to its requested sample stratum")
            if item["source_estimated_acuity"] != acuity:
                raise ValueError("Selected final result must match the estimated CSV label")
            if not MIN_CLASS_PROBABILITY <= item["ml_probability"] <= 1:
                raise ValueError("Selected ML class must have at least 50% probability")
            row = item["row"]
            if not 16 <= row["Age"] < 75:
                raise ValueError("Demo scope is adults aged 16 through 74")
            if not 0 <= row["pain"] <= pain_ceiling:
                raise ValueError("Selected pain exceeds the source 90th percentile")
            if any(not profile[field]["lower"] <= row[field] <= profile[field]["upper"] for field in VITAL_FIELDS):
                raise ValueError("A selected vital lies outside the source's central 98%")
            peak, mean = vital_distances(row, profile)
            if not math.isclose(peak, item["peak_vital_distance"]) or not math.isclose(mean, item["mean_vital_distance"]):
                raise ValueError("Candidate vital-distance evidence is inconsistent")
            cases.append({
                "row_number": int(row["source_row_number"]),
                "arrival_time": row["DATE"], "age": int(row["Age"]),
                "presenting_complaint": row["PresentingComplaint_base"],
                **{field: float(row[field]) for field in (
                    "temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain")},
                "selection_evidence": {
                    "ml_acuity_before_override": ml_acuity,
                    "final_acuity": acuity,
                    "safety_floor_acuity": floor,
                    "has_matched_discriminator": item["has_matched_discriminator"],
                    "override_applied": floor is not None and floor < ml_acuity,
                    "source_estimated_acuity": acuity,
                    "assigned_class_probability": item["ml_probability"],
                    "peak_vital_distance": peak, "mean_vital_distance": mean,
                },
            })
    if len({row["row_number"] for row in cases}) != 100:
        raise ValueError("The cohort must contain exactly 100 distinct source rows")
    if sum(row["selection_evidence"]["has_matched_discriminator"] for row in cases) != DEMO_COHORT_MATCHED_ROWS:
        raise ValueError("Exactly 60 cases must have a matched safety check")
    return {
        "schema_version": 1, "cohort_id": "uhl_curated_demo_100_v3",
        "dataset_source": DATASET_SOURCE,
        "training_dataset_sha256": DATASET_SHA256, "model_sha256": MODEL_SHA256,
        "source_rows_scanned": scan["source_rows_scanned"],
        "selection": {
            "purpose": "CURATED_DEMONSTRATION_NOT_MODEL_EVALUATION",
            "comparison": "ML baseline plus escalate-only vital safety floor; final result matches original CSV estimated acuity",
            "rule_version": "acuity_override_v1", "official_mts": False,
            "matched_cases": DEMO_COHORT_MATCHED_ROWS,
            "unmatched_cases": 100 - DEMO_COHORT_MATCHED_ROWS,
            "source_values_generated_or_changed": False,
            "selection_profile": PROFILE_ID,
            "ranking": "Lowest maximum then mean IQR-scaled distance from source vital medians; probability then source-row tie break",
            "minimum_assigned_class_probability": MIN_CLASS_PROBABILITY,
            "vital_quantile_bounds": [0.01, 0.99], "vital_profile": profile,
            "pain_quantile_ceiling": 0.9, "maximum_pain": pain_ceiling,
            "complaint_cap_per_category": "Ceiling of category quota divided by three",
            "source_inputs_unchanged": True, "synthetic_vitals": True,
            "excluded_presentations": ["UNWELL CHILD", "LIMPING CHILD", "QUERY COVID-19 before 2020"],
            "age_scope": "16-74; excludes separate paediatric and older-person clinical pathways",
            "cases_per_acuity": {str(i): n for i, n in DEMO_COHORT_CATEGORY_COUNTS.items()},
            "eligible_ml_counts_in_scan": scan["eligible_ml_counts"],
            "qualifying_counts_in_scan": scan["qualifying_counts"],
            "notice": "Unchanged CSV rows selected for 60 matched checks and final-label agreement. This is not an accuracy evaluation or approved MTS validation.",
        },
        "cases": sorted(cases, key=lambda row: (row["arrival_time"], row["row_number"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scan", type=Path, help="Complete scan output from the pinned source/model")
    parser.add_argument("--output", type=Path, default=ROOT / "data/uhl_demo_100.json")
    args = parser.parse_args()
    result = build_cohort(json.loads(args.scan.read_text(encoding="utf-8")))
    payload = (json.dumps(result, indent=2, allow_nan=False) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(json.dumps({"cases": len(result["cases"]), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}))


if __name__ == "__main__":
    main()
