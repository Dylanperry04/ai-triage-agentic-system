"""
Train a MIMIC-IV-ED Demo acuity model (predicts ESI acuity 1-5 at triage time).

SCOPE / HONESTY: this trains on the PUBLIC DEMO subset only (207 labelled stays),
which is tiny and highly class-imbalanced (e.g. acuity 4 has ~2 examples, acuity
5 has 0). The resulting model is a RESEARCH/DEMONSTRATION artefact only -- it is
not clinically validated and its metrics must be read as illustrative, not as
evidence of real-world performance. This is recorded in the model registry and
surfaced in the UI.

LEAKAGE: features come from build_mimic_demo_labels.py, which excludes every
target/temporal leakage column. This script re-asserts the guard before fitting.

Predicts the ESI `acuity` level. That value is later mapped to an MTS-style
DISPLAY level by app/rules/acuity_mts_mapping.py -- the model itself does NOT
predict Manchester categories (no Manchester labels exist in MIMIC).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import classification_report, accuracy_score
import joblib

from app.config import settings
from app.schemas.mimic_ed import RETROSPECTIVE_OR_LEAKAGE_COLUMNS

NUMERIC_FEATURES = ["temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain"]
CATEGORICAL_FEATURES = ["chiefcomplaint", "gender", "race", "arrival_transport"]
ALL_FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
LABEL = "acuity"

MODEL_NAME = "mimic_demo_acuity_rf_v1"


def _load() -> pd.DataFrame:
    path = settings.processed_dir / "mimic_demo_acuity_features.jsonl"
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    recs = []
    for r in rows:
        if r["acuity_label"] is None:
            continue  # cannot train without a label
        rec = dict(r["features"])
        rec["acuity"] = r["acuity_label"]
        recs.append(rec)
    return pd.DataFrame(recs)


def _assert_no_leakage(cols: list[str]) -> None:
    leaked = sorted(set(cols) & set(RETROSPECTIVE_OR_LEAKAGE_COLUMNS))
    if leaked or LABEL in cols:
        raise ValueError(f"LEAKAGE GUARD TRIPPED in training features: {leaked or [LABEL]}")


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in NUMERIC_FEATURES:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in CATEGORICAL_FEATURES:
        df[c] = df[c].astype("object").where(df[c].notna(), "MISSING").astype(str)
    return df


def train() -> dict:
    df = _coerce_numeric(_load())
    _assert_no_leakage(ALL_FEATURES)

    X = df[ALL_FEATURES]
    y = df[LABEL].astype(int)

    pre = ColumnTransformer([
        ("num", SimpleImputer(strategy="median"), NUMERIC_FEATURES),
        ("cat", Pipeline([
            ("imp", SimpleImputer(strategy="constant", fill_value="MISSING")),
            ("oh", OneHotEncoder(handle_unknown="ignore")),
        ]), CATEGORICAL_FEATURES),
    ])
    clf = Pipeline([
        ("pre", pre),
        ("rf", RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=2,
            class_weight="balanced", random_state=42,
        )),
    ])

    # Honest evaluation: stratified CV out-of-fold predictions. Classes with too
    # few members for the chosen folds are why we keep folds small.
    class_counts = y.value_counts()
    min_class = int(class_counts.min())
    n_splits = max(2, min(3, min_class))  # never more folds than the rarest class
    report = {}
    cv_accuracy = None
    if min_class >= 2 and y.nunique() >= 2:
        skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        try:
            oof = cross_val_predict(clf, X, y, cv=skf)
            cv_accuracy = float(accuracy_score(y, oof))
            report = classification_report(y, oof, output_dict=True, zero_division=0)
        except ValueError as exc:
            report = {"cv_note": f"CV skipped: {exc}"}

    # Fit final model on all labelled rows.
    clf.fit(X, y)

    models_dir = settings.data_root / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / f"{MODEL_NAME}.joblib"
    joblib.dump(clf, model_path)

    summary = {
        "model_name": MODEL_NAME,
        "model_path": str(model_path),
        "dataset": "MIMIC-IV-ED-Demo-v2.2",
        "target": "acuity",
        "n_train_rows": int(len(df)),
        "class_distribution": {str(k): int(v) for k, v in class_counts.items()},
        "cv_n_splits": n_splits,
        "cv_accuracy": cv_accuracy,
        "features_numeric": NUMERIC_FEATURES,
        "features_categorical": CATEGORICAL_FEATURES,
        "leakage_excluded": True,
        "clinical_use": False,
        "human_review_required": True,
        "small_data_warning": (
            "Trained on 207 public-demo rows with severe class imbalance "
            "(acuity 5 absent, acuity 4 ~2 rows). Metrics are illustrative only, "
            "NOT evidence of clinical performance."
        ),
    }
    eval_path = settings.processed_dir / "mimic_acuity_model_evaluation.json"
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "classification_report": report}, f, indent=2)
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    train()
