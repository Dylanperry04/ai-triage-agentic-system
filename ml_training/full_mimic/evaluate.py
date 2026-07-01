"""Full-MIMIC evaluation. Produces aggregate safety-focused metrics: confusion
matrix, per-class recall, severe under-triage, under/over-triage rates,
high-acuity recall, ordinal acuity metrics, and calibration (Brier). No raw rows.

Run on the credentialed environment (see verify_schema.py for env vars).
"""
import json
import sys


def main() -> int:
    from ml_training.full_mimic._safety import require_safe_environment, UnsafeEnvironmentError
    try:
        paths = require_safe_environment()
    except UnsafeEnvironmentError as e:
        sys.stderr.write(f"REFUSED: {e}\n")
        return 2
    from app.config import settings
    settings.mimic_full_ed_dir = paths["ed_dir"]
    from app.data_pipeline.mimic_full_loader import load_mimic_full_cases
    from ml_training.feature_engineering import build_feature_frame_with_meta  # type: ignore
    from ml_training.full_mimic.reports import (
        under_over_triage, high_acuity_recall_report, calibration_report,
        ordinal_acuity_metrics,
    )
    from ml_training.full_mimic.evaluation import (
        patient_grouped_split, temporal_split, assert_no_subject_overlap,
        over_triage_specificity, auroc_pr_auc, bootstrap_ci, subgroup_metrics,
    )

    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import confusion_matrix, recall_score, accuracy_score

    cases = load_mimic_full_cases()
    X, y, feature_names, subject_ids, intimes = build_feature_frame_with_meta(cases)
    mask = [v is not None for v in y]
    X2 = X[mask]
    y2 = np.array([v for v, m in zip(y, mask) if m])
    subj2 = [s for s, m in zip(subject_ids, mask) if m]
    intimes2 = [t for t, m in zip(intimes, mask) if m]

    split = temporal_split(intimes2, subj2)
    split_kind = "temporal"
    if split is None:
        split = patient_grouped_split(subj2)
        split_kind = "patient_grouped"
    tr_idx, va_idx, te_idx = split
    assert_no_subject_overlap(subj2, tr_idx, va_idx, te_idx)

    # Baseline evaluation only: no model selection. Train on development
    # patients (train+validation), report once on untouched test patients.
    dev_idx = np.concatenate([tr_idx, va_idx])
    Xtr, ytr = X2[dev_idx], y2[dev_idx]
    Xte, yte = X2[te_idx], y2[te_idx]
    model = RandomForestClassifier(n_estimators=300, class_weight="balanced",
                                   random_state=42, n_jobs=1).fit(Xtr, ytr)
    pred = model.predict(Xte)
    proba = model.predict_proba(Xte)

    labels = sorted(set(y2.tolist()))
    sex_idx = feature_names.index("sex_male")
    high_cols = [i for i, c in enumerate(model.classes_) if c <= 2]
    proba_high = proba[:, high_cols].sum(axis=1) if high_cols else np.zeros(len(yte))
    cm = confusion_matrix(yte, pred, labels=labels).tolist()
    out = {
        "split_kind": split_kind,
        "n_train": int(len(tr_idx)),
        "n_val": int(len(va_idx)),
        "n_test": int(len(te_idx)),
        "evaluation_protocol": (
            "Patient-grouped or temporal split. No random row-level split. "
            "RandomForest baseline trained on train+validation and reported "
            "once on untouched test patients."
        ),
        "labels": labels,
        "confusion_matrix": cm,
        "accuracy": float(accuracy_score(yte, pred)),
        "accuracy_95ci": bootstrap_ci(yte, pred, lambda a, b: accuracy_score(a, b)),
        "per_class_recall": {str(l): float(r) for l, r in
                             zip(labels, recall_score(yte, pred, labels=labels, average=None, zero_division=0))},
        "under_over_triage": under_over_triage(yte.tolist(), pred.tolist()),
        "ordinal_metrics": ordinal_acuity_metrics(yte.tolist(), pred.tolist()),
        "high_acuity_recall": high_acuity_recall_report(yte.tolist(), pred.tolist()),
        "over_triage_specificity": over_triage_specificity(yte, pred),
        "auroc_pr_auc": auroc_pr_auc(yte, proba_high),
        "subgroups_by_sex": subgroup_metrics(
            yte, pred, ["M" if v == 1.0 else "F" for v in Xte[:, sex_idx]]),
        "calibration": calibration_report(yte.tolist(), proba.tolist(), labels),
        "note": "Aggregate research metrics only. Not clinically validated.",
    }
    (paths["output_dir"] / "full_mimic_evaluation.json").write_text(json.dumps(out, indent=2))
    print(f"Evaluation written to {paths['output_dir']/'full_mimic_evaluation.json'}")
    print(f"  high-acuity recall: {out['high_acuity_recall'].get('recall')}")
    print(f"  under-triage rate: {out['under_over_triage'].get('under_triage_rate')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
