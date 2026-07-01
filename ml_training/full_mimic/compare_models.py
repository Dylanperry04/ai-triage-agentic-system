"""
Full-MIMIC multi-model comparison with TRIAGE-SAFETY model selection.

train.py is the simple baseline trainer (RandomForest). This script is separate:
it trains several candidate models and selects the best by triage-safety metrics,
NOT raw accuracy (an earlier comparison found the accuracy-optimal model is not the
safety-optimal one). Selection priority:

  1. HIGH-ACUITY RECALL  (catching truly urgent cases — the safety-critical metric)
  2. LOW SEVERE UNDER-TRIAGE RATE
  3. LOW UNDER-TRIAGE RATE (predicting less urgent than truth is the dangerous error)
  4. macro F1 / weighted F1 / accuracy (tiebreakers only)

Deployable structured candidates: Logistic Regression, Random Forest,
ExtraTrees, Gradient Boosting, HistGradientBoosting, calibrated Linear SVM,
soft-voting ensemble, and (when installed) XGBoost, LightGBM, CatBoost.
Experimental TF-IDF text-only and structured+TF-IDF baselines are reported
separately and are not selected for serving under the current structured
FEATURE_NAMES runtime contract. A missing optional library is skipped, never
fatal.

Outputs (aggregate only — NEVER raw rows): a JSON comparison report, a CSV
comparison table, a confusion matrix per candidate (in the JSON), a selected-model
rationale, the selected model artefact, and an updated model card.

Runs ONLY on the credentialed/approved environment (require_safe_environment):
needs MIMIC_FULL_ED_DIR outside the repo, PATIENT_DATA_MODE=true or
LOCAL_CREDENTIALED_RESEARCH=true, and an outside-repo output dir. Use
--quick-test (or MIMIC_COMPARE_QUICK=1) to shrink estimators so tests finish fast.
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import date
from uuid import uuid4

for _thread_env in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_thread_env, "1")


def _quick() -> bool:
    return os.environ.get("MIMIC_COMPARE_QUICK", "") == "1"


_ALL_CANDIDATES = (
    "logistic_regression", "random_forest", "extra_trees",
    "gradient_boosting", "hist_gradient_boosting", "calibrated_linear_svm",
    "soft_voting_ensemble", "xgboost", "lightgbm", "catboost",
)
_BASIC_CANDIDATES = ("logistic_regression", "random_forest")


def _candidate_names(raw: str | None = None) -> tuple[list[str], str]:
    """Resolve requested candidates. Quick-test defaults to the deterministic
    basic set so CI/dev runs never hang because an optional heavy library happens
    to be installed."""
    raw = (raw or os.environ.get("MIMIC_COMPARE_CANDIDATES") or "").strip().lower()
    if not raw:
        raw = "basic" if _quick() else "all"
    if raw == "basic":
        return list(_BASIC_CANDIDATES), "basic"
    if raw == "all":
        return list(_ALL_CANDIDATES), "all"
    names = [p.strip() for p in raw.split(",") if p.strip()]
    bad = [n for n in names if n not in _ALL_CANDIDATES]
    if bad:
        raise ValueError(
            f"Unknown candidate(s): {bad}. Allowed: basic, all, "
            f"or comma-list of {_ALL_CANDIDATES}"
        )
    return names, ",".join(names)


def _candidates(names: list[str] | None = None):
    """Return [(name, estimator), ...]. Estimator sizes shrink in quick-test mode."""
    names = list(names or _candidate_names()[0])
    requested = set(names)
    n_est = 15 if _quick() else 300
    iters = 15 if _quick() else 300
    cands = []
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import (
        ExtraTreesClassifier,
        GradientBoostingClassifier,
        HistGradientBoostingClassifier,
        RandomForestClassifier,
        VotingClassifier,
    )
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import FunctionTransformer, StandardScaler
    from sklearn.svm import LinearSVC

    def _pipeline(estimator, *, scale: bool = False):
        steps = []
        if scale:
            steps.append(("scaler", StandardScaler()))
        else:
            steps.append(("identity", FunctionTransformer(validate=False)))
        steps.append(("estimator", estimator))
        return Pipeline(steps)

    if "logistic_regression" in requested:
        cands.append(("logistic_regression",
                      _pipeline(
                          LogisticRegression(max_iter=500 if _quick() else 1000,
                                             class_weight="balanced", n_jobs=1),
                          scale=True,
                      )))
    if "random_forest" in requested:
        cands.append(("random_forest",
                      _pipeline(RandomForestClassifier(
                          n_estimators=n_est, class_weight="balanced",
                          random_state=42, n_jobs=1))))
    if "extra_trees" in requested:
        cands.append(("extra_trees",
                      _pipeline(ExtraTreesClassifier(
                          n_estimators=n_est, class_weight="balanced",
                          random_state=42, n_jobs=1))))
    if "gradient_boosting" in requested:
        cands.append(("gradient_boosting",
                      _pipeline(GradientBoostingClassifier(
                          n_estimators=n_est, random_state=42))))
    if "hist_gradient_boosting" in requested:
        cands.append(("hist_gradient_boosting",
                      _pipeline(HistGradientBoostingClassifier(
                          max_iter=iters, random_state=42))))
    if "calibrated_linear_svm" in requested:
        cands.append(("calibrated_linear_svm",
                      _pipeline(
                          CalibratedClassifierCV(
                              estimator=LinearSVC(
                                  class_weight="balanced",
                                  random_state=42,
                                  max_iter=1000 if _quick() else 5000,
                              ),
                              method="sigmoid",
                              cv=3,
                          ),
                          scale=True,
                      )))
    if "soft_voting_ensemble" in requested:
        lr = _pipeline(
            LogisticRegression(
                max_iter=500 if _quick() else 1000,
                class_weight="balanced",
                n_jobs=1,
            ),
            scale=True,
        )
        rf = _pipeline(RandomForestClassifier(
            n_estimators=n_est, class_weight="balanced", random_state=43, n_jobs=1))
        et = _pipeline(ExtraTreesClassifier(
            n_estimators=n_est, class_weight="balanced", random_state=44, n_jobs=1))
        cands.append(("soft_voting_ensemble",
                      VotingClassifier(
                          estimators=[("lr", lr), ("rf", rf), ("et", et)],
                          voting="soft",
                          n_jobs=1,
                      )))
    if "xgboost" in requested:
        try:
            from xgboost import XGBClassifier
            from ml_training.full_mimic.label_remap import LabelRemapClassifier
            cands.append(("xgboost",
                          LabelRemapClassifier(
                              _pipeline(XGBClassifier(
                                  n_estimators=n_est, random_state=42,
                                  eval_metric="mlogloss",
                                  tree_method="hist", n_jobs=1))
                          )))
        except Exception:
            pass
    if "lightgbm" in requested:
        try:
            from lightgbm import LGBMClassifier
            cands.append(("lightgbm",
                          _pipeline(LGBMClassifier(
                              n_estimators=n_est, class_weight="balanced",
                              random_state=42, verbose=-1, n_jobs=1))))
        except Exception:
            pass
    if "catboost" in requested:
        try:
            from catboost import CatBoostClassifier
            cands.append(("catboost",
                          _pipeline(CatBoostClassifier(
                              iterations=iters, random_seed=42,
                              verbose=0, thread_count=1))))
        except Exception:
            pass
    return cands


def _safety_score(metrics: dict) -> tuple:
    """Sort key for SAFETY-FIRST selection (higher is better)."""
    har = metrics["high_acuity_recall"].get("recall")
    severe_uot = metrics["under_over_triage"].get("severe_under_triage_rate")
    uot = metrics["under_over_triage"].get("under_triage_rate")
    macro_f1 = metrics.get("macro_f1", 0.0)
    weighted_f1 = metrics.get("weighted_f1", 0.0)
    acc = metrics.get("accuracy", 0.0)
    har = -1.0 if har is None else har
    severe_uot = 1.0 if severe_uot is None else severe_uot
    uot = 1.0 if uot is None else uot
    return (har, -severe_uot, -uot, macro_f1, weighted_f1, acc)


def _feature_schema_hash(feature_names) -> str:
    payload = json.dumps(list(feature_names), separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _patient_overlap_count(patient_ids, *splits) -> int:
    groups = []
    patient_ids = list(patient_ids)
    for idx in splits:
        groups.append({patient_ids[int(i)] for i in idx})
    overlap = set()
    for i, left in enumerate(groups):
        for right in groups[i + 1:]:
            overlap |= (left & right)
    return len(overlap)


def _training_provenance(
    *,
    training_run_id: str,
    split_kind: str,
    feature_schema_hash: str,
    feature_names,
    n_train: int,
    n_val: int,
    n_test: int,
    patient_overlap_count: int,
    candidate_mode: str,
    candidate_names,
    quick_test: bool,
    selected_model: str | None = None,
    model_artifact_sha256: str | None = None,
) -> dict:
    split_type = (
        "temporal_patient_level_group_split"
        if split_kind == "temporal"
        else "patient_level_group_split"
    )
    return {
        "training_run_id": training_run_id,
        "dataset_source": "MIMIC-IV-ED-Full-v2.2",
        "training_data_path_class": "credentialed_mimic_full",
        "synthetic_data_used": False,
        "demo_fixture_used": False,
        "test_fixture_used": False,
        "patient_level_split": True,
        "split_type": split_type,
        "split_kind": split_kind,
        "patient_overlap_train_test": int(patient_overlap_count),
        "test_set_used_for_model_selection": False,
        "final_test_evaluation_once": True,
        "preprocessing_inside_pipeline": True,
        "leakage_audit_passed": True,
        "synthetic_audit_passed": True,
        "feature_schema_hash": feature_schema_hash,
        "feature_count": int(len(feature_names)),
        "n_train": int(n_train),
        "n_val": int(n_val),
        "n_test": int(n_test),
        "record_count": int(n_train + n_val + n_test),
        "candidate_mode": candidate_mode,
        "candidate_names_requested": list(candidate_names),
        "quick_test_mode": bool(quick_test),
        "selected_model": selected_model,
        "model_artifact_sha256": model_artifact_sha256,
        "not_clinically_validated": True,
    }


def _write_feature_schema(path, feature_names, feature_schema_hash):
    payload = {
        "feature_names": list(feature_names),
        "feature_schema_hash": feature_schema_hash,
        "triage_time_only": True,
        "leakage_audit_passed": True,
        "synthetic_data_used": False,
        "demo_fixture_used": False,
        "test_fixture_used": False,
    }
    path.write_text(json.dumps(payload, indent=2))


def _write_dataset_card(path, comparison):
    payload = {
        "dataset": "MIMIC-IV-ED-Full-v2.2",
        "dataset_source": "MIMIC-IV-ED-Full-v2.2",
        "credentialed_data": True,
        "synthetic_data_used": False,
        "demo_fixture_used": False,
        "test_fixture_used": False,
        "split_kind": comparison.get("split_kind"),
        "patient_level_split": True,
        "patient_overlap_train_test": comparison.get("patient_overlap_train_test"),
        "n_train": comparison.get("n_train"),
        "n_val": comparison.get("n_val"),
        "n_test": comparison.get("n_test"),
        "generated": comparison.get("generated"),
        "training_run_id": comparison.get("training_run_id"),
        "not_clinically_validated": True,
    }
    path.write_text(json.dumps(payload, indent=2))


def _chief_complaints_from_cases(cases) -> list[str]:
    texts = []
    for case in cases:
        if hasattr(case, "model_dump"):
            case = case.model_dump(mode="json")
        triage = case.get("triage") or {}
        texts.append(str(triage.get("chiefcomplaint") or ""))
    return texts


def _experimental_text_candidates(feature_names):
    from sklearn.compose import ColumnTransformer
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    text_lr = Pipeline([
        ("tfidf", TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=2,
            max_features=5000,
        )),
        ("estimator", LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            n_jobs=1,
        )),
    ])
    combined_pre = ColumnTransformer([
        ("text", TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=2,
            max_features=5000,
        ), "chiefcomplaint_text"),
        ("structured", StandardScaler(), list(feature_names)),
    ])
    combined_lr = Pipeline([
        ("features", combined_pre),
        ("estimator", LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            n_jobs=1,
        )),
    ])
    return [
        ("tfidf_logistic_regression_text_only", "text", text_lr),
        ("structured_plus_tfidf_logistic_regression", "combined", combined_lr),
    ]


def _combined_frame(X, texts, feature_names):
    import pandas as pd

    df = pd.DataFrame(X, columns=list(feature_names))
    df["chiefcomplaint_text"] = list(texts)
    return df


def evaluate_candidate(name, est, Xtr, Xte, ytr, yte, labels):
    from ml_training.full_mimic.reports import (
        under_over_triage, high_acuity_recall_report, calibration_report,
        ordinal_acuity_metrics,
    )
    from sklearn.metrics import (
        accuracy_score, f1_score, precision_recall_fscore_support, confusion_matrix,
    )
    import numpy as np

    t0 = time.perf_counter()
    est.fit(Xtr, ytr)
    train_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    pred = est.predict(Xte)
    infer_s = time.perf_counter() - t1
    pred = np.asarray(pred).ravel()  # CatBoost returns 2D (n,1); flatten

    try:
        proba = est.predict_proba(Xte)
        proba = proba.tolist()
        calib = calibration_report(yte.tolist(), proba, labels)
    except Exception:
        calib = {"brier_mean": None}

    # Per-class precision/recall/F1
    p, r, f, sup = precision_recall_fscore_support(
        yte, pred, labels=labels, zero_division=0)
    per_class = {
        str(lbl): {"precision": float(p[i]), "recall": float(r[i]),
                   "f1": float(f[i]), "support": int(sup[i])}
        for i, lbl in enumerate(labels)
    }
    cm = confusion_matrix(yte, pred, labels=labels).tolist()

    return {
        "model_name": name,
        "accuracy": float(accuracy_score(yte, pred)),
        "macro_f1": float(f1_score(yte, pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(yte, pred, average="weighted", zero_division=0)),
        "high_acuity_recall": high_acuity_recall_report(yte.tolist(), pred.tolist()),
        "under_over_triage": under_over_triage(yte.tolist(), pred.tolist()),
        "ordinal_metrics": ordinal_acuity_metrics(yte.tolist(), pred.tolist()),
        "calibration": calib,
        "per_class": per_class,
        "confusion_matrix": cm,
        "confusion_matrix_labels": [str(l) for l in labels],
        "train_seconds": round(train_s, 3),
        "infer_seconds": round(infer_s, 3),
    }


def _write_csv(path, candidates, labels):
    cols = [
        "model_name", "high_acuity_recall", "severe_under_triage_rate",
        "under_triage_rate", "over_triage_rate", "mae",
        "quadratic_weighted_kappa", "within_1_acuity_level_accuracy",
        "accuracy", "macro_f1", "weighted_f1", "train_seconds", "infer_seconds",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in candidates:
            w.writerow([
                c["model_name"],
                (c["high_acuity_recall"] or {}).get("recall"),
                (c["under_over_triage"] or {}).get("severe_under_triage_rate"),
                (c["under_over_triage"] or {}).get("under_triage_rate"),
                (c["under_over_triage"] or {}).get("over_triage_rate"),
                (c.get("ordinal_metrics") or {}).get("mae"),
                (c.get("ordinal_metrics") or {}).get("quadratic_weighted_kappa"),
                (c.get("ordinal_metrics") or {}).get("within_1_acuity_level_accuracy"),
                c["accuracy"], c["macro_f1"], c["weighted_f1"],
                c["train_seconds"], c["infer_seconds"],
            ])


def _write_model_card(path, best, comparison, labels):
    test_metrics = comparison.get("untouched_test_metrics") or {}
    card = {
        "model_name": "mimic_full_acuity_selected",
        "model_kind": best["model_name"],
        "selected_by": "triage_safety_metrics (high-acuity recall, then low "
                       "severe under-triage, low under-triage, then F1/accuracy)",
        "dataset": "MIMIC-IV-ED-Full-v2.2 (credentialed; read from MIMIC_FULL_ED_DIR)",
        "dataset_source": "MIMIC-IV-ED-Full-v2.2",
        "synthetic_data_used": False,
        "demo_fixture_used": False,
        "test_fixture_used": False,
        "labels": [str(l) for l in labels],
        "headline_metrics": {
            "split": "untouched_test",
            "high_acuity_recall": (
                (test_metrics.get("high_acuity_recall") or {}).get("recall")
            ),
            "severe_under_triage_rate": (
                (test_metrics.get("under_over_triage") or {}).get(
                    "severe_under_triage_rate"
                )
            ),
            "under_triage_rate": (
                (test_metrics.get("under_over_triage") or {}).get("under_triage_rate")
            ),
            "mae": (test_metrics.get("ordinal_metrics") or {}).get("mae"),
            "quadratic_weighted_kappa": (
                (test_metrics.get("ordinal_metrics") or {}).get(
                    "quadratic_weighted_kappa"
                )
            ),
            "within_1_acuity_level_accuracy": (
                (test_metrics.get("ordinal_metrics") or {}).get(
                    "within_1_acuity_level_accuracy"
                )
            ),
            "accuracy": test_metrics.get("accuracy"),
            "macro_f1": test_metrics.get("macro_f1"),
            "weighted_f1": test_metrics.get("weighted_f1"),
        },
        "validation_selection_metrics": {
            "high_acuity_recall": (best["high_acuity_recall"] or {}).get("recall"),
            "severe_under_triage_rate": (
                (best["under_over_triage"] or {}).get("severe_under_triage_rate")
            ),
            "under_triage_rate": (best["under_over_triage"] or {}).get("under_triage_rate"),
            "mae": (best.get("ordinal_metrics") or {}).get("mae"),
            "quadratic_weighted_kappa": (
                (best.get("ordinal_metrics") or {}).get("quadratic_weighted_kappa")
            ),
            "within_1_acuity_level_accuracy": (
                (best.get("ordinal_metrics") or {}).get(
                    "within_1_acuity_level_accuracy"
                )
            ),
            "accuracy": best["accuracy"],
            "macro_f1": best["macro_f1"],
            "weighted_f1": best["weighted_f1"],
        },
        "split_kind": comparison.get("split_kind"),
        "patient_level_split": True,
        "patient_overlap_train_test": comparison.get("patient_overlap_train_test"),
        "test_set_used_for_model_selection": False,
        "final_test_evaluation_once": True,
        "preprocessing_inside_pipeline": True,
        "leakage_audit_passed": True,
        "synthetic_audit_passed": True,
        "n_train": comparison.get("n_train"),
        "n_val": comparison.get("n_val"),
        "n_test": comparison.get("n_test"),
        "intended_use": "Research decision-support only. Clinician review required "
                        "on every output. Not clinically validated. UHL validation "
                        "pending governance approval.",
        "excluded_leakage_features": ["acuity", "disposition", "outtime", "hadm_id",
                                      "subject_id", "stay_id", "diagnoses", "medrecon", "pyxis",
                                      "vitals_timeseries"],
        "generated": date.today().isoformat(),
        "training_run_id": comparison.get("training_run_id"),
        "feature_schema_hash": comparison.get("feature_schema_hash"),
        "model_artifact_sha256": comparison.get("model_artifact_sha256"),
    }
    open(path, "w").write(json.dumps(card, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Full-MIMIC safety-first model comparison.")
    parser.add_argument("--quick-test", action="store_true",
                        help="Shrink estimators so tests/dev runs finish fast.")
    parser.add_argument(
        "--candidates",
        default=None,
        help=(
            "Candidate set: 'basic' (logistic_regression,random_forest), 'all', "
            "or a comma-list. Defaults to basic in --quick-test, all otherwise. "
            "Can also be set with MIMIC_COMPARE_CANDIDATES."
        ),
    )
    args = parser.parse_args(argv)
    if args.quick_test:
        os.environ["MIMIC_COMPARE_QUICK"] = "1"
    try:
        candidate_names, candidate_mode = _candidate_names(args.candidates)
    except ValueError as exc:
        sys.stderr.write(f"REFUSED: {exc}\n")
        return 2

    from ml_training.full_mimic._safety import (
        require_safe_environment, assert_no_raw_rows, UnsafeEnvironmentError,
    )
    try:
        paths = require_safe_environment()
    except UnsafeEnvironmentError as e:
        sys.stderr.write(f"REFUSED: {e}\n")
        return 2

    from app.config import settings
    settings.mimic_full_ed_dir = paths["ed_dir"]
    from app.data_pipeline.mimic_full_loader import load_mimic_full_cases
    from ml_training.feature_engineering import (
        build_feature_frame_with_meta,
        validate_feature_schema,
    )
    from ml_training.full_mimic.evaluation import (
        patient_grouped_split, temporal_split, assert_no_subject_overlap,
        over_triage_specificity, auroc_pr_auc, bootstrap_ci, subgroup_metrics,
        passes_over_triage_constraint,
    )

    import numpy as np
    import joblib
    import sklearn

    cases = load_mimic_full_cases()
    complaint_texts = _chief_complaints_from_cases(cases)
    X, y, feature_names, subject_ids, intimes = build_feature_frame_with_meta(cases)
    training_run_id = str(uuid4())
    validate_feature_schema(list(feature_names))
    feature_schema_hash = _feature_schema_hash(feature_names)
    mask = [v is not None for v in y]
    X2 = X[mask]
    y2 = np.array([v for v, m in zip(y, mask) if m])
    text2 = np.array([v for v, m in zip(complaint_texts, mask) if m], dtype=object)
    subj2 = [s for s, m in zip(subject_ids, mask) if m]
    intimes2 = [t for t, m in zip(intimes, mask) if m]
    labels = sorted(set(y2.tolist()))

    # THREE-way split. Prefer a temporal split (train past / test future) when
    # intime is available; otherwise a PATIENT-GROUPED split so a patient's repeat
    # visits never span train/val/test. Never a random row-level split.
    split = temporal_split(intimes2, subj2)
    split_kind = "temporal"
    if split is None:
        split = patient_grouped_split(subj2)
        split_kind = "patient_grouped"
    tr_idx, va_idx, te_idx = split
    # Hard guarantee: no subject_id appears in more than one split.
    assert_no_subject_overlap(subj2, tr_idx, va_idx, te_idx)
    patient_overlap_count = _patient_overlap_count(subj2, tr_idx, va_idx, te_idx)

    Xtr, ytr = X2[tr_idx], y2[tr_idx]
    Xva, yva = X2[va_idx], y2[va_idx]
    Xte, yte = X2[te_idx], y2[te_idx]
    sex_idx = feature_names.index("sex_male")

    # Evaluate every candidate on the VALIDATION set (selection set). The untouched
    # TEST set is scored once, later, only for the selected model.
    results, fitted = [], {}
    for name, est in _candidates(candidate_names):
        try:
            m = evaluate_candidate(name, est, Xtr, Xva, ytr, yva, labels)
            # over-triage / specificity view on validation
            va_pred = np.asarray(est.predict(Xva)).ravel()
            ot = over_triage_specificity(yva, va_pred)
            m["over_triage_specificity"] = ot
            m["passes_over_triage_constraint"] = passes_over_triage_constraint(ot)
            results.append(m)
            fitted[name] = est
            print(f"  {name}: high_acuity_recall="
                  f"{m['high_acuity_recall'].get('recall')} "
                  f"severe_under_triage={m['under_over_triage'].get('severe_under_triage_rate')} "
                  f"under_triage={m['under_over_triage'].get('under_triage_rate')} "
                  f"specificity={ot.get('specificity')} "
                  f"urgent_rate={ot.get('predicted_urgent_rate')} "
                  f"macro_f1={m['macro_f1']:.3f} "
                  f"constraint={'PASS' if m['passes_over_triage_constraint'] else 'FAIL'}")
        except Exception as exc:
            print(f"  {name}: SKIPPED ({type(exc).__name__}: {exc})")

    experimental_results = []
    if not _quick():
        Xtr_text, Xva_text = text2[tr_idx].tolist(), text2[va_idx].tolist()
        Xtr_combined = _combined_frame(Xtr, Xtr_text, feature_names)
        Xva_combined = _combined_frame(Xva, Xva_text, feature_names)
        for name, kind, est in _experimental_text_candidates(feature_names):
            try:
                if kind == "text":
                    m = evaluate_candidate(name, est, Xtr_text, Xva_text, ytr, yva, labels)
                else:
                    m = evaluate_candidate(
                        name, est, Xtr_combined, Xva_combined, ytr, yva, labels)
                m["experimental"] = True
                m["deployment_eligible"] = False
                m["not_selected_reason"] = (
                    "TF-IDF/text pipeline baseline only. The current serving "
                    "artefact contract is structured FEATURE_NAMES, so this "
                    "candidate is reported but never selected for clinical-facing "
                    "runtime output."
                )
                experimental_results.append(m)
                print(f"  {name}: EXPERIMENTAL non-serving baseline evaluated")
            except Exception as exc:
                experimental_results.append({
                    "model_name": name,
                    "experimental": True,
                    "deployment_eligible": False,
                    "status": "SKIPPED",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                print(f"  {name}: EXPERIMENTAL SKIPPED ({type(exc).__name__}: {exc})")

    if not results:
        sys.stderr.write("No candidate models could be trained.\n")
        return 1

    # SELECTION on validation: only candidates that pass the over-triage constraint
    # are eligible (blocks 'predict everything urgent'). If none pass, fail rather
    # than silently shipping a degenerate model.
    eligible = [m for m in results if m.get("passes_over_triage_constraint")]
    if not eligible:
        sys.stderr.write(
            "No candidate passed the over-triage/specificity constraint on "
            "validation (all were too non-specific / 'predict everything urgent'). "
            "Refusing to select a model.\n")
        results_sorted = sorted(results, key=_safety_score, reverse=True)
        comparison = {
            "generated": date.today().isoformat(),
            "training_run_id": training_run_id,
            "feature_schema_hash": feature_schema_hash,
            "dataset_source": "MIMIC-IV-ED-Full-v2.2",
            "synthetic_data_used": False,
            "demo_fixture_used": False,
            "test_fixture_used": False,
            "status": "NO_MODEL_SELECTED",
            "selection_criterion": (
                "safety-first on VALIDATION among candidates passing the over-triage/"
                "specificity constraint; rank by high-acuity recall, severe "
                "under-triage, under-triage, then F1/accuracy. No model is "
                "selected when all candidates fail."
            ),
            "split_kind": split_kind,
            "patient_level_split": True,
            "patient_overlap_train_test": int(patient_overlap_count),
            "test_set_used_for_model_selection": False,
            "final_test_evaluation_once": True,
            "preprocessing_inside_pipeline": True,
            "leakage_audit_passed": True,
            "synthetic_audit_passed": True,
            "over_triage_constraint_failed": True,
            "quick_test_mode": _quick(),
            "candidate_mode": candidate_mode,
            "candidate_names_requested": candidate_names,
            "n_train": int(len(ytr)), "n_val": int(len(yva)), "n_test": int(len(yte)),
            "untouched_test_metrics": None,
            "labels": [str(l) for l in labels],
            "candidates": results_sorted,
            "experimental_non_serving_candidates": experimental_results,
            "experimental_non_serving_note": (
                "TF-IDF text and structured+text baselines are evaluated for "
                "research comparison only and are not eligible for serving."
            ),
            "selected_model": None,
            "selection_rationale": (
                "No candidate passed the over-triage/specificity constraint on "
                "the validation split. No deployable artefact or model card was "
                "written."
            ),
            "sklearn_version": sklearn.__version__,
            "note": "Aggregate research metrics only. Not clinically validated.",
        }
        assert_no_raw_rows(comparison)
        out = paths["output_dir"]
        (out / "full_mimic_model_comparison.json").write_text(json.dumps(comparison, indent=2))
        _write_csv(out / "full_mimic_model_comparison.csv", results_sorted, labels)
        _write_feature_schema(out / "mimic_full_feature_schema.json", feature_names, feature_schema_hash)
        provenance = _training_provenance(
            training_run_id=training_run_id,
            split_kind=split_kind,
            feature_schema_hash=feature_schema_hash,
            feature_names=feature_names,
            n_train=len(ytr),
            n_val=len(yva),
            n_test=len(yte),
            patient_overlap_count=patient_overlap_count,
            candidate_mode=candidate_mode,
            candidate_names=candidate_names,
            quick_test=_quick(),
        )
        (out / "mimic_full_training_provenance.json").write_text(json.dumps(provenance, indent=2))
        _write_dataset_card(out / "mimic_full_dataset_card.json", comparison)
        return 1

    results_sorted = sorted(results, key=_safety_score, reverse=True)
    eligible_sorted = sorted(eligible, key=_safety_score, reverse=True)
    constraint_failed = False
    best = eligible_sorted[0]
    best_name = best["model_name"]

    # FINAL: score the selected model ONCE on the untouched test set.
    best_est = fitted[best_name]
    test_metrics = evaluate_candidate(best_name, best_est, Xtr, Xte, ytr, yte, labels)
    te_pred = np.asarray(best_est.predict(Xte)).ravel()
    test_ot = over_triage_specificity(yte, te_pred)
    try:
        proba_te = np.asarray(best_est.predict_proba(Xte))
        high_cols = [i for i, c in enumerate(sorted(set(ytr.tolist()))) if c <= 2]
        proba_high = proba_te[:, high_cols].sum(axis=1) if high_cols else np.zeros(len(yte))
        test_auc = auroc_pr_auc(yte, proba_high)
    except Exception:
        test_auc = {"auroc": None, "pr_auc": None}
    from sklearn.metrics import accuracy_score as _acc
    test_acc_ci = bootstrap_ci(yte, te_pred, lambda a, b: _acc(a, b))
    test_subgroups = subgroup_metrics(
        yte, te_pred, ["M" if v == 1.0 else "F" for v in Xte[:, sex_idx]])

    runner_up = eligible_sorted[1]["model_name"] if len(eligible_sorted) > 1 else None
    rationale = (
        f"Selected '{best_name}' on the VALIDATION set by safety-first ranking "
        f"among candidates that PASSED the over-triage/specificity constraint "
        f"(specificity >= 0.10 and predicted-urgent-rate <= 0.95, blocking a "
        f"'predict everything urgent' model). Ranking: highest high-acuity recall, "
        f"then lowest severe under-triage, then lowest under-triage, then macro F1 "
        f"and accuracy. "
        + (f"Runner-up: '{runner_up}'. " if runner_up else "")
        + (f"Split: {split_kind}. " )
        + ("WARNING: no candidate passed the constraint; selection is provisional "
           "and must not be used. " if constraint_failed else "")
        + "Final performance below is reported ONCE on the untouched test set."
    )

    comparison = {
        "generated": date.today().isoformat(),
        "training_run_id": training_run_id,
        "feature_schema_hash": feature_schema_hash,
        "model_artifact_sha256": None,
        "dataset_source": "MIMIC-IV-ED-Full-v2.2",
        "synthetic_data_used": False,
        "demo_fixture_used": False,
        "test_fixture_used": False,
        "selection_criterion": (
            "safety-first on VALIDATION among candidates passing the over-triage/"
            "specificity constraint; rank by high-acuity recall, severe "
            "under-triage, under-triage, then F1/accuracy. Final reported once "
            "on untouched TEST. "
            "Patient-grouped or temporal split (never random row-level)."
        ),
        "split_kind": split_kind,
        "patient_level_split": True,
        "patient_overlap_train_test": int(patient_overlap_count),
        "test_set_used_for_model_selection": False,
        "final_test_evaluation_once": True,
        "preprocessing_inside_pipeline": True,
        "leakage_audit_passed": True,
        "synthetic_audit_passed": True,
        "over_triage_constraint_failed": constraint_failed,
        "quick_test_mode": _quick(),
        "candidate_mode": candidate_mode,
        "candidate_names_requested": candidate_names,
        "n_train": int(len(ytr)), "n_val": int(len(yva)), "n_test": int(len(yte)),
        "untouched_test_metrics": {
            "model": best_name,
            "accuracy": test_metrics["accuracy"],
            "accuracy_95ci": test_acc_ci,
            "macro_f1": test_metrics["macro_f1"],
            "weighted_f1": test_metrics["weighted_f1"],
            "high_acuity_recall": test_metrics["high_acuity_recall"],
            "under_over_triage": test_metrics["under_over_triage"],
            "ordinal_metrics": test_metrics["ordinal_metrics"],
            "over_triage_specificity": test_ot,
            "auroc_pr_auc": test_auc,
            "subgroups_by_sex": test_subgroups,
            "confusion_matrix": test_metrics["confusion_matrix"],
            "confusion_matrix_labels": test_metrics["confusion_matrix_labels"],
        },
        "labels": [str(l) for l in labels],
        "candidates": results_sorted,
        "experimental_non_serving_candidates": experimental_results,
        "experimental_non_serving_note": (
            "TF-IDF text and structured+text baselines are evaluated for research "
            "comparison only and are not eligible for serving under the current "
            "structured FEATURE_NAMES runtime contract."
        ),
        "selected_model": best_name,
        "selection_rationale": rationale,
        "sklearn_version": sklearn.__version__,
        "note": "Aggregate research metrics only. Not clinically validated.",
    }
    assert_no_raw_rows(comparison)

    out = paths["output_dir"]
    artefact = out / "mimic_full_acuity_selected.joblib"
    joblib.dump({"model": fitted[best_name], "feature_names": list(feature_names),
                 "sklearn_version": sklearn.__version__,
                 "selected_by": "triage_safety_metrics",
                 "model_kind": best_name,
                 "training_run_id": training_run_id,
                 "feature_schema_hash": feature_schema_hash}, artefact)
    comparison["model_artifact_sha256"] = hashlib.sha256(artefact.read_bytes()).hexdigest()
    provenance = _training_provenance(
        training_run_id=training_run_id,
        split_kind=split_kind,
        feature_schema_hash=feature_schema_hash,
        feature_names=feature_names,
        n_train=len(ytr),
        n_val=len(yva),
        n_test=len(yte),
        patient_overlap_count=patient_overlap_count,
        candidate_mode=candidate_mode,
        candidate_names=candidate_names,
        quick_test=_quick(),
        selected_model=best_name,
        model_artifact_sha256=comparison["model_artifact_sha256"],
    )

    (out / "full_mimic_model_comparison.json").write_text(json.dumps(comparison, indent=2))
    _write_csv(out / "full_mimic_model_comparison.csv", results_sorted, labels)
    _write_feature_schema(out / "mimic_full_feature_schema.json", feature_names, feature_schema_hash)
    (out / "mimic_full_training_provenance.json").write_text(json.dumps(provenance, indent=2))
    _write_dataset_card(out / "mimic_full_dataset_card.json", comparison)
    (out / "mimic_full_model_sha256.txt").write_text(comparison["model_artifact_sha256"] + "\n")
    (out / "full_mimic_confusion_matrix.json").write_text(json.dumps({
        "labels": test_metrics["confusion_matrix_labels"],
        "confusion_matrix": test_metrics["confusion_matrix"],
        "training_run_id": training_run_id,
        "synthetic_data_used": False,
        "demo_fixture_used": False,
        "test_fixture_used": False,
    }, indent=2))
    (out / "full_mimic_calibration_report.json").write_text(json.dumps({
        "calibration": test_metrics["calibration"],
        "training_run_id": training_run_id,
        "synthetic_data_used": False,
        "demo_fixture_used": False,
        "test_fixture_used": False,
    }, indent=2))
    (out / "full_mimic_under_over_triage_report.json").write_text(json.dumps({
        "under_over_triage": test_metrics["under_over_triage"],
        "ordinal_metrics": test_metrics["ordinal_metrics"],
        "over_triage_specificity": test_ot,
        "training_run_id": training_run_id,
        "synthetic_data_used": False,
        "demo_fixture_used": False,
        "test_fixture_used": False,
    }, indent=2))
    (out / "full_mimic_subgroup_metrics.json").write_text(json.dumps({
        "subgroups_by_sex": test_subgroups,
        "training_run_id": training_run_id,
        "synthetic_data_used": False,
        "demo_fixture_used": False,
        "test_fixture_used": False,
    }, indent=2))
    _write_model_card(out / "mimic_full_model_card.json", best, comparison, labels)

    print(f"\n{rationale}")
    print(f"JSON:  {out/'full_mimic_model_comparison.json'}")
    print(f"CSV:   {out/'full_mimic_model_comparison.csv'}")
    print(f"Card:  {out/'mimic_full_model_card.json'}")
    print(f"Model: {artefact}")
    print("Point MIMIC_FULL_MODEL_PATH at the artefact to serve it (after review).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
