"""
Evaluation methodology for the full-MIMIC acuity model (per external review).

This module exists to make model evaluation HONEST:

  * PATIENT-GROUPED split by subject_id: a patient's repeat ED visits must never
    appear in more than one of train/validation/test (random row-level splitting
    leaks repeat visits and inflates performance).
  * Optional TEMPORAL split by intime when timestamps are available (train on the
    past, test on the future) — a stricter, more realistic protocol.
  * THREE sets: train / validation / untouched test. Preprocessing is fit on
    train only; model selection happens on VALIDATION only; the final number is
    reported ONCE on the untouched test set.
  * An OVER-TRIAGE / specificity constraint so a degenerate "predict everything
    urgent" model (perfect high-acuity recall, useless specificity) cannot win.
  * AUROC / PR-AUC, bootstrap confidence intervals, calibration, and subgroup
    analysis.

No raw rows or identifiers are emitted; subject_id/intime are used only to form
the split indices and are never returned in any report.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


# ── Splitting ────────────────────────────────────────────────────────────────

def patient_grouped_split(
    subject_ids: Sequence,
    *,
    val_frac: float = 0.15,
    test_frac: float = 0.15,
    seed: int = 42,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split ROW indices into (train, val, test) so that all rows sharing a
    subject_id fall entirely within one split. Returns index arrays.

    Patients (not rows) are partitioned, so a patient's repeat visits never span
    splits. Deterministic given the seed.
    """
    subject_ids = list(subject_ids)
    # Map each unique subject to its row indices.
    groups: Dict[object, List[int]] = {}
    for i, s in enumerate(subject_ids):
        groups.setdefault(s, []).append(i)
    unique = list(groups.keys())
    rng = np.random.RandomState(seed)
    rng.shuffle(unique)

    n = len(unique)
    n_test = max(1, int(round(n * test_frac))) if n > 2 else 0
    n_val = max(1, int(round(n * val_frac))) if n > 2 else 0
    test_subj = set(unique[:n_test])
    val_subj = set(unique[n_test:n_test + n_val])

    train_idx, val_idx, test_idx = [], [], []
    for s in unique:
        target = test_idx if s in test_subj else val_idx if s in val_subj else train_idx
        target.extend(groups[s])
    return (np.array(sorted(train_idx)), np.array(sorted(val_idx)),
            np.array(sorted(test_idx)))


def temporal_split(
    intimes: Sequence,
    subject_ids: Optional[Sequence] = None,
    *,
    val_frac: float = 0.15,
    test_frac: float = 0.15,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Split ROW indices chronologically by intime: earliest -> train, then val,
    latest -> test. Returns None if intimes are missing/unparseable (caller falls
    back to the patient-grouped split).

    When subject_ids is provided, the split is ALSO patient-grouped: each PATIENT
    is assigned to a split by their EARLIEST visit time, and all of that patient's
    visits move together. This keeps the protocol temporal (train on earlier
    patients, test on later ones) while guaranteeing a patient's repeat visits
    never span splits. Without subject_ids, falls back to a pure row-temporal
    split (only safe when every row is a distinct patient).
    """
    import pandas as pd
    try:
        ts = pd.to_datetime(list(intimes), errors="coerce")
    except Exception:
        return None
    if ts.isna().any():
        return None
    ts_vals = ts.values.astype("datetime64[ns]")
    n = len(ts_vals)
    if n < 3:
        return None

    if subject_ids is not None:
        sids = list(subject_ids)
        # earliest time per patient
        first_time: Dict[object, np.datetime64] = {}
        rows_by_subj: Dict[object, List[int]] = {}
        for i, s in enumerate(sids):
            rows_by_subj.setdefault(s, []).append(i)
            if s not in first_time or ts_vals[i] < first_time[s]:
                first_time[s] = ts_vals[i]
        ordered_subj = sorted(first_time.keys(), key=lambda s: first_time[s])
        nsub = len(ordered_subj)
        if nsub < 3:
            return None
        n_test = max(1, int(round(nsub * test_frac)))
        n_val = max(1, int(round(nsub * val_frac)))
        n_train = nsub - n_test - n_val
        if n_train < 1:
            return None
        train_s = set(ordered_subj[:n_train])
        val_s = set(ordered_subj[n_train:n_train + n_val])
        tr, va, te = [], [], []
        for s in ordered_subj:
            tgt = tr if s in train_s else va if s in val_s else te
            tgt.extend(rows_by_subj[s])
        return np.array(sorted(tr)), np.array(sorted(va)), np.array(sorted(te))

    order = np.argsort(ts_vals)
    n_test = max(1, int(round(n * test_frac)))
    n_val = max(1, int(round(n * val_frac)))
    n_train = n - n_test - n_val
    if n_train < 1:
        return None
    return (np.sort(order[:n_train]), np.sort(order[n_train:n_train + n_val]),
            np.sort(order[n_train + n_val:]))


def assert_no_subject_overlap(subject_ids: Sequence, train_idx, val_idx, test_idx) -> None:
    """Raise AssertionError if any subject_id appears in more than one split. The
    splitter guarantees this; this is the explicit check used by tests/at runtime.
    """
    sids = list(subject_ids)
    tr = {sids[i] for i in train_idx}
    va = {sids[i] for i in val_idx}
    te = {sids[i] for i in test_idx}
    assert not (tr & va), f"subject leak train/val: {tr & va}"
    assert not (tr & te), f"subject leak train/test: {tr & te}"
    assert not (va & te), f"subject leak val/test: {va & te}"


# ── Metrics ──────────────────────────────────────────────────────────────────

def _high_acuity_mask(y: np.ndarray, high_threshold: int = 2) -> np.ndarray:
    """Acuity 1-2 are high-acuity (urgent) on the MIMIC 1-5 scale (1 most urgent)."""
    return y <= high_threshold


def over_triage_specificity(y_true: np.ndarray, y_pred: np.ndarray,
                            high_threshold: int = 2) -> Dict:
    """Binary view (high-acuity vs not). Reports sensitivity (high-acuity recall),
    specificity, over-triage rate (non-urgent predicted urgent), and the urgent
    prediction rate (to expose a 'predict everything urgent' model)."""
    yt_high = _high_acuity_mask(y_true, high_threshold)
    yp_high = _high_acuity_mask(y_pred, high_threshold)
    tp = int(np.sum(yt_high & yp_high))
    fn = int(np.sum(yt_high & ~yp_high))
    tn = int(np.sum(~yt_high & ~yp_high))
    fp = int(np.sum(~yt_high & yp_high))
    sens = tp / (tp + fn) if (tp + fn) else None
    spec = tn / (tn + fp) if (tn + fp) else None
    over = fp / (fp + tn) if (fp + tn) else None
    urgent_rate = float(np.mean(yp_high)) if len(yp_high) else None
    return {
        "high_acuity_sensitivity": sens,
        "specificity": spec,
        "over_triage_rate": over,
        "predicted_urgent_rate": urgent_rate,
    }


def auroc_pr_auc(y_true: np.ndarray, proba_high: np.ndarray) -> Dict:
    """AUROC and PR-AUC for the binary high-acuity task, given P(high-acuity)."""
    from sklearn.metrics import roc_auc_score, average_precision_score
    yt = _high_acuity_mask(y_true).astype(int)
    out = {"auroc": None, "pr_auc": None}
    if len(set(yt.tolist())) < 2:
        return out
    try:
        out["auroc"] = float(roc_auc_score(yt, proba_high))
        out["pr_auc"] = float(average_precision_score(yt, proba_high))
    except Exception:
        pass
    return out


def bootstrap_ci(y_true: np.ndarray, y_pred: np.ndarray, metric_fn,
                 *, n_boot: int = 200, seed: int = 42) -> Dict:
    """Percentile bootstrap 95% CI for a scalar metric_fn(y_true, y_pred)."""
    rng = np.random.RandomState(seed)
    n = len(y_true)
    if n == 0:
        return {"point": None, "ci_low": None, "ci_high": None}
    point = metric_fn(y_true, y_pred)
    vals = []
    for _ in range(n_boot):
        idx = rng.randint(0, n, n)
        try:
            vals.append(metric_fn(y_true[idx], y_pred[idx]))
        except Exception:
            continue
    if not vals:
        return {"point": point, "ci_low": None, "ci_high": None}
    return {"point": float(point),
            "ci_low": float(np.percentile(vals, 2.5)),
            "ci_high": float(np.percentile(vals, 97.5))}


def subgroup_metrics(y_true: np.ndarray, y_pred: np.ndarray,
                     subgroup: Sequence) -> Dict:
    """High-acuity sensitivity/specificity within each subgroup value (e.g. sex).
    The subgroup vector is a non-sensitive categorical (e.g. 'M'/'F')."""
    out = {}
    sg = np.array([str(s) for s in subgroup])
    for val in sorted(set(sg.tolist())):
        m = sg == val
        if int(np.sum(m)) == 0:
            continue
        out[val] = over_triage_specificity(y_true[m], y_pred[m])
    return out


# ── Selection constraint ─────────────────────────────────────────────────────

def passes_over_triage_constraint(metrics: Dict,
                                  *, min_specificity: float = 0.10,
                                  max_predicted_urgent_rate: float = 0.95) -> bool:
    """A candidate must clear a minimum specificity AND must not flag almost
    everything as urgent. This blocks the degenerate 'predict everything urgent'
    model that maximises high-acuity recall trivially."""
    spec = metrics.get("specificity")
    urgent = metrics.get("predicted_urgent_rate")
    if spec is None or urgent is None:
        return False
    return spec >= min_specificity and urgent <= max_predicted_urgent_rate
