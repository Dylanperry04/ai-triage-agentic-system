"""Curated demo selection uses unchanged inputs and disclosed estimated labels."""
import pandas as pd
import pytest

from scripts.build_demo_cohort import build_cohort, candidate_key
from scripts.scan_demo_candidates import eligible_rows, batch_safety_floors
from app.rules.discriminators import evaluate_vital_discriminators, VITAL_DISCRIMINATORS
from app.schemas.internal import TriageTimeInput
from app.constants import DATASET_SHA256, MODEL_SHA256


def test_less_extreme_vitals_rank_before_higher_confidence():
    typical = dict(peak_vital_distance=.3, mean_vital_distance=.1,
                   ml_probability=.6, row={"source_row_number": 2})
    extreme = dict(peak_vital_distance=2, mean_vital_distance=.5,
                   ml_probability=.99, row={"source_row_number": 1})
    assert sorted([extreme, typical], key=candidate_key) == [typical, extreme]
    tie = {**typical, "row": {"source_row_number": 1}}
    assert sorted([typical, tie], key=candidate_key)[0] is tie


@pytest.mark.parametrize("change", [
    {"Age": 15}, {"PresentingComplaint_base": "UNWELL CHILD"},
    {"PresentingComplaint_base": "LIMPING CHILD"},
    {"PresentingComplaint_base": "QUERY COVID-19", "DATE": "2014-01-01"},
    {"heartrate": 0}, {"o2sat": float("nan")}, {"dbp": 130},
])
def test_inconsistent_or_unusable_source_rows_are_excluded(change):
    base = dict(Age=40, DATE="2022-01-01", PresentingComplaint_base="OTHER",
                temperature=98.6, heartrate=80, resprate=18, o2sat=98,
                sbp=120, dbp=80, pain=2)
    result = eligible_rows(pd.DataFrame([base, {**base, **change}]),
                           ["OTHER", "UNWELL CHILD", "LIMPING CHILD", "QUERY COVID-19"])
    assert result.index.tolist() == [0]


def test_old_highest_confidence_candidate_scan_is_not_reused():
    with pytest.raises(ValueError, match="60-matched-check"):
        build_cohort(dict(source_rows_scanned=777176, training_dataset_sha256=DATASET_SHA256,
                          model_sha256=MODEL_SHA256, rule_version="acuity_override_v1"))


@pytest.mark.parametrize("change", [{"heartrate": 80.000001}, {"pain": 3}, {"age": 40.1}, {"arrival_time": "2022-01-02"}])
def test_provenance_verifier_rejects_changed_values_without_rounding(change):
    from scripts.verify_demo_source import verify_values
    original = dict(DATE="2022-01-01", PresentingComplaint_base="OTHER", Age=40,
                    temperature=98.6, heartrate=80, resprate=16, o2sat=98, sbp=120, dbp=80,
                    pain=2, master_estimated_acuity=3)
    chosen = dict(arrival_time=original["DATE"], presenting_complaint="OTHER", age=40,
                  **{k: original[k] for k in ("temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain")},
                  selection_evidence={"source_estimated_acuity": 3})
    verify_values(original, chosen)
    with pytest.raises(ValueError, match="Source value differs"):
        verify_values(original, {**chosen, **change})


@pytest.mark.parametrize("rule", VITAL_DISCRIMINATORS, ids=lambda r: r.code)
def test_offline_batch_matches_serving_rules_at_every_boundary(rule):
    rows = []
    for delta in (-.01, 0, .01):
        row = dict(temperature=98.6, heartrate=80, resprate=16, o2sat=98, sbp=120, dbp=80, pain=2)
        value = rule.threshold + delta
        row[rule.field] = value * 9 / 5 + 32 if rule.field == "temperature" else value
        rows.append(row)
    actual = batch_safety_floors(pd.DataFrame(rows)).tolist()
    expected = []
    for row in rows:
        triage = TriageTimeInput(subject_id=1, stay_id=1, temperature_unit="F", **{**row, "pain": "2"})
        evaluated = evaluate_vital_discriminators(triage)
        expected.append(min((r["priority"] for r in evaluated if r["status"] == "matched"), default=6))
    assert actual == expected
