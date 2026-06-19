"""
KTAS research ML training pipeline.

Trains models on the supplied public Kaggle KTAS dataset.

Main models:
  1. KTAS expert classifier: predicts KTAS_expert 1-5.
  2. Emergency classifier: predicts KTAS emergency status, where KTAS 1-3 is
     emergency and KTAS 4-5 is non-emergency per dataset documentation.

These are research models only. They do not predict Manchester triage and must
not be used for autonomous clinical decisions.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import joblib
import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.naive_bayes import GaussianNB
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, label_binarize

from app.config import settings
from ml_training.feature_engineering import FEATURE_NAMES, build_training_dataframe

warnings.filterwarnings("ignore", category=UserWarning)


def _try_import_xgb():
    try:
        from xgboost import XGBClassifier
        return XGBClassifier
    except Exception:
        return None


def _try_import_lgbm():
    try:
        from lightgbm import LGBMClassifier
        return LGBMClassifier
    except Exception:
        return None


class _XGBLabelWrapper:
    """Wrapper so XGBoost can train on KTAS class labels 1..5."""
    def __init__(self, **params):
        self.params = params
        self.model = None
        self.classes_ = None
        self._class_to_idx = {}
        self._idx_to_class = {}

    def fit(self, X, y):
        XGBClassifier = _try_import_xgb()
        if XGBClassifier is None:
            raise RuntimeError("xgboost is not installed")
        y = np.asarray(y, dtype=int)
        self.classes_ = np.array(sorted(set(y)))
        self._class_to_idx = {c: i for i, c in enumerate(self.classes_)}
        self._idx_to_class = {i: c for c, i in self._class_to_idx.items()}
        y_idx = np.array([self._class_to_idx[int(v)] for v in y])
        self.model = XGBClassifier(**self.params)
        self.model.fit(X, y_idx)
        return self

    def predict(self, X):
        pred = self.model.predict(X)
        return np.array([self._idx_to_class[int(i)] for i in pred])

    def predict_proba(self, X):
        return self.model.predict_proba(X)

    def get_params(self, deep=True):
        return dict(self.params)

    def set_params(self, **params):
        self.params.update(params)
        return self


def _core_models(task: str, include_optional_boosters: bool = False):
    is_binary = task == "binary"
    models = [
        ("DummyMostFrequent", DummyClassifier(strategy="most_frequent")),
        (
            "LogisticRegression",
            Pipeline([
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                    random_state=42,
                )),
            ]),
        ),
        (
            "RandomForest",
            RandomForestClassifier(
                n_estimators=120,
                max_depth=10,
                min_samples_leaf=4,
                class_weight="balanced",
                random_state=42,
                n_jobs=-1,
            ),
        ),
        ("GaussianNB", GaussianNB()),
    ]
    if include_optional_boosters:
        models.append((
            "GradientBoosting",
            GradientBoostingClassifier(
                n_estimators=60,
                max_depth=2,
                learning_rate=0.05,
                random_state=42,
            ),
        ))
        XGBClassifier = _try_import_xgb()
        if XGBClassifier is not None:
            if is_binary:
                models.append((
                    "XGBoost",
                    XGBClassifier(
                        n_estimators=250,
                        max_depth=4,
                        learning_rate=0.05,
                        eval_metric="logloss",
                        random_state=42,
                        n_jobs=-1,
                    ),
                ))
            else:
                models.append((
                    "XGBoost",
                    _XGBLabelWrapper(
                        n_estimators=250,
                        max_depth=4,
                        learning_rate=0.05,
                        eval_metric="mlogloss",
                        random_state=42,
                        n_jobs=-1,
                    ),
                ))
        LGBMClassifier = _try_import_lgbm()
        if LGBMClassifier is not None:
            models.append((
                "LightGBM",
                LGBMClassifier(
                    n_estimators=250,
                    max_depth=4,
                    learning_rate=0.05,
                    random_state=42,
                    n_jobs=-1,
                    verbose=-1,
                    class_weight="balanced" if is_binary else None,
                ),
            ))
    return models


def _macro_auroc(y_true, y_proba, classes) -> float:
    try:
        if len(classes) < 2:
            return float("nan")
        if len(classes) == 2 and y_proba.shape[1] == 2:
            return float(roc_auc_score(y_true, y_proba[:, 1]))
        y_bin = label_binarize(y_true, classes=classes)
        return float(roc_auc_score(y_bin, y_proba, average="macro", multi_class="ovr"))
    except Exception:
        return float("nan")


def _under_over_rates(y_true, y_pred) -> tuple[float, float]:
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    # KTAS: 1 is most urgent, 5 least urgent.
    under = np.mean(y_pred > y_true)  # predicted less urgent than expert
    over = np.mean(y_pred < y_true)   # predicted more urgent than expert
    return float(under), float(over)


def _binary_false_negative_rate(y_true, y_pred) -> float:
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    positives = y_true == 1
    if positives.sum() == 0:
        return float("nan")
    return float(np.mean(y_pred[positives] == 0))


def _make_cv(y) -> StratifiedKFold:
    _, counts = np.unique(y, return_counts=True)
    min_class = int(counts.min())
    n_splits = min(5, min_class)
    if n_splits < 2:
        raise ValueError("Insufficient class counts for stratified cross-validation")
    return StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)


def _evaluate_model(name: str, model, X, y, task: str) -> dict[str, Any]:
    classes = sorted(np.unique(y).astype(int).tolist())
    cv = _make_cv(y)
    print(f"  Training {name} ({task}) with {cv.n_splits}-fold CV...")
    y_pred = cross_val_predict(model, X, y, cv=cv, method="predict")
    y_proba = None
    auroc = float("nan")
    try:
        y_proba = cross_val_predict(model, X, y, cv=cv, method="predict_proba")
        auroc = _macro_auroc(y, y_proba, classes)
    except Exception:
        pass

    acc = float(accuracy_score(y, y_pred))
    bal_acc = float(balanced_accuracy_score(y, y_pred))
    macro_f1 = float(f1_score(y, y_pred, average="macro", zero_division=0))
    weighted_f1 = float(f1_score(y, y_pred, average="weighted", zero_division=0))
    cm = confusion_matrix(y, y_pred, labels=classes).tolist()
    report = classification_report(y, y_pred, labels=classes, output_dict=True, zero_division=0)

    metrics: dict[str, Any] = {
        "model_name": name,
        "task": task,
        "classes": classes,
        "accuracy": acc,
        "balanced_accuracy": bal_acc,
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
        "macro_auroc": auroc,
        "confusion_matrix": cm,
        "classification_report": report,
    }
    if task == "ktas_5_class":
        under, over = _under_over_rates(y, y_pred)
        metrics["under_triage_rate"] = under
        metrics["over_triage_rate"] = over
        metrics["selection_score"] = macro_f1 - (1.5 * under)
        print(f"    macro-F1={macro_f1:.3f} under-triage={under:.3f} score={metrics['selection_score']:.3f}")
    else:
        fn = _binary_false_negative_rate(y, y_pred)
        metrics["false_negative_emergency_rate"] = fn
        metrics["selection_score"] = (auroc if not np.isnan(auroc) else macro_f1) - (1.5 * fn if not np.isnan(fn) else 0)
        print(f"    AUROC={auroc:.3f} macro-F1={macro_f1:.3f} FN-emergency={fn:.3f} score={metrics['selection_score']:.3f}")
    return metrics


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _save_model(model, X, y, output_path: Path) -> None:
    model.fit(X, y)
    joblib.dump(model, output_path)


def train_all_models(data_path: Path, output_dir: Path, include_optional_boosters: bool = False) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    if not data_path.exists():
        raise FileNotFoundError(
            f"Training labels not found: {data_path}\n"
            "Run: python scripts/run_ktas_pipeline.py"
        )

    records = _load_jsonl(data_path)
    df = build_training_dataframe(records)
    df = df.dropna(subset=["label_ktas_expert", "label_ktas_emergency"])
    if len(df) < 50:
        raise ValueError(f"Insufficient data for KTAS training: {len(df)} rows")

    X = df[FEATURE_NAMES].values.astype(float)
    y_ktas = df["label_ktas_expert"].values.astype(int)
    y_emergency = df["label_ktas_emergency"].values.astype(int)

    print("\n============================================================")
    print("Kaggle KTAS Research ML Training")
    print(f"Rows: {len(df)}")
    print(f"Features: {len(FEATURE_NAMES)}")
    print(f"KTAS distribution: {dict(zip(*np.unique(y_ktas, return_counts=True)))}")
    print(f"Emergency distribution: {dict(zip(*np.unique(y_emergency, return_counts=True)))}")
    print("============================================================\n")

    ktas_results = []
    for name, model in _core_models("multiclass", include_optional_boosters):
        try:
            metrics = _evaluate_model(name, model, X, y_ktas, "ktas_5_class")
            ktas_results.append((name, model, metrics))
        except Exception as exc:
            print(f"  FAILED {name} KTAS: {type(exc).__name__}: {exc}")
    if not ktas_results:
        raise RuntimeError("All KTAS models failed")

    emergency_results = []
    for name, model in _core_models("binary", include_optional_boosters):
        try:
            metrics = _evaluate_model(name, model, X, y_emergency, "ktas_emergency_binary")
            emergency_results.append((name, model, metrics))
        except Exception as exc:
            print(f"  FAILED {name} emergency: {type(exc).__name__}: {exc}")
    if not emergency_results:
        raise RuntimeError("All emergency models failed")

    best_ktas_name, best_ktas_model, best_ktas_metrics = max(ktas_results, key=lambda x: x[2]["selection_score"])
    best_em_name, best_em_model, best_em_metrics = max(emergency_results, key=lambda x: x[2]["selection_score"])

    timestamp = datetime.now(timezone.utc).isoformat()
    version = f"ktas.1.0.{int(datetime.now(timezone.utc).timestamp())}"

    best_ktas_path = output_dir / f"{best_ktas_name}_ktas_5class.pkl"
    best_em_path = output_dir / f"{best_em_name}_ktas_emergency.pkl"
    _save_model(best_ktas_model, X, y_ktas, best_ktas_path)
    _save_model(best_em_model, X, y_emergency, best_em_path)

    registry = {
        "created_at_utc": timestamp,
        "version": version,
        "dataset": "Kaggle-KTAS",
        "training_data": str(data_path),
        "n_samples": int(len(df)),
        "feature_names": FEATURE_NAMES,
        "blocked_leakage_features": [
            "KTAS_RN", "KTAS_expert", "mistriage", "Error_group", "Diagnosis in ED",
            "Disposition", "Length of stay_min", "KTAS duration_min",
        ],
        "research_note": (
            "Models trained on public Kaggle KTAS data using KTAS_expert as the research target. "
            "KTAS is not Manchester Triage Scale. No KTAS-to-Manchester mapping is implemented. "
            "Not for clinical use; human review required."
        ),
        "best_model": {
            "name": best_ktas_name,
            "version": version,
            "task": "ktas_5_class",
            "path": best_ktas_path.name,
            "feature_names": FEATURE_NAMES,
            "metrics": best_ktas_metrics,
        },
        "best_ktas_model": {
            "name": best_ktas_name,
            "version": version,
            "task": "ktas_5_class",
            "path": best_ktas_path.name,
            "feature_names": FEATURE_NAMES,
            "metrics": best_ktas_metrics,
        },
        "best_emergency_model": {
            "name": best_em_name,
            "version": version,
            "task": "ktas_emergency_binary",
            "path": best_em_path.name,
            "feature_names": FEATURE_NAMES,
            "metrics": best_em_metrics,
        },
        "all_ktas_models": [{"name": n, "metrics": m} for n, _, m in ktas_results],
        "all_emergency_models": [{"name": n, "metrics": m} for n, _, m in emergency_results],
    }

    registry_path = output_dir / "registry.json"
    with registry_path.open("w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, default=str)

    print("\n============================================================")
    print(f"Best KTAS 5-class model: {best_ktas_name}")
    print(f"  macro-F1: {best_ktas_metrics['macro_f1']:.3f}")
    print(f"  under-triage rate: {best_ktas_metrics['under_triage_rate']:.3f}")
    print(f"Best emergency model: {best_em_name}")
    print(f"  macro-F1: {best_em_metrics['macro_f1']:.3f}")
    print(f"  false-negative emergency rate: {best_em_metrics['false_negative_emergency_rate']:.3f}")
    print(f"Registry saved to: {registry_path}")
    print("NOT FOR CLINICAL USE. KTAS is not Manchester.")
    print("============================================================\n")
    return registry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train KTAS research models.")
    parser.add_argument("--data", type=Path, default=settings.processed_dir / "ktas_labels.jsonl")
    parser.add_argument("--output", type=Path, default=settings.models_dir)
    parser.add_argument("--include-optional-boosters", action="store_true")
    args = parser.parse_args()
    train_all_models(args.data, args.output, include_optional_boosters=args.include_optional_boosters)
