"""
Tests for the corrected full-MIMIC evaluation methodology (per external review):
  - patient-grouped split: no subject_id spans train/val/test
  - temporal split is ALSO patient-grouped (repeat visits never leak)
  - three distinct sets
  - over-triage/specificity constraint rejects a 'predict everything urgent' model
  - AUROC/PR-AUC, bootstrap CI, subgroup metrics behave sanely
"""
import numpy as np

from ml_training.full_mimic.evaluation import (
    patient_grouped_split, temporal_split, assert_no_subject_overlap,
    over_triage_specificity, auroc_pr_auc, bootstrap_ci, subgroup_metrics,
    passes_over_triage_constraint,
)


class TestPatientGroupedSplit:
    def test_no_subject_spans_splits(self):
        # 60 patients, 1-3 visits each
        rng = np.random.RandomState(0)
        subject_ids = []
        for p in range(60):
            subject_ids += [p] * rng.randint(1, 4)
        tr, va, te = patient_grouped_split(subject_ids)
        assert_no_subject_overlap(subject_ids, tr, va, te)  # raises on leak
        # three non-empty sets and a full partition
        assert len(tr) and len(va) and len(te)
        assert len(tr) + len(va) + len(te) == len(subject_ids)

    def test_repeat_visits_move_together(self):
        subject_ids = [1, 1, 1, 2, 2, 3, 4, 4, 5, 6, 7, 8, 9, 10]
        tr, va, te = patient_grouped_split(subject_ids, seed=1)
        sid = np.array(subject_ids)
        for s in set(subject_ids):
            rows = set(np.where(sid == s)[0].tolist())
            in_tr = bool(rows & set(tr.tolist()))
            in_va = bool(rows & set(va.tolist()))
            in_te = bool(rows & set(te.tolist()))
            assert sum([in_tr, in_va, in_te]) == 1, f"subject {s} spans splits"

    def test_deterministic(self):
        s = list(range(40)) + list(range(40))
        a = patient_grouped_split(s, seed=7)
        b = patient_grouped_split(s, seed=7)
        for x, y in zip(a, b):
            assert np.array_equal(x, y)


class TestTemporalSplitIsPatientGrouped:
    def test_temporal_split_does_not_leak_subjects(self):
        # patients with multiple visits at different times
        subject_ids, intimes = [], []
        for p in range(30):
            for v in range(np.random.RandomState(p).randint(1, 4)):
                subject_ids.append(p)
                intimes.append(f"2180-{(p % 12) + 1:02d}-{(v % 28) + 1:02d} 10:00:00")
        res = temporal_split(intimes, subject_ids)
        assert res is not None
        tr, va, te = res
        assert_no_subject_overlap(subject_ids, tr, va, te)

    def test_temporal_returns_none_without_timestamps(self):
        assert temporal_split([None, None, None], [1, 2, 3]) is None


class TestOverTriageConstraint:
    def test_predict_everything_urgent_is_rejected(self):
        # truth: half urgent (<=2), half not
        y_true = np.array([1, 2, 3, 4, 5, 1, 2, 4, 5, 3])
        # degenerate model: predict acuity 1 (urgent) for everyone
        y_pred_all_urgent = np.ones_like(y_true)
        m = over_triage_specificity(y_true, y_pred_all_urgent)
        assert m["high_acuity_sensitivity"] == 1.0          # trivially perfect
        assert m["specificity"] == 0.0                       # useless
        assert m["predicted_urgent_rate"] == 1.0
        assert passes_over_triage_constraint(m) is False     # REJECTED

    def test_reasonable_model_passes(self):
        y_true = np.array([1, 2, 3, 4, 5, 1, 2, 4, 5, 3])
        y_pred = np.array([1, 2, 3, 4, 5, 2, 2, 3, 5, 4])  # decent
        m = over_triage_specificity(y_true, y_pred)
        assert passes_over_triage_constraint(m) is True


class TestMetrics:
    def test_auroc_pr_auc_range(self):
        y_true = np.array([1, 2, 3, 4, 5, 1, 2, 4, 5, 3])
        # P(high-acuity) higher for the truly urgent
        proba_high = np.array([.9, .8, .2, .1, .05, .85, .75, .15, .05, .3])
        out = auroc_pr_auc(y_true, proba_high)
        assert out["auroc"] is not None and 0.5 <= out["auroc"] <= 1.0
        assert out["pr_auc"] is not None

    def test_bootstrap_ci_brackets_point(self):
        from sklearn.metrics import accuracy_score
        y_true = np.array([1, 2, 3, 4, 5] * 8)
        y_pred = y_true.copy()
        y_pred[:5] = 3  # a few errors
        ci = bootstrap_ci(y_true, y_pred, lambda a, b: accuracy_score(a, b), n_boot=100)
        assert ci["ci_low"] <= ci["point"] <= ci["ci_high"]

    def test_subgroup_metrics_per_value(self):
        y_true = np.array([1, 2, 3, 4, 1, 2, 3, 4])
        y_pred = np.array([1, 2, 3, 4, 1, 2, 4, 4])
        sex = ["M", "M", "M", "M", "F", "F", "F", "F"]
        out = subgroup_metrics(y_true, y_pred, sex)
        assert set(out.keys()) == {"M", "F"}
        assert "specificity" in out["M"]
