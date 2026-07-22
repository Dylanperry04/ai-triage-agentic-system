"""Aggregate safety-report functions for full-MIMIC evaluation. Pure functions
over label lists — no data loading, no raw rows — so they are unit-testable with
synthetic labels. Acuity convention: 1 = most urgent ... 5 = least urgent.
"""
from __future__ import annotations

from typing import Dict, List


def under_over_triage(y_true: List[int], y_pred: List[int]) -> Dict:
    """Under-triage = predicted LESS urgent than truth (pred > true). Over-triage =
    predicted MORE urgent (pred < true)."""
    n = len(y_true)
    if n == 0:
        return {
            "n": 0,
            "under_triage_rate": None,
            "severe_under_triage_rate": None,
            "over_triage_rate": None,
        }
    under = sum(1 for t, p in zip(y_true, y_pred) if p > t)
    severe_under = sum(1 for t, p in zip(y_true, y_pred) if (p - t) >= 2)
    over = sum(1 for t, p in zip(y_true, y_pred) if p < t)
    exact = sum(1 for t, p in zip(y_true, y_pred) if p == t)
    within_1 = sum(1 for t, p in zip(y_true, y_pred) if abs(p - t) <= 1)
    return {
        "n": n,
        "under_triage_count": under,
        "severe_under_triage_count": severe_under,
        "over_triage_count": over,
        "exact_count": exact,
        "within_1_acuity_level_count": within_1,
        "under_triage_rate": under / n,
        "severe_under_triage_rate": severe_under / n,
        "over_triage_rate": over / n,
        "exact_rate": exact / n,
        "within_1_acuity_level_accuracy": within_1 / n,
    }


def ordinal_acuity_metrics(y_true: List[int], y_pred: List[int]) -> Dict:
    """Ordinal 1-5 acuity metrics. Lower acuity numbers are more urgent, so
    distance-based metrics are useful alongside classification metrics."""
    n = len(y_true)
    if n == 0:
        return {
            "n": 0,
            "mae": None,
            "within_1_acuity_level_accuracy": None,
            "quadratic_weighted_kappa": None,
        }
    errors = [abs(int(p) - int(t)) for t, p in zip(y_true, y_pred)]
    out = {
        "n": n,
        "mae": sum(errors) / n,
        "within_1_acuity_level_accuracy": sum(1 for e in errors if e <= 1) / n,
        "quadratic_weighted_kappa": None,
    }
    try:
        from sklearn.metrics import cohen_kappa_score
        out["quadratic_weighted_kappa"] = float(
            cohen_kappa_score(y_true, y_pred, weights="quadratic")
        )
    except Exception:
        pass
    return out


def high_acuity_recall_report(y_true: List[int], y_pred: List[int],
                              high_acuity_levels=(1, 2)) -> Dict:
    """Recall on high-acuity cases (the safety-critical ones): of truly
    high-acuity cases, how many were predicted high-acuity."""
    hi = [(t, p) for t, p in zip(y_true, y_pred) if t in high_acuity_levels]
    if not hi:
        return {"n_high_acuity": 0, "recall": None}
    caught = sum(1 for t, p in hi if p in high_acuity_levels)
    return {
        "n_high_acuity": len(hi),
        "caught_as_high_acuity": caught,
        "recall": caught / len(hi),
        "high_acuity_levels": list(high_acuity_levels),
    }


def calibration_report(y_true: List[int], proba: List[List[float]],
                       labels: List[int], n_bins: int = 10) -> Dict:
    """Multiclass calibration via mean Brier score across one-vs-rest, plus a
    simple reliability summary. proba rows align with `labels` columns."""
    if not y_true or not proba:
        return {"brier_mean": None}
    idx = {l: i for i, l in enumerate(labels)}
    briers = []
    for li, lab in enumerate(labels):
        # one-vs-rest Brier for this class
        s = 0.0
        for t, row in zip(y_true, proba):
            o = 1.0 if t == lab else 0.0
            s += (row[li] - o) ** 2
        briers.append(s / len(y_true))
    return {
        "brier_per_class": {str(l): float(b) for l, b in zip(labels, briers)},
        "brier_mean": float(sum(briers) / len(briers)),
        "note": "Lower Brier = better calibrated. One-vs-rest mean.",
    }
