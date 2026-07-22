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
soft-voting ensemble, and (when installed) XGBoost, LightGBM, CatBoost plus
explicit GPU variants for H100-class training machines.
Serving-eligible v2 raw-triage-dataframe candidates can use chiefcomplaint
TF-IDF text features while preserving train/serve parity. Legacy experimental
TF-IDF baselines are still reported separately in full runs. A missing optional
library is skipped, never fatal.

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
    "logistic_regression_unweighted",
    "logistic_regression", "random_forest", "extra_trees",
    "gradient_boosting", "hist_gradient_boosting", "calibrated_linear_svm",
    "soft_voting_ensemble", "xgboost", "lightgbm", "catboost",
    "xgboost_gpu", "lightgbm_gpu", "catboost_gpu",
    "structured_logistic_smote",
    "structured_xgboost_smote",
    "raw_tfidf_word_char_logistic",
    "raw_tfidf_word_char_linear_svm",
    "raw_tfidf_word_char_sgd_logistic",
    "raw_tfidf_svd_xgboost",
    "raw_tfidf_svd_lightgbm",
    "raw_tfidf_svd_catboost",
    "raw_tfidf_svd_xgboost_smote",
    "raw_tfidf_svd_lightgbm_smote",
    "raw_tfidf_svd_catboost_smote",
)
_BASIC_CANDIDATES = ("logistic_regression", "random_forest")
_RAW_TRIAGE_CANDIDATES = (
    "raw_tfidf_word_char_logistic",
    "raw_tfidf_word_char_linear_svm",
    "raw_tfidf_word_char_sgd_logistic",
    "raw_tfidf_svd_xgboost",
    "raw_tfidf_svd_lightgbm",
    "raw_tfidf_svd_catboost",
    "raw_tfidf_svd_xgboost_smote",
    "raw_tfidf_svd_lightgbm_smote",
    "raw_tfidf_svd_catboost_smote",
)
_SMOTE_CANDIDATES = (
    "structured_logistic_smote",
    "structured_xgboost_smote",
    "raw_tfidf_svd_xgboost_smote",
    "raw_tfidf_svd_lightgbm_smote",
    "raw_tfidf_svd_catboost_smote",
)
_BALANCED_CLASS_WEIGHT_CANDIDATES = {
    "logistic_regression",
    "random_forest",
    "extra_trees",
    "calibrated_linear_svm",
    "soft_voting_ensemble",
    "lightgbm",
    "lightgbm_gpu",
    "catboost_gpu",
    "raw_tfidf_word_char_logistic",
    "raw_tfidf_word_char_linear_svm",
    "raw_tfidf_word_char_sgd_logistic",
    "raw_tfidf_svd_lightgbm",
    "raw_tfidf_svd_catboost",
}
_BALANCED_SAMPLE_WEIGHT_CANDIDATES = {
    "xgboost",
    "xgboost_gpu",
    "raw_tfidf_svd_xgboost",
}


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
    n_est = 15 if _quick() else int(os.environ.get("MIMIC_COMPARE_N_ESTIMATORS", "1200"))
    gpu_n_est = 15 if _quick() else int(os.environ.get("MIMIC_COMPARE_GPU_N_ESTIMATORS", "2000"))
    iters = 15 if _quick() else int(os.environ.get("MIMIC_COMPARE_CATBOOST_ITERATIONS", str(n_est)))
    gpu_iters = 15 if _quick() else int(os.environ.get("MIMIC_COMPARE_GPU_ITERATIONS", str(gpu_n_est)))
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

    def _smote_pipeline(estimator, *, scale: bool = True):
        from imblearn.over_sampling import SMOTE
        from imblearn.pipeline import Pipeline as ImbPipeline

        k_neighbors = int(os.environ.get(
            "MIMIC_SMOTE_K_NEIGHBORS",
            "1" if _quick() else "5",
        ))
        steps = []
        if scale:
            steps.append(("scaler", StandardScaler()))
        else:
            steps.append(("identity", FunctionTransformer(validate=False)))
        steps.append(("sampler", SMOTE(random_state=42, k_neighbors=k_neighbors)))
        steps.append(("estimator", estimator))
        return ImbPipeline(steps)

    if "logistic_regression_unweighted" in requested:
        cands.append(("logistic_regression_unweighted",
                      _pipeline(
                          LogisticRegression(max_iter=500 if _quick() else 1000,
                                             class_weight=None, n_jobs=-1),
                          scale=True,
                      )))
    if "logistic_regression" in requested:
        cands.append(("logistic_regression",
                      _pipeline(
                          LogisticRegression(max_iter=500 if _quick() else 1000,
                                             class_weight="balanced", n_jobs=-1),
                          scale=True,
                      )))
    if "random_forest" in requested:
        cands.append(("random_forest",
                      _pipeline(RandomForestClassifier(
                          n_estimators=n_est, class_weight="balanced",
                          random_state=42, n_jobs=-1))))
    if "extra_trees" in requested:
        cands.append(("extra_trees",
                      _pipeline(ExtraTreesClassifier(
                          n_estimators=n_est, class_weight="balanced",
                          random_state=42, n_jobs=-1))))
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
    if "structured_logistic_smote" in requested:
        try:
            cands.append(("structured_logistic_smote",
                          _smote_pipeline(
                              LogisticRegression(
                                  max_iter=500 if _quick() else 1000,
                                  class_weight=None,
                                  n_jobs=-1,
                              ),
                              scale=True,
                          )))
        except Exception:
            pass
    if "structured_xgboost_smote" in requested:
        try:
            from xgboost import XGBClassifier
            from ml_training.full_mimic.label_remap import LabelRemapClassifier
            cands.append(("structured_xgboost_smote",
                          _smote_pipeline(
                              LabelRemapClassifier(XGBClassifier(
                                  n_estimators=n_est,
                                  random_state=42,
                                  eval_metric="mlogloss",
                                  tree_method="hist",
                                  n_jobs=-1,
                              )),
                              scale=True,
                          )))
        except Exception:
            pass
    if "soft_voting_ensemble" in requested:
        lr = _pipeline(
            LogisticRegression(
                max_iter=500 if _quick() else 1000,
                class_weight="balanced",
                n_jobs=-1,
            ),
            scale=True,
        )
        rf = _pipeline(RandomForestClassifier(
            n_estimators=n_est, class_weight="balanced", random_state=43, n_jobs=-1))
        et = _pipeline(ExtraTreesClassifier(
            n_estimators=n_est, class_weight="balanced", random_state=44, n_jobs=-1))
        cands.append(("soft_voting_ensemble",
                      VotingClassifier(
                          estimators=[("lr", lr), ("rf", rf), ("et", et)],
                          voting="soft",
                          n_jobs=-1,
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
                                  tree_method="hist", n_jobs=-1))
                          )))
        except Exception:
            pass
    if "lightgbm" in requested:
        try:
            from lightgbm import LGBMClassifier
            cands.append(("lightgbm",
                          _pipeline(LGBMClassifier(
                              n_estimators=n_est, class_weight="balanced",
                              random_state=42, verbose=-1, n_jobs=-1))))
        except Exception:
            pass
    if "catboost" in requested:
        try:
            from catboost import CatBoostClassifier
            cands.append(("catboost",
                          _pipeline(CatBoostClassifier(
                              iterations=iters, random_seed=42,
                              verbose=0, thread_count=-1))))
        except Exception:
            pass
    if "xgboost_gpu" in requested:
        try:
            from xgboost import XGBClassifier
            from ml_training.full_mimic.label_remap import LabelRemapClassifier
            cands.append(("xgboost_gpu",
                          LabelRemapClassifier(
                              _pipeline(XGBClassifier(
                                  objective="multi:softprob",
                                  n_estimators=gpu_n_est,
                                  max_depth=int(os.environ.get("MIMIC_XGB_GPU_MAX_DEPTH", "4")),
                                  learning_rate=float(os.environ.get("MIMIC_XGB_GPU_LR", "0.03")),
                                  subsample=0.85,
                                  colsample_bytree=0.85,
                                  reg_lambda=3.0,
                                  random_state=42,
                                  eval_metric="mlogloss",
                                  tree_method="hist",
                                  device=os.environ.get("MIMIC_XGB_DEVICE", "cuda"),
                                  n_jobs=1))
                          )))
        except Exception:
            pass
    if "lightgbm_gpu" in requested:
        try:
            from lightgbm import LGBMClassifier
            cands.append(("lightgbm_gpu",
                          _pipeline(LGBMClassifier(
                              objective="multiclass",
                              n_estimators=gpu_n_est,
                              num_leaves=int(os.environ.get("MIMIC_LGBM_GPU_NUM_LEAVES", "63")),
                              learning_rate=float(os.environ.get("MIMIC_LGBM_GPU_LR", "0.03")),
                              class_weight="balanced",
                              random_state=42,
                              verbose=-1,
                              device_type=os.environ.get("MIMIC_LGBM_DEVICE_TYPE", "cuda"),
                              gpu_device_id=int(os.environ.get("MIMIC_LGBM_GPU_DEVICE_ID", "-1")),
                              n_jobs=1))))
        except Exception:
            pass
    if "catboost_gpu" in requested:
        try:
            from catboost import CatBoostClassifier
            cands.append(("catboost_gpu",
                          _pipeline(CatBoostClassifier(
                              loss_function="MultiClass",
                              task_type="GPU",
                              devices=os.environ.get("MIMIC_CATBOOST_GPU_DEVICES", "0:1"),
                              iterations=gpu_iters,
                              learning_rate=float(os.environ.get("MIMIC_CATBOOST_GPU_LR", "0.03")),
                              depth=int(os.environ.get("MIMIC_CATBOOST_GPU_DEPTH", "6")),
                              l2_leaf_reg=float(os.environ.get("MIMIC_CATBOOST_GPU_L2", "5")),
                              auto_class_weights="Balanced",
                              eval_metric="MultiClass",
                              random_seed=42,
                              verbose=100 if not _quick() else 0))))
        except Exception:
            pass
    return cands


def _raw_triage_candidates(names: list[str] | None = None):
    """Return [(name, estimator), ...] for v2 raw-dataframe artefacts."""
    names = list(names or _candidate_names()[0])
    requested = set(names)
    cands = []
    from ml_training.full_mimic.raw_triage_pipeline import (
        make_raw_tfidf_linear_svm_pipeline,
        make_raw_tfidf_logistic_pipeline,
        make_raw_tfidf_sgd_logistic_pipeline,
        make_raw_tfidf_svd_pipeline,
        make_raw_tfidf_svd_smote_pipeline,
    )

    min_df = 1 if _quick() else int(os.environ.get("MIMIC_TFIDF_MIN_DF", "3"))
    word_max = (
        1000 if _quick()
        else int(os.environ.get("MIMIC_TFIDF_WORD_MAX_FEATURES", "50000"))
    )
    char_max = (
        1000 if _quick()
        else int(os.environ.get("MIMIC_TFIDF_CHAR_MAX_FEATURES", "50000"))
    )
    max_iter = int(os.environ.get(
        "MIMIC_TFIDF_LOGISTIC_MAX_ITER",
        "500" if _quick() else "1000",
    ))
    svm_max_iter = int(os.environ.get(
        "MIMIC_TFIDF_SVM_MAX_ITER",
        "1000" if _quick() else "5000",
    ))
    svd_components = int(os.environ.get(
        "MIMIC_TFIDF_SVD_COMPONENTS",
        "16" if _quick() else "512",
    ))
    n_est = 15 if _quick() else int(os.environ.get("MIMIC_COMPARE_N_ESTIMATORS", "1200"))

    def _smote_sampler():
        from imblearn.over_sampling import SMOTE

        return SMOTE(
            random_state=42,
            k_neighbors=int(os.environ.get(
                "MIMIC_SMOTE_K_NEIGHBORS",
                "1" if _quick() else "5",
            )),
        )

    if "raw_tfidf_word_char_logistic" in requested:
        cands.append((
            "raw_tfidf_word_char_logistic",
            make_raw_tfidf_logistic_pipeline(
                min_df=min_df,
                word_max_features=word_max,
                char_max_features=char_max,
                max_iter=max_iter,
                solver=os.environ.get(
                    "MIMIC_TFIDF_LOGISTIC_SOLVER",
                    "saga",
                ),
                tol=float(os.environ.get(
                    "MIMIC_TFIDF_LOGISTIC_TOL",
                    "0.01" if _quick() else "0.0001",
                )),
            ),
        ))
    if "raw_tfidf_word_char_linear_svm" in requested:
        cands.append((
            "raw_tfidf_word_char_linear_svm",
            make_raw_tfidf_linear_svm_pipeline(
                min_df=min_df,
                word_max_features=word_max,
                char_max_features=char_max,
                max_iter=svm_max_iter,
                cv=2 if _quick() else int(os.environ.get("MIMIC_TFIDF_SVM_CV", "3")),
            ),
        ))
    if "raw_tfidf_word_char_sgd_logistic" in requested:
        cands.append((
            "raw_tfidf_word_char_sgd_logistic",
            make_raw_tfidf_sgd_logistic_pipeline(
                min_df=min_df,
                word_max_features=word_max,
                char_max_features=char_max,
                max_iter=max_iter,
                tol=float(os.environ.get(
                    "MIMIC_TFIDF_SGD_TOL",
                    "0.01" if _quick() else "0.0001",
                )),
            ),
        ))
    if "raw_tfidf_svd_xgboost" in requested:
        try:
            from xgboost import XGBClassifier
            from ml_training.full_mimic.label_remap import LabelRemapClassifier
            cands.append((
                "raw_tfidf_svd_xgboost",
                make_raw_tfidf_svd_pipeline(
                    LabelRemapClassifier(XGBClassifier(
                        n_estimators=n_est,
                        random_state=42,
                        eval_metric="mlogloss",
                        tree_method="hist",
                        n_jobs=-1,
                    )),
                    min_df=min_df,
                    word_max_features=word_max,
                    char_max_features=char_max,
                    n_components=svd_components,
                ),
            ))
        except Exception:
            pass
    if "raw_tfidf_svd_lightgbm" in requested:
        try:
            from lightgbm import LGBMClassifier
            cands.append((
                "raw_tfidf_svd_lightgbm",
                make_raw_tfidf_svd_pipeline(
                    LGBMClassifier(
                        n_estimators=n_est,
                        class_weight="balanced",
                        random_state=42,
                        verbose=-1,
                        n_jobs=-1,
                    ),
                    min_df=min_df,
                    word_max_features=word_max,
                    char_max_features=char_max,
                    n_components=svd_components,
                ),
            ))
        except Exception:
            pass
    if "raw_tfidf_svd_catboost" in requested:
        try:
            from catboost import CatBoostClassifier
            cands.append((
                "raw_tfidf_svd_catboost",
                make_raw_tfidf_svd_pipeline(
                    CatBoostClassifier(
                        loss_function="MultiClass",
                        iterations=15 if _quick() else int(
                            os.environ.get("MIMIC_COMPARE_CATBOOST_ITERATIONS", str(n_est))
                        ),
                        auto_class_weights="Balanced",
                        random_seed=42,
                        verbose=0,
                        thread_count=-1,
                    ),
                    min_df=min_df,
                    word_max_features=word_max,
                    char_max_features=char_max,
                    n_components=svd_components,
                ),
            ))
        except Exception:
            pass
    if "raw_tfidf_svd_xgboost_smote" in requested:
        try:
            from xgboost import XGBClassifier
            from ml_training.full_mimic.label_remap import LabelRemapClassifier
            cands.append((
                "raw_tfidf_svd_xgboost_smote",
                make_raw_tfidf_svd_smote_pipeline(
                    LabelRemapClassifier(XGBClassifier(
                        n_estimators=n_est,
                        random_state=42,
                        eval_metric="mlogloss",
                        tree_method="hist",
                        n_jobs=-1,
                    )),
                    _smote_sampler(),
                    min_df=min_df,
                    word_max_features=word_max,
                    char_max_features=char_max,
                    n_components=svd_components,
                ),
            ))
        except Exception:
            pass
    if "raw_tfidf_svd_lightgbm_smote" in requested:
        try:
            from lightgbm import LGBMClassifier
            cands.append((
                "raw_tfidf_svd_lightgbm_smote",
                make_raw_tfidf_svd_smote_pipeline(
                    LGBMClassifier(
                        n_estimators=n_est,
                        class_weight=None,
                        random_state=42,
                        verbose=-1,
                        n_jobs=-1,
                    ),
                    _smote_sampler(),
                    min_df=min_df,
                    word_max_features=word_max,
                    char_max_features=char_max,
                    n_components=svd_components,
                ),
            ))
        except Exception:
            pass
    if "raw_tfidf_svd_catboost_smote" in requested:
        try:
            from catboost import CatBoostClassifier
            cands.append((
                "raw_tfidf_svd_catboost_smote",
                make_raw_tfidf_svd_smote_pipeline(
                    CatBoostClassifier(
                        loss_function="MultiClass",
                        iterations=15 if _quick() else int(
                            os.environ.get("MIMIC_COMPARE_CATBOOST_ITERATIONS", str(n_est))
                        ),
                        random_seed=42,
                        verbose=0,
                        thread_count=-1,
                    ),
                    _smote_sampler(),
                    min_df=min_df,
                    word_max_features=word_max,
                    char_max_features=char_max,
                    n_components=svd_components,
                ),
            ))
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


def _selection_constraints(
    *,
    profile: str | None = None,
    min_specificity: float | None = None,
    max_predicted_urgent_rate: float | None = None,
    min_high_acuity_recall: float | None = None,
) -> dict:
    """Resolve validation-selection constraints.

    balanced_safety is intentionally stricter than the original recall-max rule:
    it prevents a threshold like 0.05 from winning by marking almost everyone
    urgent. recall_max is retained for explicit sensitivity comparisons.
    """
    profile = (profile or os.environ.get("MIMIC_SELECTION_PROFILE") or
               "balanced_safety").strip().lower()
    if profile not in {"balanced_safety", "recall_max"}:
        raise ValueError(
            "selection profile must be 'balanced_safety' or 'recall_max'"
        )
    defaults = {
        "balanced_safety": {
            "min_specificity": 0.20,
            "max_predicted_urgent_rate": 0.85,
            "min_high_acuity_recall": 0.80,
        },
        "recall_max": {
            "min_specificity": 0.10,
            "max_predicted_urgent_rate": 0.95,
            "min_high_acuity_recall": None,
        },
    }[profile]

    def _env_float(name: str, default: float | None) -> float | None:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        value = float(raw)
        if value < 0.0 or value > 1.0:
            raise ValueError(f"{name} must be between 0 and 1")
        return value

    resolved = {
        "profile": profile,
        "min_specificity": (
            defaults["min_specificity"] if min_specificity is None else min_specificity
        ),
        "max_predicted_urgent_rate": (
            defaults["max_predicted_urgent_rate"]
            if max_predicted_urgent_rate is None else max_predicted_urgent_rate
        ),
        "min_high_acuity_recall": (
            defaults["min_high_acuity_recall"]
            if min_high_acuity_recall is None else min_high_acuity_recall
        ),
    }
    resolved["min_specificity"] = _env_float(
        "MIMIC_MIN_SPECIFICITY", resolved["min_specificity"])
    resolved["max_predicted_urgent_rate"] = _env_float(
        "MIMIC_MAX_PREDICTED_URGENT_RATE",
        resolved["max_predicted_urgent_rate"],
    )
    resolved["min_high_acuity_recall"] = _env_float(
        "MIMIC_MIN_HIGH_ACUITY_RECALL", resolved["min_high_acuity_recall"])
    for key in ("min_specificity", "max_predicted_urgent_rate",
                "min_high_acuity_recall"):
        value = resolved.get(key)
        if value is not None and (value < 0.0 or value > 1.0):
            raise ValueError(f"{key} must be between 0 and 1")
    return resolved


def _passes_selection_constraint(
    metrics: dict,
    over_triage_metrics: dict,
    constraints: dict,
) -> bool:
    spec = (over_triage_metrics or {}).get("specificity")
    urgent = (over_triage_metrics or {}).get("predicted_urgent_rate")
    recall = (metrics.get("high_acuity_recall") or {}).get("recall")
    if spec is None or urgent is None:
        return False
    if spec < float(constraints["min_specificity"]):
        return False
    if urgent > float(constraints["max_predicted_urgent_rate"]):
        return False
    min_recall = constraints.get("min_high_acuity_recall")
    if min_recall is not None and (recall is None or recall < float(min_recall)):
        return False
    return True


def _selection_score(metrics: dict, constraints: dict | None = None) -> tuple:
    """Selection ranking after profile constraints have been applied."""
    constraints = constraints or {"profile": "recall_max"}
    if constraints.get("profile") == "recall_max":
        return _safety_score(metrics)
    har = (metrics.get("high_acuity_recall") or {}).get("recall")
    severe_uot = (metrics.get("under_over_triage") or {}).get(
        "severe_under_triage_rate")
    uot = (metrics.get("under_over_triage") or {}).get("under_triage_rate")
    ot = metrics.get("over_triage_specificity") or {}
    spec = ot.get("specificity")
    urgent = ot.get("predicted_urgent_rate")
    macro_f1 = metrics.get("macro_f1", 0.0)
    weighted_f1 = metrics.get("weighted_f1", 0.0)
    acc = metrics.get("accuracy", 0.0)
    har = -1.0 if har is None else har
    severe_uot = 1.0 if severe_uot is None else severe_uot
    uot = 1.0 if uot is None else uot
    spec = -1.0 if spec is None else spec
    urgent = 1.0 if urgent is None else urgent
    # Once recall clears the configured safety floor, prefer lower dangerous
    # under-triage and better specificity over tiny extra recall gains.
    return (-severe_uot, -uot, spec, -urgent, macro_f1, weighted_f1, acc, har)


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


def _class_distribution(y_values, labels) -> dict:
    import numpy as np

    y_arr = np.asarray(y_values)
    total = int(len(y_arr))
    counts = {
        str(int(label)): int(np.sum(y_arr == label))
        for label in labels
    }
    percentages = {
        label: (count / total if total else None)
        for label, count in counts.items()
    }
    high_count = int(sum(
        count for label, count in counts.items() if int(float(label)) <= 2
    ))
    non_high_count = int(total - high_count)
    count_values = list(counts.values())
    nonzero_counts = [c for c in count_values if c > 0]
    minority = min(nonzero_counts) if nonzero_counts else 0
    majority = max(nonzero_counts) if nonzero_counts else 0
    return {
        "total": total,
        "class_counts": counts,
        "class_percentages": percentages,
        "high_acuity_1_2_count": high_count,
        "high_acuity_1_2_percentage": high_count / total if total else None,
        "non_high_acuity_3_5_count": non_high_count,
        "non_high_acuity_3_5_percentage": non_high_count / total if total else None,
        "minority_class_count": int(minority),
        "majority_class_count": int(majority),
        "minority_to_majority_ratio": (
            float(minority / majority) if majority else None
        ),
    }


def _smote_balanced_distribution(y_values, labels) -> dict:
    base = _class_distribution(y_values, labels)
    counts = base["class_counts"]
    majority = max(counts.values()) if counts else 0
    balanced_counts = {label: int(majority) for label in counts}
    total = int(sum(balanced_counts.values()))
    return {
        **base,
        "total": total,
        "class_counts": balanced_counts,
        "class_percentages": {
            label: (count / total if total else None)
            for label, count in balanced_counts.items()
        },
        "high_acuity_1_2_count": int(sum(
            count for label, count in balanced_counts.items()
            if int(float(label)) <= 2
        )),
        "high_acuity_1_2_percentage": (
            sum(
                count for label, count in balanced_counts.items()
                if int(float(label)) <= 2
            ) / total if total else None
        ),
        "non_high_acuity_3_5_count": int(sum(
            count for label, count in balanced_counts.items()
            if int(float(label)) > 2
        )),
        "non_high_acuity_3_5_percentage": (
            sum(
                count for label, count in balanced_counts.items()
                if int(float(label)) > 2
            ) / total if total else None
        ),
        "minority_class_count": int(majority),
        "majority_class_count": int(majority),
        "minority_to_majority_ratio": 1.0 if majority else None,
        "note": (
            "Expected SMOTE training distribution with default not-majority "
            "sampling strategy. Validation and test distributions are not "
            "resampled."
        ),
    }


def _split_class_distribution_report(
    *,
    training_run_id: str,
    split_kind: str,
    labels,
    y_all,
    y_train,
    y_val,
    y_test,
) -> dict:
    return {
        "training_run_id": training_run_id,
        "dataset_source": "MIMIC-IV-ED-Full-v2.2",
        "split_kind": split_kind,
        "patient_level_split": True,
        "stratified_split": split_kind == "stratified_patient_grouped",
        "balance_before_split": False,
        "validation_and_test_resampled": False,
        "sampling_policy": (
            "Any class balancing or SMOTE is applied inside training pipelines "
            "after the train/validation/test split. Validation and test retain "
            "the natural MIMIC-IV-ED class distribution."
        ),
        "labels": [str(int(label)) for label in labels],
        "overall": _class_distribution(y_all, labels),
        "train": _class_distribution(y_train, labels),
        "validation": _class_distribution(y_val, labels),
        "test": _class_distribution(y_test, labels),
    }


def _write_class_distribution_csv(path, report: dict) -> None:
    labels = report.get("labels") or []
    cols = [
        "split", "acuity", "count", "percentage", "total",
        "high_acuity_1_2_count", "high_acuity_1_2_percentage",
        "non_high_acuity_3_5_count", "non_high_acuity_3_5_percentage",
        "minority_to_majority_ratio",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for split_name in ("overall", "train", "validation", "test"):
            split = report.get(split_name) or {}
            counts = split.get("class_counts") or {}
            percentages = split.get("class_percentages") or {}
            for label in labels:
                writer.writerow([
                    split_name,
                    label,
                    counts.get(label, 0),
                    percentages.get(label),
                    split.get("total"),
                    split.get("high_acuity_1_2_count"),
                    split.get("high_acuity_1_2_percentage"),
                    split.get("non_high_acuity_3_5_count"),
                    split.get("non_high_acuity_3_5_percentage"),
                    split.get("minority_to_majority_ratio"),
                ])


def _candidate_imbalance_metadata(name: str, y_train, y_val, y_test, labels) -> dict:
    train_before = _class_distribution(y_train, labels)
    uses_smote = name in _SMOTE_CANDIDATES or name.endswith("_smote")
    if uses_smote:
        strategy = "smote_training_only_after_split"
        sampler = "SMOTE"
        class_weight_mode = None
        train_after = _smote_balanced_distribution(y_train, labels)
    elif name in _BALANCED_SAMPLE_WEIGHT_CANDIDATES:
        strategy = "balanced_sample_weight"
        sampler = None
        class_weight_mode = "balanced_sample_weight"
        train_after = train_before
    elif name in _BALANCED_CLASS_WEIGHT_CANDIDATES:
        strategy = "class_weight_balanced"
        sampler = None
        class_weight_mode = "balanced"
        train_after = train_before
    else:
        strategy = "none"
        sampler = None
        class_weight_mode = None
        train_after = train_before
    return {
        "imbalance_strategy": strategy,
        "class_weight_mode": class_weight_mode,
        "sampler": sampler,
        "sampler_scope": (
            "training_only_inside_pipeline"
            if uses_smote else None
        ),
        "balance_before_split": False,
        "validation_and_test_resampled": False,
        "train_distribution_before": train_before,
        "train_distribution_after": train_after,
        "validation_distribution": _class_distribution(y_val, labels),
        "test_distribution": _class_distribution(y_test, labels),
    }


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
    if split_kind == "shifted_temporal_patient_grouped":
        split_type = "shifted_date_temporal_patient_level_group_split"
    elif split_kind == "stratified_patient_grouped":
        split_type = "stratified_patient_level_group_split"
    else:
        split_type = "patient_level_group_split"
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
        "class_distribution_report": "full_mimic_class_distribution.json",
        "balance_before_split": False,
        "validation_and_test_resampled": False,
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
        "class_distribution_report": comparison.get("class_distribution_report"),
        "balance_before_split": False,
        "validation_and_test_resampled": False,
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
            n_jobs=-1,
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
            n_jobs=-1,
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


def _model_classes(model, labels):
    classes = getattr(model, "classes_", None)
    if classes is None and hasattr(model, "named_steps"):
        final = model.named_steps.get("estimator")
        classes = getattr(final, "classes_", None)
    if classes is None:
        return [int(l) for l in labels]
    try:
        return [int(c) for c in list(classes)]
    except Exception:
        return [int(l) for l in labels]


def _predict_proba_aligned(model, X, labels):
    import numpy as np

    if not hasattr(model, "predict_proba"):
        return None
    try:
        proba = np.asarray(model.predict_proba(X), dtype=float)
    except Exception:
        return None
    if proba.ndim != 2 or proba.shape[0] == 0:
        return None
    target_labels = [int(l) for l in labels]
    classes = _model_classes(model, labels)
    if len(classes) != proba.shape[1]:
        if proba.shape[1] == len(target_labels):
            classes = target_labels
        else:
            return None
    aligned = np.zeros((proba.shape[0], len(target_labels)), dtype=float)
    label_to_col = {int(label): i for i, label in enumerate(target_labels)}
    for src_i, cls in enumerate(classes):
        dst_i = label_to_col.get(int(cls))
        if dst_i is not None:
            aligned[:, dst_i] = proba[:, src_i]
    return aligned


def _metrics_from_predictions(
    name,
    y_true,
    pred,
    labels,
    *,
    proba_aligned=None,
    train_s: float = 0.0,
    infer_s: float = 0.0,
    sample_weighting: str | None = None,
):
    from ml_training.full_mimic.reports import (
        under_over_triage, high_acuity_recall_report, calibration_report,
        ordinal_acuity_metrics,
    )
    from ml_training.full_mimic.evaluation import auroc_pr_auc
    from sklearn.metrics import (
        accuracy_score, f1_score, precision_recall_fscore_support, confusion_matrix,
    )
    import numpy as np

    y_true = np.asarray(y_true).ravel()
    pred = np.asarray(pred).ravel()
    if proba_aligned is not None:
        proba_list = np.asarray(proba_aligned).tolist()
        calib = calibration_report(y_true.tolist(), proba_list, labels)
        high_cols = [i for i, c in enumerate(labels) if int(c) <= 2]
        proba_high = (
            np.asarray(proba_aligned)[:, high_cols].sum(axis=1)
            if high_cols else np.zeros(len(y_true))
        )
        auc_metrics = auroc_pr_auc(y_true, proba_high)
    else:
        calib = {"brier_mean": None}
        auc_metrics = {"auroc": None, "pr_auc": None}

    p, r, f, sup = precision_recall_fscore_support(
        y_true, pred, labels=labels, zero_division=0)
    per_class = {
        str(lbl): {"precision": float(p[i]), "recall": float(r[i]),
                   "f1": float(f[i]), "support": int(sup[i])}
        for i, lbl in enumerate(labels)
    }
    cm = confusion_matrix(y_true, pred, labels=labels).tolist()
    return {
        "model_name": name,
        "accuracy": float(accuracy_score(y_true, pred)),
        "macro_f1": float(f1_score(y_true, pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, pred, average="weighted", zero_division=0)),
        "high_acuity_recall": high_acuity_recall_report(y_true.tolist(), pred.tolist()),
        "under_over_triage": under_over_triage(y_true.tolist(), pred.tolist()),
        "ordinal_metrics": ordinal_acuity_metrics(y_true.tolist(), pred.tolist()),
        "calibration": calib,
        "auroc_pr_auc": auc_metrics,
        "per_class": per_class,
        "confusion_matrix": cm,
        "confusion_matrix_labels": [str(l) for l in labels],
        "train_seconds": round(float(train_s), 3),
        "infer_seconds": round(float(infer_s), 3),
        "sample_weighting": sample_weighting,
    }


def _metric_summary(metrics: dict, over_triage_metrics: dict | None = None) -> dict:
    ot = over_triage_metrics or metrics.get("over_triage_specificity") or {}
    uot = metrics.get("under_over_triage") or {}
    har = metrics.get("high_acuity_recall") or {}
    auc = metrics.get("auroc_pr_auc") or {}
    return {
        "accuracy": metrics.get("accuracy"),
        "macro_f1": metrics.get("macro_f1"),
        "weighted_f1": metrics.get("weighted_f1"),
        "high_acuity_recall": har.get("recall"),
        "high_acuity_precision": ot.get("high_acuity_precision"),
        "severe_under_triage_rate": uot.get("severe_under_triage_rate"),
        "under_triage_rate": uot.get("under_triage_rate"),
        "over_triage_rate": uot.get("over_triage_rate"),
        "specificity": ot.get("specificity"),
        "predicted_urgent_rate": ot.get("predicted_urgent_rate"),
        "auroc": auc.get("auroc"),
        "pr_auc": auc.get("pr_auc"),
    }


def _threshold_grid() -> list[float]:
    raw = os.environ.get("MIMIC_HIGH_ACUITY_THRESHOLDS", "").strip()
    if raw:
        vals = []
        for part in raw.split(","):
            try:
                val = float(part.strip())
            except ValueError:
                continue
            if 0.0 <= val <= 1.0:
                vals.append(val)
        if vals:
            return sorted(set(vals))
    return [round(v / 100.0, 2) for v in range(5, 96, 5)]


def _under_penalty_grid() -> list[float]:
    raw = os.environ.get("MIMIC_UNDER_TRIAGE_COST_PENALTIES", "").strip()
    if raw:
        vals = []
        for part in raw.split(","):
            try:
                val = float(part.strip())
            except ValueError:
                continue
            if val >= 1.0:
                vals.append(val)
        if vals:
            return sorted(set(vals))
    return [1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0]


def _apply_high_acuity_threshold(pred, proba_aligned, labels, threshold: float):
    import numpy as np

    pred = np.asarray(pred).ravel().astype(int).copy()
    labels = [int(l) for l in labels]
    high_cols = [i for i, label in enumerate(labels) if label <= 2]
    if proba_aligned is None or not high_cols:
        return pred
    high_prob = proba_aligned[:, high_cols].sum(axis=1)
    high_best = np.asarray([labels[high_cols[int(np.argmax(row[high_cols]))]]
                            for row in proba_aligned])
    mask = high_prob >= float(threshold)
    pred[mask] = high_best[mask]
    return pred


def _ordinal_cost_matrix(labels, *, under_penalty: float, over_penalty: float = 1.0):
    matrix = []
    for true_label in labels:
        row = []
        for predicted_label in labels:
            distance = abs(int(predicted_label) - int(true_label))
            if distance == 0:
                row.append(0.0)
            elif int(predicted_label) > int(true_label):
                row.append(float(under_penalty) * float(distance))
            else:
                row.append(float(over_penalty) * float(distance))
        matrix.append(row)
    return matrix


def _apply_ordinal_cost_rule(proba_aligned, labels, *, under_penalty: float):
    import numpy as np

    labels = [int(l) for l in labels]
    cost = np.asarray(
        _ordinal_cost_matrix(labels, under_penalty=under_penalty),
        dtype=float,
    )
    expected_cost = np.asarray(proba_aligned, dtype=float).dot(cost)
    return np.asarray([labels[int(i)] for i in np.argmin(expected_cost, axis=1)])


def _apply_decision_rule(pred, proba_aligned, labels, decision_rule: dict | None):
    if not decision_rule or decision_rule.get("type") == "argmax":
        return pred
    rule_type = decision_rule.get("type")
    if proba_aligned is None:
        raise ValueError(f"decision rule {rule_type!r} requires class probabilities")
    if rule_type == "high_acuity_threshold":
        return _apply_high_acuity_threshold(
            pred, proba_aligned, labels, float(decision_rule["threshold"]))
    if rule_type == "ordinal_cost_sensitive":
        return _apply_ordinal_cost_rule(
            proba_aligned, labels,
            under_penalty=float(decision_rule["under_triage_penalty"]))
    raise ValueError(f"unsupported decision rule: {rule_type!r}")


def _tune_candidate_decision_rule(
    name,
    est,
    Xva,
    yva,
    labels,
    base_metrics,
    selection_constraints,
):
    """Tune post-model decision rules using validation data only."""
    from ml_training.full_mimic.evaluation import over_triage_specificity
    import numpy as np

    base_pred = np.asarray(est.predict(Xva)).ravel()
    proba = _predict_proba_aligned(est, Xva, labels)
    base_ot = over_triage_specificity(yva, base_pred)
    base_rule = {
        "type": "argmax",
        "labels": [int(l) for l in labels],
        "validation_tuned": False,
    }
    candidates = [{
        "rule": base_rule,
        "metrics": base_metrics,
        "over_triage_specificity": base_ot,
        "passes_over_triage_constraint": _passes_selection_constraint(
            base_metrics, base_ot, selection_constraints),
    }]
    threshold_curve = []
    cost_curve = []

    if proba is not None:
        for threshold in _threshold_grid():
            pred = _apply_high_acuity_threshold(base_pred, proba, labels, threshold)
            metrics = _metrics_from_predictions(name, yva, pred, labels, proba_aligned=proba)
            ot = over_triage_specificity(yva, pred)
            rule = {
                "type": "high_acuity_threshold",
                "threshold": float(threshold),
                "labels": [int(l) for l in labels],
                "validation_tuned": True,
            }
            record = {
                "threshold": float(threshold),
                **_metric_summary(metrics, ot),
                "passes_over_triage_constraint": _passes_selection_constraint(
                    metrics, ot, selection_constraints),
                "selection_profile": selection_constraints["profile"],
            }
            threshold_curve.append(record)
            candidates.append({
                "rule": rule,
                "metrics": metrics,
                "over_triage_specificity": ot,
                "passes_over_triage_constraint": record["passes_over_triage_constraint"],
            })
        for penalty in _under_penalty_grid():
            pred = _apply_ordinal_cost_rule(proba, labels, under_penalty=penalty)
            metrics = _metrics_from_predictions(name, yva, pred, labels, proba_aligned=proba)
            ot = over_triage_specificity(yva, pred)
            rule = {
                "type": "ordinal_cost_sensitive",
                "under_triage_penalty": float(penalty),
                "over_triage_penalty": 1.0,
                "labels": [int(l) for l in labels],
                "cost_matrix": _ordinal_cost_matrix(labels, under_penalty=penalty),
                "validation_tuned": True,
            }
            record = {
                "under_triage_penalty": float(penalty),
                **_metric_summary(metrics, ot),
                "passes_over_triage_constraint": _passes_selection_constraint(
                    metrics, ot, selection_constraints),
                "selection_profile": selection_constraints["profile"],
            }
            cost_curve.append(record)
            candidates.append({
                "rule": rule,
                "metrics": metrics,
                "over_triage_specificity": ot,
                "passes_over_triage_constraint": record["passes_over_triage_constraint"],
            })

    passing = [c for c in candidates if c["passes_over_triage_constraint"]]
    pool = passing or [candidates[0]]
    for candidate in pool:
        candidate["metrics"]["over_triage_specificity"] = candidate[
            "over_triage_specificity"]
    selected = sorted(
        pool,
        key=lambda c: _selection_score(c["metrics"], selection_constraints),
        reverse=True,
    )[0]
    selected_metrics = selected["metrics"]
    selected_ot = selected["over_triage_specificity"]
    return {
        "validation_only": True,
        "test_set_used": False,
        "selected_rule": selected["rule"],
        "selected_validation_metrics": _metric_summary(selected_metrics, selected_ot),
        "argmax_validation_metrics": _metric_summary(base_metrics, base_ot),
        "selection_profile": selection_constraints["profile"],
        "selection_constraints": dict(selection_constraints),
        "threshold_curve": threshold_curve,
        "cost_curve": cost_curve,
        "selected_metrics_full": selected_metrics,
        "selected_over_triage_specificity": selected_ot,
        "selected_passes_over_triage_constraint": selected[
            "passes_over_triage_constraint"
        ],
    }


def _apply_tuning_to_validation_metrics(name, metrics, tuning):
    if not tuning:
        return metrics
    selected_metrics = dict(tuning["selected_metrics_full"])
    selected_metrics["model_name"] = name
    selected_metrics["train_seconds"] = metrics.get("train_seconds", 0.0)
    selected_metrics["infer_seconds"] = metrics.get("infer_seconds", 0.0)
    selected_metrics["sample_weighting"] = metrics.get("sample_weighting")
    for key in (
        "imbalance_strategy",
        "class_weight_mode",
        "sampler",
        "sampler_scope",
        "balance_before_split",
        "validation_and_test_resampled",
        "train_distribution_before",
        "train_distribution_after",
        "validation_distribution",
        "test_distribution",
    ):
        if key in metrics:
            selected_metrics[key] = metrics[key]
    selected_metrics["argmax_validation_metrics"] = tuning["argmax_validation_metrics"]
    selected_metrics["decision_rule_tuning"] = {
        "validation_only": True,
        "test_set_used": False,
        "selection_profile": tuning["selection_profile"],
        "selection_constraints": tuning["selection_constraints"],
        "selected_rule": tuning["selected_rule"],
        "selected_validation_metrics": tuning["selected_validation_metrics"],
        "threshold_curve": tuning["threshold_curve"],
        "cost_curve": tuning["cost_curve"],
    }
    selected_metrics["selected_decision_rule"] = tuning["selected_rule"]
    selected_metrics["decision_rule_tuned_on"] = (
        "validation" if tuning["selected_rule"].get("validation_tuned") else None
    )
    return selected_metrics


def _evaluate_binary_high_acuity_detector(
    Rtr, Rva, Rte, ytr, yva, yte, selection_constraints,
):
    """Train/report a binary acuity 1/2 detector. It is not the served model."""
    from ml_training.full_mimic.evaluation import over_triage_specificity, auroc_pr_auc
    from ml_training.full_mimic.raw_triage_pipeline import make_raw_tfidf_logistic_pipeline
    from sklearn.metrics import precision_recall_fscore_support, confusion_matrix
    import numpy as np

    min_df = 1 if _quick() else int(os.environ.get("MIMIC_TFIDF_MIN_DF", "3"))
    word_max = 1000 if _quick() else int(os.environ.get("MIMIC_TFIDF_WORD_MAX_FEATURES", "50000"))
    char_max = 1000 if _quick() else int(os.environ.get("MIMIC_TFIDF_CHAR_MAX_FEATURES", "50000"))
    model = make_raw_tfidf_logistic_pipeline(
        min_df=min_df,
        word_max_features=word_max,
        char_max_features=char_max,
        max_iter=int(os.environ.get("MIMIC_BINARY_TFIDF_MAX_ITER", "500" if _quick() else "1000")),
        solver=os.environ.get("MIMIC_BINARY_TFIDF_SOLVER", "saga"),
        tol=float(os.environ.get("MIMIC_BINARY_TFIDF_TOL", "0.01" if _quick() else "0.0001")),
    )
    ytr_bin = (np.asarray(ytr) <= 2).astype(int)
    yva_bin = (np.asarray(yva) <= 2).astype(int)
    yte_bin = (np.asarray(yte) <= 2).astype(int)
    model.fit(Rtr, ytr_bin)
    classes = [int(c) for c in getattr(model, "classes_", [0, 1])]
    pos_col = classes.index(1) if 1 in classes else len(classes) - 1
    pva = np.asarray(model.predict_proba(Rva), dtype=float)[:, pos_col]
    pte = np.asarray(model.predict_proba(Rte), dtype=float)[:, pos_col]
    curve = []
    for threshold in _threshold_grid():
        pred_bin = (pva >= threshold).astype(int)
        recall, specificity = _binary_recall_specificity(yva_bin, pred_bin)
        urgent_rate = float(np.mean(pred_bin)) if len(pred_bin) else None
        passes = (
            recall is not None
            and specificity is not None
            and specificity >= selection_constraints["min_specificity"]
            and urgent_rate <= selection_constraints["max_predicted_urgent_rate"]
            and (
                selection_constraints["min_high_acuity_recall"] is None
                or recall >= selection_constraints["min_high_acuity_recall"]
            )
        )
        curve.append({
            "threshold": float(threshold),
            "high_acuity_recall": recall,
            "specificity": specificity,
            "predicted_urgent_rate": urgent_rate,
            "passes_selection_constraint": passes,
        })
    pool = [row for row in curve if row["passes_selection_constraint"]] or curve
    selected = sorted(
        pool,
        key=lambda r: (
            -1.0 if r["specificity"] is None else r["specificity"],
            -1.0 * (r["predicted_urgent_rate"] or 1.0),
            -1.0 if r["high_acuity_recall"] is None else r["high_acuity_recall"],
        ),
        reverse=True,
    )[0]
    test_pred_bin = (pte >= selected["threshold"]).astype(int)
    precision, recall, f1, support = precision_recall_fscore_support(
        yte_bin, test_pred_bin, labels=[0, 1], zero_division=0)
    specificity_view = over_triage_specificity(np.asarray(yte), np.where(test_pred_bin == 1, 2, 3))
    return {
        "model_name": "binary_raw_tfidf_word_char_logistic_high_acuity_detector",
        "target": "acuity_1_or_2_vs_3_4_5",
        "validation_only_threshold_selection": True,
        "test_set_used_for_threshold_selection": False,
        "selection_profile": selection_constraints["profile"],
        "selection_constraints": dict(selection_constraints),
        "selected_threshold": selected["threshold"],
        "validation_threshold_curve": curve,
        "untouched_test_metrics": {
            "precision_non_high_acuity": float(precision[0]),
            "precision_high_acuity": float(precision[1]),
            "recall_non_high_acuity": float(recall[0]),
            "recall_high_acuity": float(recall[1]),
            "f1_non_high_acuity": float(f1[0]),
            "f1_high_acuity": float(f1[1]),
            "support_non_high_acuity": int(support[0]),
            "support_high_acuity": int(support[1]),
            "confusion_matrix_labels": ["not_high_acuity", "high_acuity"],
            "confusion_matrix": confusion_matrix(
                yte_bin, test_pred_bin, labels=[0, 1]).tolist(),
            "specificity_view": specificity_view,
            "auroc_pr_auc": auroc_pr_auc(np.asarray(yte), pte),
        },
        "note": "Research-only binary safety detector report; final artefact remains the selected multiclass model.",
    }


def _binary_recall_specificity(y_true_bin, y_pred_bin):
    import numpy as np

    y_true_bin = np.asarray(y_true_bin).astype(int)
    y_pred_bin = np.asarray(y_pred_bin).astype(int)
    tp = int(np.sum((y_true_bin == 1) & (y_pred_bin == 1)))
    fn = int(np.sum((y_true_bin == 1) & (y_pred_bin == 0)))
    tn = int(np.sum((y_true_bin == 0) & (y_pred_bin == 0)))
    fp = int(np.sum((y_true_bin == 0) & (y_pred_bin == 1)))
    recall = tp / (tp + fn) if (tp + fn) else None
    specificity = tn / (tn + fp) if (tn + fp) else None
    return recall, specificity


def evaluate_candidate(name, est, Xtr, Xte, ytr, yte, labels):
    import numpy as np

    t0 = time.perf_counter()
    fit_kwargs = {}
    if "xgboost" in name and "smote" not in name:
        from sklearn.utils.class_weight import compute_sample_weight
        sample_weight = compute_sample_weight("balanced", ytr)
        if hasattr(est, "steps") or hasattr(est, "named_steps"):
            fit_kwargs["estimator__sample_weight"] = sample_weight
        else:
            fit_kwargs["sample_weight"] = sample_weight
    est.fit(Xtr, ytr, **fit_kwargs)
    train_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    pred = est.predict(Xte)
    infer_s = time.perf_counter() - t1
    pred = np.asarray(pred).ravel()  # CatBoost returns 2D (n,1); flatten
    proba = _predict_proba_aligned(est, Xte, labels)
    return _metrics_from_predictions(
        name,
        yte,
        pred,
        labels,
        proba_aligned=proba,
        train_s=train_s,
        infer_s=infer_s,
        sample_weighting="balanced" if fit_kwargs else None,
    )


def _write_csv(path, candidates, labels):
    cols = [
        "deployment_eligible", "artifact_contract_version", "input_type",
        "imbalance_strategy", "class_weight_mode", "sampler",
        "selected_decision_rule_type", "decision_rule_tuned_on",
        "model_name", "high_acuity_recall", "severe_under_triage_rate",
        "under_triage_rate", "over_triage_rate", "mae",
        "quadratic_weighted_kappa", "within_1_acuity_level_accuracy",
        "specificity", "high_acuity_precision", "predicted_urgent_rate",
        "passes_over_triage_constraint",
        "high_acuity_threshold", "cost_sensitive_rule_used",
        "auroc", "pr_auc", "accuracy", "macro_f1", "weighted_f1",
        "train_seconds", "infer_seconds",
        "train_distribution_before", "train_distribution_after",
        "validation_distribution", "test_distribution",
    ]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in candidates:
            decision_rule = c.get("selected_decision_rule") or {}
            decision_rule_type = decision_rule.get("type")
            threshold = (
                decision_rule.get("threshold")
                if decision_rule_type == "high_acuity_threshold"
                else None
            )
            cost_sensitive = decision_rule_type == "ordinal_cost_sensitive"
            over_triage = c.get("over_triage_specificity") or {}
            auc = c.get("auroc_pr_auc") or {}
            w.writerow([
                c.get("deployment_eligible"),
                c.get("artifact_contract_version"),
                c.get("input_type"),
                c.get("imbalance_strategy"),
                c.get("class_weight_mode"),
                c.get("sampler"),
                decision_rule_type,
                c.get("decision_rule_tuned_on"),
                c["model_name"],
                (c["high_acuity_recall"] or {}).get("recall"),
                (c["under_over_triage"] or {}).get("severe_under_triage_rate"),
                (c["under_over_triage"] or {}).get("under_triage_rate"),
                (c["under_over_triage"] or {}).get("over_triage_rate"),
                (c.get("ordinal_metrics") or {}).get("mae"),
                (c.get("ordinal_metrics") or {}).get("quadratic_weighted_kappa"),
                (c.get("ordinal_metrics") or {}).get("within_1_acuity_level_accuracy"),
                over_triage.get("specificity"),
                over_triage.get("high_acuity_precision"),
                over_triage.get("predicted_urgent_rate"),
                c.get("passes_over_triage_constraint"),
                threshold,
                cost_sensitive,
                auc.get("auroc"),
                auc.get("pr_auc"),
                c["accuracy"], c["macro_f1"], c["weighted_f1"],
                c["train_seconds"], c["infer_seconds"],
                json.dumps(c.get("train_distribution_before")),
                json.dumps(c.get("train_distribution_after")),
                json.dumps(c.get("validation_distribution")),
                json.dumps(c.get("test_distribution")),
            ])


def _write_auc_comparison_csv(path, candidates) -> None:
    cols = [
        "model_name", "imbalance_strategy", "class_weight_mode", "sampler",
        "auroc", "pr_auc", "high_acuity_recall", "high_acuity_precision",
        "specificity", "predicted_urgent_rate", "over_triage_rate",
        "passes_over_triage_constraint",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for candidate in candidates:
            auc = candidate.get("auroc_pr_auc") or {}
            over = candidate.get("over_triage_specificity") or {}
            har = candidate.get("high_acuity_recall") or {}
            writer.writerow([
                candidate.get("model_name"),
                candidate.get("imbalance_strategy"),
                candidate.get("class_weight_mode"),
                candidate.get("sampler"),
                auc.get("auroc"),
                auc.get("pr_auc"),
                har.get("recall"),
                over.get("high_acuity_precision"),
                over.get("specificity"),
                over.get("predicted_urgent_rate"),
                over.get("over_triage_rate"),
                candidate.get("passes_over_triage_constraint"),
            ])


def _write_binary_curve_artifacts(
    out_dir,
    *,
    training_run_id: str,
    y_true,
    proba_high,
    prefix: str = "selected_model",
) -> dict:
    import numpy as np
    from sklearn.metrics import (
        average_precision_score,
        auc as sklearn_auc,
        precision_recall_curve,
        roc_curve,
    )

    y_bin = (np.asarray(y_true) <= 2).astype(int)
    proba = np.asarray(proba_high, dtype=float)
    report = {
        "training_run_id": training_run_id,
        "binary_target": "acuity_1_2_high_acuity_vs_3_5_non_high_acuity",
        "test_set_used_for_model_selection": False,
        "status": "SKIPPED",
        "reason": None,
        "roc_curve_csv": f"{prefix}_roc_curve.csv",
        "pr_curve_csv": f"{prefix}_pr_curve.csv",
        "roc_curve_png": f"{prefix}_roc_curve.png",
        "pr_curve_png": f"{prefix}_pr_curve.png",
    }
    if len(y_bin) == 0 or len(set(y_bin.tolist())) < 2 or proba.shape[0] != len(y_bin):
        report["reason"] = "binary test labels or probabilities are not usable"
        (out_dir / f"{prefix}_binary_curve_report.json").write_text(
            json.dumps(report, indent=2))
        return report

    fpr, tpr, roc_thresholds = roc_curve(y_bin, proba)
    precision, recall, pr_thresholds = precision_recall_curve(y_bin, proba)
    roc_auc = float(sklearn_auc(fpr, tpr))
    pr_auc = float(average_precision_score(y_bin, proba))

    with open(out_dir / f"{prefix}_roc_curve.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["false_positive_rate", "true_positive_rate", "threshold"])
        for idx, (x, y) in enumerate(zip(fpr, tpr)):
            threshold = roc_thresholds[idx] if idx < len(roc_thresholds) else None
            writer.writerow([float(x), float(y), None if threshold is None else float(threshold)])

    with open(out_dir / f"{prefix}_pr_curve.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["recall", "precision", "threshold"])
        for idx, (x, y) in enumerate(zip(recall, precision)):
            threshold = pr_thresholds[idx] if idx < len(pr_thresholds) else None
            writer.writerow([float(x), float(y), None if threshold is None else float(threshold)])

    report.update({
        "status": "WRITTEN",
        "reason": None,
        "roc_auc": roc_auc,
        "pr_auc": pr_auc,
        "positive_class_prevalence": float(np.mean(y_bin)),
    })

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(fpr, tpr, label=f"ROC-AUC = {roc_auc:.3f}", color="#0b5cab", linewidth=2)
        ax.plot([0, 1], [0, 1], linestyle="--", color="#9aa4b2", linewidth=1)
        ax.set_xlabel("False positive rate")
        ax.set_ylabel("True positive rate / recall")
        ax.set_title("Selected model ROC curve: high acuity 1-2 vs 3-5")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="lower right")
        fig.tight_layout()
        fig.savefig(out_dir / f"{prefix}_roc_curve.png", dpi=160)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(recall, precision, label=f"PR-AUC = {pr_auc:.3f}", color="#087f5b", linewidth=2)
        ax.axhline(float(np.mean(y_bin)), linestyle="--", color="#9aa4b2", linewidth=1,
                   label="High-acuity prevalence")
        ax.set_xlabel("Recall")
        ax.set_ylabel("Precision")
        ax.set_title("Selected model precision-recall curve: high acuity 1-2")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="lower left")
        fig.tight_layout()
        fig.savefig(out_dir / f"{prefix}_pr_curve.png", dpi=160)
        plt.close(fig)
    except Exception as exc:
        report["plot_status"] = "CSV_WRITTEN_PNG_SKIPPED"
        report["plot_reason"] = f"{type(exc).__name__}: {exc}"
    else:
        report["plot_status"] = "PNG_WRITTEN"

    (out_dir / f"{prefix}_binary_curve_report.json").write_text(
        json.dumps(report, indent=2))
    return report


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
            "high_acuity_precision": (
                (test_metrics.get("over_triage_specificity") or {}).get(
                    "high_acuity_precision"
                )
            ),
            "specificity": (
                (test_metrics.get("over_triage_specificity") or {}).get(
                    "specificity"
                )
            ),
            "predicted_urgent_rate": (
                (test_metrics.get("over_triage_specificity") or {}).get(
                    "predicted_urgent_rate"
                )
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
            "auroc": (test_metrics.get("auroc_pr_auc") or {}).get("auroc"),
            "pr_auc": (test_metrics.get("auroc_pr_auc") or {}).get("pr_auc"),
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
        "selection_profile": comparison.get("selection_profile"),
        "selection_constraints": comparison.get("selection_constraints"),
        "selected_imbalance_strategy": best.get("imbalance_strategy"),
        "selected_class_weight_mode": best.get("class_weight_mode"),
        "selected_sampler": best.get("sampler"),
        "class_distribution_report": comparison.get("class_distribution_report"),
        "selected_decision_rule": comparison.get("selected_decision_rule"),
        "decision_rule_tuned_on": comparison.get("decision_rule_tuned_on"),
        "test_set_used_for_decision_rule_tuning": False,
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
    parser.add_argument(
        "--allow-shifted-temporal-split",
        action="store_true",
        help=(
            "Use MIMIC intime ordering for a patient-grouped shifted-date temporal "
            "sensitivity analysis. Default is stratified patient-grouped because "
            "MIMIC dates are patient-shifted de-identification dates, not a true "
            "cross-patient deployment chronology."
        ),
    )
    parser.add_argument(
        "--selection-profile",
        choices=("balanced_safety", "recall_max"),
        default=os.environ.get("MIMIC_SELECTION_PROFILE", "balanced_safety"),
        help=(
            "Validation-selection profile. balanced_safety is the default and "
            "requires better specificity/lower urgent-rate before a threshold can "
            "win. recall_max reproduces the earlier recall-first behavior."
        ),
    )
    parser.add_argument(
        "--min-specificity",
        type=float,
        default=None,
        help="Override the selected profile's minimum validation specificity.",
    )
    parser.add_argument(
        "--max-predicted-urgent-rate",
        type=float,
        default=None,
        help="Override the selected profile's maximum validation urgent-rate.",
    )
    parser.add_argument(
        "--min-high-acuity-recall",
        type=float,
        default=None,
        help=(
            "Override the selected profile's minimum validation high-acuity "
            "recall. Choose recall_max for no recall floor."
        ),
    )
    args = parser.parse_args(argv)
    if args.quick_test:
        os.environ["MIMIC_COMPARE_QUICK"] = "1"
    try:
        selection_constraints = _selection_constraints(
            profile=args.selection_profile,
            min_specificity=args.min_specificity,
            max_predicted_urgent_rate=args.max_predicted_urgent_rate,
            min_high_acuity_recall=args.min_high_acuity_recall,
        )
    except ValueError as exc:
        sys.stderr.write(f"REFUSED: {exc}\n")
        return 2
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
    from app.data_pipeline.mimic_full_loader import load_mimic_full_cases_triage_time
    from ml_training.feature_engineering import (
        build_feature_frame_with_meta,
        validate_feature_schema,
    )
    from ml_training.full_mimic.raw_triage_pipeline import (
        RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION,
        RAW_TRIAGE_INPUT_COLUMNS,
        RAW_TRIAGE_INPUT_TYPE,
        raw_triage_dataframe_from_cases,
        validate_raw_triage_columns,
    )
    from ml_training.full_mimic.evaluation import (
        stratified_patient_grouped_split, temporal_split, assert_no_subject_overlap,
        over_triage_specificity, auroc_pr_auc, bootstrap_ci, subgroup_metrics,
    )

    import numpy as np
    import joblib
    import sklearn

    cases = load_mimic_full_cases_triage_time()
    complaint_texts = _chief_complaints_from_cases(cases)
    raw_triage_frame = raw_triage_dataframe_from_cases(cases)
    validate_raw_triage_columns(RAW_TRIAGE_INPUT_COLUMNS)
    X, y, feature_names, subject_ids, intimes = build_feature_frame_with_meta(cases)
    training_run_id = str(uuid4())
    validate_feature_schema(list(feature_names))
    feature_schema_hash = _feature_schema_hash(feature_names)
    mask = [v is not None for v in y]
    X2 = X[mask]
    y2 = np.array([v for v, m in zip(y, mask) if m])
    text2 = np.array([v for v, m in zip(complaint_texts, mask) if m], dtype=object)
    raw_triage2 = raw_triage_frame.loc[mask].reset_index(drop=True)
    subj2 = [s for s, m in zip(subject_ids, mask) if m]
    intimes2 = [t for t, m in zip(intimes, mask) if m]
    labels = sorted(set(y2.tolist()))

    # THREE-way split. Default for MIMIC-IV-ED is STRATIFIED PATIENT-GROUPED:
    # patient-level groups prevent repeat-visit leakage, and stratification keeps
    # the severe acuity imbalance represented. A shifted-date temporal split is
    # available only as an explicit sensitivity analysis because MIMIC dates are
    # de-identified with patient-specific offsets.
    use_shifted_temporal = (
        args.allow_shifted_temporal_split
        or os.environ.get("MIMIC_ALLOW_SHIFTED_TEMPORAL_SPLIT", "").lower() == "true"
    )
    split = None
    split_kind = "stratified_patient_grouped"
    if use_shifted_temporal:
        split = temporal_split(intimes2, subj2)
        split_kind = "shifted_temporal_patient_grouped" if split is not None else split_kind
    if split is None:
        split = stratified_patient_grouped_split(subj2, y2)
    tr_idx, va_idx, te_idx = split
    # Hard guarantee: no subject_id appears in more than one split.
    assert_no_subject_overlap(subj2, tr_idx, va_idx, te_idx)
    patient_overlap_count = _patient_overlap_count(subj2, tr_idx, va_idx, te_idx)

    Xtr, ytr = X2[tr_idx], y2[tr_idx]
    Xva, yva = X2[va_idx], y2[va_idx]
    Xte, yte = X2[te_idx], y2[te_idx]
    Rtr = raw_triage2.iloc[tr_idx].reset_index(drop=True)
    Rva = raw_triage2.iloc[va_idx].reset_index(drop=True)
    Rte = raw_triage2.iloc[te_idx].reset_index(drop=True)
    sex_idx = feature_names.index("sex_male")
    class_distribution_report = _split_class_distribution_report(
        training_run_id=training_run_id,
        split_kind=split_kind,
        labels=labels,
        y_all=y2,
        y_train=ytr,
        y_val=yva,
        y_test=yte,
    )

    # Evaluate every candidate on the VALIDATION set (selection set). The untouched
    # TEST set is scored once, later, only for the selected model.
    results, fitted, fitted_contracts, decision_rule_reports = [], {}, {}, {}
    for name, est in _candidates(candidate_names):
        try:
            m = evaluate_candidate(name, est, Xtr, Xva, ytr, yva, labels)
            m["artifact_contract_version"] = 1
            m["input_type"] = "structured_feature_matrix"
            m["deployment_eligible"] = True
            m.update(_candidate_imbalance_metadata(name, ytr, yva, yte, labels))
            # over-triage / specificity view on validation
            va_pred = np.asarray(est.predict(Xva)).ravel()
            ot = over_triage_specificity(yva, va_pred)
            tuning = _tune_candidate_decision_rule(
                name, est, Xva, yva, labels, m, selection_constraints)
            decision_rule_reports[name] = {
                "validation_only": True,
                "test_set_used": False,
                "selection_profile": tuning["selection_profile"],
                "selection_constraints": tuning["selection_constraints"],
                "selected_rule": tuning["selected_rule"],
                "selected_validation_metrics": tuning["selected_validation_metrics"],
                "argmax_validation_metrics": tuning["argmax_validation_metrics"],
                "threshold_curve": tuning["threshold_curve"],
                "cost_curve": tuning["cost_curve"],
            }
            m = _apply_tuning_to_validation_metrics(name, m, tuning)
            ot = tuning["selected_over_triage_specificity"]
            m["over_triage_specificity"] = ot
            m["selection_profile"] = selection_constraints["profile"]
            m["selection_constraints"] = dict(selection_constraints)
            m["passes_over_triage_constraint"] = _passes_selection_constraint(
                m, ot, selection_constraints)
            results.append(m)
            fitted[name] = est
            fitted_contracts[name] = 1
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

    for name, est in _raw_triage_candidates(candidate_names):
        try:
            m = evaluate_candidate(name, est, Rtr, Rva, ytr, yva, labels)
            m["artifact_contract_version"] = RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION
            m["input_type"] = RAW_TRIAGE_INPUT_TYPE
            m["raw_input_columns"] = list(RAW_TRIAGE_INPUT_COLUMNS)
            m["deployment_eligible"] = True
            m.update(_candidate_imbalance_metadata(name, ytr, yva, yte, labels))
            va_pred = np.asarray(est.predict(Rva)).ravel()
            ot = over_triage_specificity(yva, va_pred)
            tuning = _tune_candidate_decision_rule(
                name, est, Rva, yva, labels, m, selection_constraints)
            decision_rule_reports[name] = {
                "validation_only": True,
                "test_set_used": False,
                "selection_profile": tuning["selection_profile"],
                "selection_constraints": tuning["selection_constraints"],
                "selected_rule": tuning["selected_rule"],
                "selected_validation_metrics": tuning["selected_validation_metrics"],
                "argmax_validation_metrics": tuning["argmax_validation_metrics"],
                "threshold_curve": tuning["threshold_curve"],
                "cost_curve": tuning["cost_curve"],
            }
            m = _apply_tuning_to_validation_metrics(name, m, tuning)
            ot = tuning["selected_over_triage_specificity"]
            m["over_triage_specificity"] = ot
            m["selection_profile"] = selection_constraints["profile"]
            m["selection_constraints"] = dict(selection_constraints)
            m["passes_over_triage_constraint"] = _passes_selection_constraint(
                m, ot, selection_constraints)
            results.append(m)
            fitted[name] = est
            fitted_contracts[name] = RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION
            print(f"  {name}: high_acuity_recall="
                  f"{m['high_acuity_recall'].get('recall')} "
                  f"severe_under_triage={m['under_over_triage'].get('severe_under_triage_rate')} "
                  f"under_triage={m['under_over_triage'].get('under_triage_rate')} "
                  f"specificity={ot.get('specificity')} "
                  f"urgent_rate={ot.get('predicted_urgent_rate')} "
                  f"macro_f1={m['macro_f1']:.3f} "
                  f"constraint={'PASS' if m['passes_over_triage_constraint'] else 'FAIL'} "
                  f"contract=v{RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION}")
        except Exception as exc:
            print(f"  {name}: SKIPPED ({type(exc).__name__}: {exc})")

    try:
        binary_high_acuity_detector_report = _evaluate_binary_high_acuity_detector(
            Rtr, Rva, Rte, ytr, yva, yte, selection_constraints)
        print("  binary_raw_tfidf_word_char_logistic_high_acuity_detector: "
              "research-only threshold report written")
    except Exception as exc:
        binary_high_acuity_detector_report = {
            "model_name": "binary_raw_tfidf_word_char_logistic_high_acuity_detector",
            "status": "SKIPPED",
            "reason": f"{type(exc).__name__}: {exc}",
            "validation_only_threshold_selection": True,
            "test_set_used_for_threshold_selection": False,
            "selection_profile": selection_constraints["profile"],
            "selection_constraints": dict(selection_constraints),
        }
        print("  binary_raw_tfidf_word_char_logistic_high_acuity_detector: "
              f"SKIPPED ({type(exc).__name__}: {exc})")

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
                    "Legacy TF-IDF/text baseline only. Use the v2 "
                    "raw_tfidf_word_char_logistic candidate for a serving-eligible "
                    "raw-chief-complaint pipeline."
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

    from ml_training.full_mimic.feature_importance import (
        extract_feature_importance_report,
        write_feature_importance_reports,
    )
    feature_importance_reports = {}
    for name, est in fitted.items():
        try:
            feature_importance_reports[name] = extract_feature_importance_report(
                model_name=name,
                model=est,
                base_feature_names=feature_names,
                labels=labels,
            )
        except Exception as exc:
            feature_importance_reports[name] = {
                "model_name": name,
                "status": "unavailable",
                "reason": f"{type(exc).__name__}: {exc}",
                "top_features": [],
                "class_specific": {},
            }

    if not results:
        sys.stderr.write("No candidate models could be trained.\n")
        return 1

    # SELECTION on validation: only candidates that pass the over-triage constraint
    # are eligible (blocks 'predict everything urgent'). If none pass, fail rather
    # than silently shipping a degenerate model.
    eligible = [m for m in results if m.get("passes_over_triage_constraint")]
    if not eligible:
        sys.stderr.write(
            "No candidate passed the configured validation selection constraints "
            f"({selection_constraints}) on validation. "
            "Refusing to select a model.\n")
        results_sorted = sorted(
            results,
            key=lambda m: _selection_score(m, selection_constraints),
            reverse=True,
        )
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
                "safety-first on VALIDATION among candidates passing the configured "
                "over-triage/specificity/recall constraints. No model is selected "
                "when all candidates fail."
            ),
            "selection_profile": selection_constraints["profile"],
            "selection_constraints": dict(selection_constraints),
            "split_kind": split_kind,
            "patient_level_split": True,
            "patient_overlap_train_test": int(patient_overlap_count),
            "test_set_used_for_model_selection": False,
            "test_set_used_for_decision_rule_tuning": False,
            "final_test_evaluation_once": True,
            "preprocessing_inside_pipeline": True,
            "leakage_audit_passed": True,
            "synthetic_audit_passed": True,
            "over_triage_constraint_failed": True,
            "quick_test_mode": _quick(),
            "candidate_mode": candidate_mode,
            "candidate_names_requested": candidate_names,
            "n_train": int(len(ytr)), "n_val": int(len(yva)), "n_test": int(len(yte)),
            "class_distribution_report": "full_mimic_class_distribution.json",
            "class_distribution": class_distribution_report,
            "untouched_test_metrics": None,
            "labels": [str(l) for l in labels],
            "candidates": results_sorted,
            "validation_decision_rule_reports": decision_rule_reports,
            "binary_high_acuity_detector_report": binary_high_acuity_detector_report,
            "experimental_non_serving_candidates": experimental_results,
            "experimental_non_serving_note": (
                "Legacy TF-IDF text baselines are evaluated for research "
                "comparison only. Serving-eligible raw-text candidates use the "
                "v2 raw_triage_dataframe artefact contract."
            ),
            "selected_model": None,
            "selection_rationale": (
                "No candidate passed the configured validation selection "
                "constraints. No deployable artefact or model card was written."
            ),
            "sklearn_version": sklearn.__version__,
            "note": "Aggregate research metrics only. Not clinically validated.",
        }
        assert_no_raw_rows(comparison)
        out = paths["output_dir"]
        (out / "full_mimic_model_comparison.json").write_text(json.dumps(comparison, indent=2))
        _write_csv(out / "full_mimic_model_comparison.csv", results_sorted, labels)
        _write_auc_comparison_csv(out / "all_models_roc_auc_comparison.csv", results_sorted)
        write_feature_importance_reports(
            out,
            feature_importance_reports,
            selected_model=None,
        )
        (out / "full_mimic_class_distribution.json").write_text(
            json.dumps(class_distribution_report, indent=2))
        _write_class_distribution_csv(
            out / "full_mimic_class_distribution.csv", class_distribution_report)
        (out / "full_mimic_threshold_tuning_report.json").write_text(json.dumps({
            "training_run_id": training_run_id,
            "validation_only": True,
            "test_set_used": False,
            "selection_profile": selection_constraints["profile"],
            "selection_constraints": dict(selection_constraints),
            "candidate_decision_rule_reports": decision_rule_reports,
        }, indent=2))
        (out / "full_mimic_high_acuity_detector_report.json").write_text(json.dumps(
            binary_high_acuity_detector_report, indent=2))
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

    results_sorted = sorted(
        results,
        key=lambda m: _selection_score(m, selection_constraints),
        reverse=True,
    )
    eligible_sorted = sorted(
        eligible,
        key=lambda m: _selection_score(m, selection_constraints),
        reverse=True,
    )
    constraint_failed = False
    best = eligible_sorted[0]
    best_name = best["model_name"]
    best_contract_version = fitted_contracts.get(best_name, 1)
    best_decision_rule = best.get("selected_decision_rule") or {
        "type": "argmax",
        "labels": [int(l) for l in labels],
        "validation_tuned": False,
    }
    Xtr_best = Rtr if best_contract_version == RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION else Xtr
    Xte_best = Rte if best_contract_version == RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION else Xte

    # FINAL: score the selected model ONCE on the untouched test set.
    best_est = fitted[best_name]
    test_metrics = evaluate_candidate(best_name, best_est, Xtr_best, Xte_best, ytr, yte, labels)
    te_pred = np.asarray(best_est.predict(Xte_best)).ravel()
    proba_aligned_te = _predict_proba_aligned(best_est, Xte_best, labels)
    te_pred = _apply_decision_rule(te_pred, proba_aligned_te, labels, best_decision_rule)
    if best_decision_rule.get("type") != "argmax":
        test_metrics = _metrics_from_predictions(
            best_name,
            yte,
            te_pred,
            labels,
            proba_aligned=proba_aligned_te,
            train_s=test_metrics.get("train_seconds", 0.0),
            infer_s=test_metrics.get("infer_seconds", 0.0),
            sample_weighting=test_metrics.get("sample_weighting"),
        )
    test_ot = over_triage_specificity(yte, te_pred)
    test_proba_high = None
    try:
        proba_te = proba_aligned_te
        high_cols = [i for i, c in enumerate(labels) if c <= 2]
        test_proba_high = (
            proba_te[:, high_cols].sum(axis=1)
            if high_cols else np.zeros(len(yte))
        )
        test_auc = auroc_pr_auc(yte, test_proba_high)
    except Exception:
        test_auc = {"auroc": None, "pr_auc": None}
    from sklearn.metrics import accuracy_score as _acc
    test_acc_ci = bootstrap_ci(yte, te_pred, lambda a, b: _acc(a, b))
    test_subgroups = subgroup_metrics(
        yte, te_pred, ["M" if v == 1.0 else "F" for v in Xte[:, sex_idx]])

    runner_up = eligible_sorted[1]["model_name"] if len(eligible_sorted) > 1 else None
    rationale = (
        f"Selected '{best_name}' on the VALIDATION set by safety-first ranking "
        f"among candidates that PASSED the '{selection_constraints['profile']}' "
        f"validation constraints "
        f"(specificity >= {selection_constraints['min_specificity']}, "
        f"predicted-urgent-rate <= "
        f"{selection_constraints['max_predicted_urgent_rate']}, "
        f"minimum high-acuity recall = "
        f"{selection_constraints['min_high_acuity_recall']}). "
        f"Ranking within that profile balances severe under-triage, total "
        f"under-triage, over-triage specificity, urgent-rate, F1, and accuracy. "
        f"Candidate post-model decision rules were tuned on "
        f"VALIDATION only; selected rule: {best_decision_rule.get('type')}. "
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
            "safety-first on VALIDATION among candidates passing the configured "
            "over-triage/specificity/recall constraints. balanced_safety is the "
            "default profile to avoid a low threshold that predicts most cases "
            "urgent; recall_max is available as an explicit sensitivity profile. "
            "Final reported once on untouched TEST. "
            "Default stratified patient-grouped split, or explicit shifted-date "
            "temporal sensitivity split when requested (never random row-level)."
        ),
        "selection_profile": selection_constraints["profile"],
        "selection_constraints": dict(selection_constraints),
        "split_kind": split_kind,
        "patient_level_split": True,
        "patient_overlap_train_test": int(patient_overlap_count),
        "test_set_used_for_model_selection": False,
        "test_set_used_for_decision_rule_tuning": False,
        "final_test_evaluation_once": True,
        "preprocessing_inside_pipeline": True,
        "leakage_audit_passed": True,
        "synthetic_audit_passed": True,
        "over_triage_constraint_failed": constraint_failed,
        "selected_artifact_contract_version": int(best_contract_version),
        "selected_input_type": (
            RAW_TRIAGE_INPUT_TYPE
            if best_contract_version == RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION
            else "structured_feature_matrix"
        ),
        "quick_test_mode": _quick(),
        "candidate_mode": candidate_mode,
        "candidate_names_requested": candidate_names,
        "n_train": int(len(ytr)), "n_val": int(len(yva)), "n_test": int(len(yte)),
        "class_distribution_report": "full_mimic_class_distribution.json",
        "class_distribution": class_distribution_report,
        "untouched_test_metrics": {
            "model": best_name,
            "decision_rule_applied": best_decision_rule,
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
        "validation_decision_rule_reports": decision_rule_reports,
        "selected_decision_rule": best_decision_rule,
        "decision_rule_tuned_on": "validation",
        "binary_high_acuity_detector_report": binary_high_acuity_detector_report,
        "experimental_non_serving_candidates": experimental_results,
        "experimental_non_serving_note": (
            "Legacy TF-IDF text baselines are evaluated for research comparison "
            "only. Serving-eligible raw-text candidates use the v2 "
            "raw_triage_dataframe artefact contract."
        ),
        "selected_model": best_name,
        "selection_rationale": rationale,
        "sklearn_version": sklearn.__version__,
        "note": "Aggregate research metrics only. Not clinically validated.",
    }
    out = paths["output_dir"]
    if test_proba_high is not None:
        curve_report = _write_binary_curve_artifacts(
            out,
            training_run_id=training_run_id,
            y_true=yte,
            proba_high=test_proba_high,
            prefix="selected_model",
        )
    else:
        curve_report = {
            "training_run_id": training_run_id,
            "binary_target": "acuity_1_2_high_acuity_vs_3_5_non_high_acuity",
            "status": "SKIPPED",
            "reason": "selected model did not expose usable probabilities",
        }
        (out / "selected_model_binary_curve_report.json").write_text(
            json.dumps(curve_report, indent=2))
    comparison["selected_model_binary_curve_report"] = curve_report
    assert_no_raw_rows(comparison)

    artefact = out / "mimic_full_acuity_selected.joblib"
    raw_input_schema_hash = _feature_schema_hash(RAW_TRIAGE_INPUT_COLUMNS)
    if best_contract_version == RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION:
        joblib.dump({
            "model": fitted[best_name],
            "artifact_contract_version": RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION,
            "input_type": RAW_TRIAGE_INPUT_TYPE,
            "raw_input_columns": list(RAW_TRIAGE_INPUT_COLUMNS),
            "raw_input_schema_hash": raw_input_schema_hash,
            "structured_feature_names": list(feature_names),
            "structured_feature_schema_hash": feature_schema_hash,
            "sklearn_version": sklearn.__version__,
            "selected_by": "triage_safety_metrics",
            "selection_profile": selection_constraints["profile"],
            "selection_constraints": dict(selection_constraints),
            "decision_rule": best_decision_rule,
            "decision_rule_tuned_on": "validation",
            "test_set_used_for_decision_rule_tuning": False,
            "model_kind": best_name,
            "training_run_id": training_run_id,
            "not_clinically_validated": True,
        }, artefact)
    else:
        joblib.dump({
            "model": fitted[best_name],
            "artifact_contract_version": 1,
            "input_type": "structured_feature_matrix",
            "feature_names": list(feature_names),
            "sklearn_version": sklearn.__version__,
            "selected_by": "triage_safety_metrics",
            "selection_profile": selection_constraints["profile"],
            "selection_constraints": dict(selection_constraints),
            "decision_rule": best_decision_rule,
            "decision_rule_tuned_on": "validation",
            "test_set_used_for_decision_rule_tuning": False,
            "model_kind": best_name,
            "training_run_id": training_run_id,
            "feature_schema_hash": feature_schema_hash,
            "not_clinically_validated": True,
        }, artefact)
    comparison["model_artifact_sha256"] = hashlib.sha256(artefact.read_bytes()).hexdigest()
    comparison["raw_input_schema_hash"] = (
        raw_input_schema_hash
        if best_contract_version == RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION
        else None
    )
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
    provenance["selected_artifact_contract_version"] = int(best_contract_version)
    provenance["selected_input_type"] = comparison["selected_input_type"]
    provenance["selection_profile"] = selection_constraints["profile"]
    provenance["selection_constraints"] = dict(selection_constraints)
    provenance["selected_decision_rule"] = best_decision_rule
    provenance["decision_rule_tuned_on"] = "validation"
    provenance["test_set_used_for_decision_rule_tuning"] = False
    if best_contract_version == RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION:
        provenance["raw_input_columns"] = list(RAW_TRIAGE_INPUT_COLUMNS)
        provenance["raw_input_schema_hash"] = raw_input_schema_hash

    (out / "full_mimic_model_comparison.json").write_text(json.dumps(comparison, indent=2))
    _write_csv(out / "full_mimic_model_comparison.csv", results_sorted, labels)
    _write_auc_comparison_csv(out / "all_models_roc_auc_comparison.csv", results_sorted)
    write_feature_importance_reports(
        out,
        feature_importance_reports,
        selected_model=best_name,
    )
    (out / "full_mimic_class_distribution.json").write_text(
        json.dumps(class_distribution_report, indent=2))
    _write_class_distribution_csv(
        out / "full_mimic_class_distribution.csv", class_distribution_report)
    (out / "full_mimic_threshold_tuning_report.json").write_text(json.dumps({
        "training_run_id": training_run_id,
        "validation_only": True,
        "test_set_used": False,
        "selection_profile": selection_constraints["profile"],
        "selection_constraints": dict(selection_constraints),
        "candidate_decision_rule_reports": decision_rule_reports,
        "selected_model": best_name,
        "selected_decision_rule": best_decision_rule,
    }, indent=2))
    (out / "full_mimic_selected_decision_rule.json").write_text(json.dumps({
        "training_run_id": training_run_id,
        "selected_model": best_name,
        "selected_decision_rule": best_decision_rule,
        "selection_profile": selection_constraints["profile"],
        "selection_constraints": dict(selection_constraints),
        "decision_rule_tuned_on": "validation",
        "test_set_used": False,
    }, indent=2))
    (out / "full_mimic_high_acuity_detector_report.json").write_text(json.dumps(
        binary_high_acuity_detector_report, indent=2))
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
