"""Feature-importance reporting for full-MIMIC model comparison.

The extractor is conservative: it reports coefficients or native importances
only when the transformed feature space can be named. SVD-compressed text models
are explicitly marked as not directly interpretable rather than pretending that
latent components are clinical fields.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np


FORBIDDEN_IMPORTANCE_TERMS = (
    "acuity",
    "disposition",
    "diagnosis",
    "diagnos",
    "mortality",
    "death",
    "expire",
    "outtime",
    "hadm_id",
    "medrecon",
    "pyxis",
    "vitalsign",
    "charttime",
    "future",
)


def _final_estimator(model: Any) -> Any:
    est = model
    if hasattr(est, "named_steps"):
        est = est.named_steps.get("estimator", est)
    if hasattr(est, "estimator_"):
        est = est.estimator_
    return est


def _transformed_feature_names(model: Any, base_feature_names: Iterable[str]) -> list[str] | None:
    if hasattr(model, "named_steps") and "svd" in model.named_steps:
        return None
    if hasattr(model, "named_steps") and "features" in model.named_steps:
        features = model.named_steps["features"]
        getter = getattr(features, "get_feature_names_out", None)
        if getter is not None:
            try:
                return [str(v) for v in getter()]
            except Exception:
                return None
    return [str(v) for v in base_feature_names]


def _logical_field(feature_name: str) -> str:
    name = str(feature_name)
    for prefix in ("structured__", "word_raw__", "word_normalised__", "char_normalised__"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    if name.startswith("tfidf__"):
        name = name[len("tfidf__"):]
    if name.startswith("arrival_"):
        return "arrival_transport"
    if name.startswith("sex_"):
        return "gender"
    if name.startswith("cc_"):
        return "chiefcomplaint_keyword_group"
    if name.startswith("chiefcomplaint") or "chiefcomplaint" in name:
        return "chiefcomplaint_text"
    if name.startswith("pain_") or name in {"nrs_pain", "pain_present"}:
        return "pain"
    if name.endswith("_missing") or name.endswith("_outlier"):
        return name.rsplit("_", 1)[0]
    return name


def _assert_no_leakage_feature_names(names: Iterable[str]) -> None:
    bad = [
        name for name in names
        if any(term in str(name).lower() for term in FORBIDDEN_IMPORTANCE_TERMS)
    ]
    if bad:
        raise ValueError(f"feature importance contains leakage-like feature names: {bad[:10]}")


def _importance_values(estimator: Any) -> tuple[np.ndarray | None, str | None]:
    coef = getattr(estimator, "coef_", None)
    if coef is not None:
        return np.asarray(coef, dtype=float), "coefficient"
    importances = getattr(estimator, "feature_importances_", None)
    if importances is not None:
        return np.asarray(importances, dtype=float), "feature_importances"
    getter = getattr(estimator, "get_feature_importance", None)
    if getter is not None:
        try:
            return np.asarray(getter(), dtype=float), "native_feature_importance"
        except Exception:
            return None, None
    return None, None


def extract_feature_importance_report(
    *,
    model_name: str,
    model: Any,
    base_feature_names: Iterable[str],
    labels: Iterable[int],
    top_n: int = 50,
) -> dict[str, Any]:
    if "svd" in model_name.lower():
        return {
            "model_name": model_name,
            "status": "not_directly_interpretable",
            "reason": (
                "This candidate uses TF-IDF followed by TruncatedSVD. The model "
                "sees latent SVD components, so raw complaint tokens cannot be "
                "reported as direct clinical feature importances."
            ),
            "top_features": [],
            "class_specific": {},
        }
    feature_names = _transformed_feature_names(model, base_feature_names)
    if not feature_names:
        return {
            "model_name": model_name,
            "status": "unavailable",
            "reason": "Transformed feature names are unavailable for this estimator.",
            "top_features": [],
            "class_specific": {},
        }
    _assert_no_leakage_feature_names(feature_names)
    estimator = _final_estimator(model)
    values, method = _importance_values(estimator)
    if values is None:
        return {
            "model_name": model_name,
            "status": "unavailable",
            "reason": "Estimator does not expose coefficients or reliable native importances.",
            "top_features": [],
            "class_specific": {},
        }
    if values.ndim == 1:
        if len(values) != len(feature_names):
            return {
                "model_name": model_name,
                "status": "unavailable",
                "reason": "Importance vector length does not match transformed feature count.",
                "top_features": [],
                "class_specific": {},
            }
        global_scores = np.abs(values)
        class_specific: dict[str, list[dict[str, Any]]] = {}
    elif values.ndim == 2:
        if values.shape[1] != len(feature_names):
            return {
                "model_name": model_name,
                "status": "unavailable",
                "reason": "Coefficient matrix width does not match transformed feature count.",
                "top_features": [],
                "class_specific": {},
            }
        global_scores = np.max(np.abs(values), axis=0)
        class_specific = {}
        label_list = [str(label) for label in labels]
        for idx, label in enumerate(label_list[: values.shape[0]]):
            row = values[idx]
            order = np.argsort(np.abs(row))[::-1][:top_n]
            class_specific[label] = [
                {
                    "feature": feature_names[i],
                    "logical_field": _logical_field(feature_names[i]),
                    "importance": float(abs(row[i])),
                    "signed_value": float(row[i]),
                    "method": method,
                }
                for i in order
            ]
    else:
        return {
            "model_name": model_name,
            "status": "unavailable",
            "reason": "Unsupported importance array shape.",
            "top_features": [],
            "class_specific": {},
        }
    order = np.argsort(global_scores)[::-1][:top_n]
    top_features = [
        {
            "feature": feature_names[i],
            "logical_field": _logical_field(feature_names[i]),
            "importance": float(global_scores[i]),
            "method": method,
        }
        for i in order
    ]
    logical_totals: dict[str, float] = {}
    for idx, score in enumerate(global_scores):
        field = _logical_field(feature_names[idx])
        logical_totals[field] = logical_totals.get(field, 0.0) + float(abs(score))
    logical_fields = [
        {"logical_field": key, "importance": value}
        for key, value in sorted(logical_totals.items(), key=lambda item: item[1], reverse=True)[:top_n]
    ]
    return {
        "model_name": model_name,
        "status": "available",
        "method": method,
        "top_features": top_features,
        "top_logical_fields": logical_fields,
        "class_specific": class_specific,
        "notes": [
            "Importances are model-specific research diagnostics, not clinical causality.",
            "One-hot and keyword features are aggregated to logical fields where possible.",
        ],
    }


def write_feature_importance_reports(
    out_dir: Path,
    reports: dict[str, dict[str, Any]],
    *,
    selected_model: str | None = None,
) -> None:
    payload = {
        "selected_model": selected_model,
        "reports": reports,
        "not_clinically_validated": True,
    }
    (out_dir / "full_mimic_feature_importance.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    rows = []
    for model_name, report in reports.items():
        for rank, item in enumerate(report.get("top_features") or [], start=1):
            rows.append({
                "model_name": model_name,
                "status": report.get("status"),
                "rank": rank,
                "feature": item.get("feature"),
                "logical_field": item.get("logical_field"),
                "importance": item.get("importance"),
                "method": item.get("method") or report.get("method"),
            })
        if not report.get("top_features"):
            rows.append({
                "model_name": model_name,
                "status": report.get("status"),
                "rank": None,
                "feature": None,
                "logical_field": None,
                "importance": None,
                "method": report.get("method"),
            })
    with (out_dir / "full_mimic_feature_importance.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "model_name",
                "status",
                "rank",
                "feature",
                "logical_field",
                "importance",
                "method",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    if selected_model and selected_model in reports:
        (out_dir / "selected_model_feature_importance.json").write_text(
            json.dumps(reports[selected_model], indent=2),
            encoding="utf-8",
        )
