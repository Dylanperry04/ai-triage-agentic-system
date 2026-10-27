"""Offline, complete pinned-source scan for a curated demonstration.

Requires the original full CSV (not shipped in the small runtime package).
This deliberately ranks agreement examples and must never be used as an
unbiased evaluation. Estimated source labels filter demonstration candidates;
they never enter model inference. Outcomes are not used.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import operator
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd

from app.agents.ml_prediction_agent import _select_uhl_serving_acuity
from app.config import settings
from app.constants import DATASET_SHA256, MODEL_SHA256, EXPECTED_SOURCE_ROWS, MODEL_FEATURE_RANGES
from app.rules.discriminators import VITAL_DISCRIMINATORS
from ml_training.uhl_synthetic.dataset import _normalise_features
from ml_training.uhl_synthetic.features import time_bin_from_hour_value, season_from_month_value
from ml_training.uhl_synthetic.serving import validate_uhl_serving_bundle
from scripts.build_demo_cohort import PROFILE_ID, MIN_CLASS_PROBABILITY, VITAL_FIELDS, vital_distances, candidate_key


def batch_safety_floors(frame: pd.DataFrame) -> np.ndarray:
    """Evaluate the shared rules for an offline batch; 6 means no safety floor.

    Non-finite observations never count as matched. Temperature is Fahrenheit
    in the pinned source CSV. Serving still uses the original scalar evaluator.
    """
    floors = np.full(len(frame), 6, dtype=np.int8)
    comparisons = {"<": operator.lt, ">": operator.gt, ">=": operator.ge}
    for rule in VITAL_DISCRIMINATORS:
        values = frame[rule.field].to_numpy(dtype=float)
        if rule.field == "temperature":
            values = (values - 32) * 5 / 9
        matched = np.isfinite(values) & comparisons[rule.operator](values, rule.threshold)
        floors[matched] = np.minimum(floors[matched], rule.priority)
    return floors


def eligible_rows(frame: pd.DataFrame, complaints) -> pd.DataFrame:
    mask = ((frame.Age >= 16) & (frame.Age < 75)
            & frame.PresentingComplaint_base.isin(complaints)
            & ~frame.PresentingComplaint_base.isin(("UNWELL CHILD", "LIMPING CHILD")))
    for field in (*VITAL_FIELDS, "pain"):
        mask &= frame[field].between(*MODEL_FEATURE_RANGES[field])
    mask &= frame.dbp <= frame.sbp
    mask &= (frame.PresentingComplaint_base.ne("QUERY COVID-19")
             | pd.to_datetime(frame.DATE).ge(pd.Timestamp("2020-01-01")))
    return frame.loc[mask].copy()


def source_vital_profile(source: Path, complaints) -> tuple[dict, float]:
    fields = ["Age", "DATE", "PresentingComplaint_base", *VITAL_FIELDS, "pain"]
    frame = eligible_rows(pd.read_csv(source, usecols=fields), complaints)
    profile = {}
    for field in VITAL_FIELDS:
        lower, q1, median, q3, upper = frame[field].quantile([.01, .25, .5, .75, .99])
        if q3 <= q1:
            raise ValueError("Source vital has no usable interquartile range: " + field)
        profile[field] = dict(lower=float(lower), median=float(median),
                              upper=float(upper), iqr=float(q3 - q1))
    return profile, float(frame.pain.quantile(.9))


def scan_candidates(source: Path, model_path: Path) -> dict:
    if hashlib.sha256(source.read_bytes()).hexdigest() != DATASET_SHA256:
        raise ValueError("Source must match the pinned training cohort")
    if hashlib.sha256(model_path.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("Model must match the pinned serving bundle")
    bundle = joblib.load(model_path)
    validate_uhl_serving_bundle(bundle, require_deployable=True)
    model = bundle["model"]
    profile, pain_ceiling = source_vital_profile(source, bundle["seen_presenting_complaints"])
    counters, qualifying, overrides = Counter(), Counter(), Counter()
    best = {i: {} for i in range(1, 6)}
    offset = 0
    for chunk in pd.read_csv(source, chunksize=20000):
        chunk["source_row_number"] = np.arange(offset + 1, offset + len(chunk) + 1)
        offset += len(chunk)
        eligible = eligible_rows(chunk, bundle["seen_presenting_complaints"])
        if eligible.empty:
            continue
        frame = _normalise_features(eligible)
        # Match the deployed timestamp policy, including Dublin DST handling.
        when = pd.to_datetime(eligible.DATE).dt.tz_localize(
            "Europe/Dublin", ambiguous=False, nonexistent="shift_forward")
        frame["month"], frame["hour"] = when.dt.month, when.dt.hour
        frame["time_bin"] = frame.hour.map(time_bin_from_hour_value)
        frame["season"] = frame.month.map(season_from_month_value)
        probabilities = model.predict_proba(frame)
        floors = batch_safety_floors(frame)
        for row, probability, floor_code in zip(eligible.to_dict("records"), probabilities, floors):
            pred = _select_uhl_serving_acuity(dict(zip(map(str, model.classes_), map(float, probability))))
            counters[pred] += 1
            final = min(pred, int(floor_code))
            matched = floor_code < 6
            # The 60 matched slots are the two urgent categories; the other
            # 40 slots retain an unmodified ML result in categories 3-5.
            if bool(matched) != (final in (1, 2)):
                continue
            score = float(probability[list(model.classes_).index(pred)])
            if score < MIN_CLASS_PROBABILITY or pred != int(model.classes_[np.argmax(probability)]):
                continue
            if row["pain"] > pain_ceiling:
                continue
            if int(row["master_estimated_acuity"]) != final:
                continue
            if any(not profile[field]["lower"] <= row[field] <= profile[field]["upper"] for field in VITAL_FIELDS):
                continue
            qualifying[final] += 1
            if final < pred:
                overrides[final] += 1
            peak, mean = vital_distances(row, profile)
            # Keep the estimated label in selection evidence only. The case
            # adapter never passes it to the model; outcomes are omitted.
            inputs = {key: row[key] for key in (
                "source_row_number", "DATE", "Age", "PresentingComplaint_base",
                "temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain")}
            group = best[final].setdefault(row["PresentingComplaint_base"], [])
            group.append(dict(row=inputs, ml_acuity=pred, ml_probability=score,
                              final_acuity=final, has_matched_discriminator=bool(matched),
                              safety_floor_acuity=int(floor_code) if matched else None,
                              source_estimated_acuity=int(row["master_estimated_acuity"]),
                              peak_vital_distance=peak, mean_vital_distance=mean))
        for key in best:
            for complaint in best[key]:
                best[key][complaint] = sorted(best[key][complaint], key=candidate_key)[:100]
        print(json.dumps({"scanned": offset, "ml": dict(counters),
                          "qualifying": dict(qualifying), "overrides": dict(overrides)}), flush=True)
    if offset != EXPECTED_SOURCE_ROWS:
        raise ValueError("Incomplete source scan")
    return {"selection_profile": PROFILE_ID, "vital_profile": profile, "pain_ceiling": pain_ceiling,
            "source_rows_scanned": offset, "training_dataset_sha256": DATASET_SHA256,
            "model_sha256": MODEL_SHA256, "rule_version": "acuity_override_v1",
            "eligible_ml_counts": dict(counters), "qualifying_override_counts": dict(overrides),
            "qualifying_counts": dict(qualifying),
            "candidates": {key: sorted([item for group in groups.values() for item in group], key=candidate_key)
                           for key, groups in best.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=settings.uhl_data_path)
    parser.add_argument("--model", type=Path, default=settings.uhl_model_path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = scan_candidates(args.source, args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
