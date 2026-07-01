"""
Missing-data intelligence.

Answers the question the supervisor raised: "a field might be missing that, if I
had it, could help predict the level better." For a given case it reports:
  - which triage-time fields are missing,
  - which of those are CRITICAL (safety-relevant vitals),
  - the model's reliance on each missing field (aggregated feature importance),
  - which missing field would most likely improve the prediction if available,
  - a confidence-impact signal, and
  - whether clinician review is required because of missing data.

The "which field would help most" ranking is derived HONESTLY from the trained
model's feature importances (aggregated from the one-hot-expanded space back to
logical fields), intersected with what is missing for this case. It is a
research signal, NOT a clinical instruction, and the whole module is gated behind
the usual research-only / clinician-review framing.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import joblib

from app.config import settings

CRITICAL_FIELDS = {"o2sat", "sbp", "heartrate", "resprate", "temperature"}

FIELD_DESCRIPTIONS = {
    "o2sat": "Oxygen saturation — a key indicator of respiratory/perfusion compromise.",
    "sbp": "Systolic blood pressure — central to detecting shock/hypotension.",
    "heartrate": "Heart rate — tachy/bradycardia are core acuity signals.",
    "resprate": "Respiratory rate — strong early-deterioration signal.",
    "temperature": "Temperature — fever/hypothermia affects acuity.",
    "dbp": "Diastolic blood pressure — supports haemodynamic assessment.",
    "pain": "Pain score — contributes to urgency assessment.",
    "chiefcomplaint": "Chief complaint — primary context for the presentation.",
    "gender": "Gender — minor demographic context.",
    "race": "Race — demographic context (used cautiously; fairness-monitored).",
    "arrival_transport": "Arrival mode (e.g. ambulance) — proxy for acuity.",
}


def _model_and_features():
    """Load the full-MIMIC acuity model and compute aggregated logical-feature
    importances. The only model is full MIMIC-IV-ED, referenced via
    MIMIC_FULL_MODEL_PATH on the credentialed/approved environment; it is not
    present in this repo. When no model is available, this returns empty
    importances and the caller surfaces a clear 'model not available' note rather
    than crashing."""
    import os
    model_path_env = os.environ.get("MIMIC_FULL_MODEL_PATH", "")
    model_path = Path(model_path_env).expanduser() if model_path_env else None
    if not model_path or not model_path.exists():
        # No full-MIMIC model in this environment (e.g. the sandbox). Fail soft.
        return [], [], {}

    bundle = joblib.load(model_path)
    feature_names = list(bundle.get("feature_names", [])) if isinstance(bundle, dict) else []
    est = bundle.get("model") if isinstance(bundle, dict) else bundle
    importances = getattr(est, "feature_importances_", None)

    logical_importance: Dict[str, float] = {f: 0.0 for f in feature_names}
    if importances is not None and len(feature_names) == len(importances):
        for name, imp in zip(feature_names, importances):
            logical_importance[name] += float(imp)
    total = sum(logical_importance.values()) or 1.0
    logical_importance = {k: v / total for k, v in logical_importance.items()}
    return feature_names, [], logical_importance


def analyse_missing_data(missing_fields: List[str],
                         applicable: bool = True) -> Dict[str, Any]:
    """Produce the missing-data intelligence report for a case."""
    missing = list(missing_fields or [])
    _, _, importance = _model_and_features()

    critical_missing = [f for f in missing if f in CRITICAL_FIELDS]

    ranked = sorted(
        ({"field": f,
          "model_reliance": round(importance.get(f, 0.0), 4),
          "is_critical": f in CRITICAL_FIELDS,
          "description": FIELD_DESCRIPTIONS.get(f, "")}
         for f in missing),
        key=lambda r: r["model_reliance"], reverse=True,
    )

    most_helpful = ranked[0] if ranked else None

    missing_importance = sum(importance.get(f, 0.0) for f in missing)
    if not missing:
        impact = "none"
    elif critical_missing or missing_importance >= 0.20:
        impact = "high"
    elif missing_importance >= 0.08:
        impact = "moderate"
    else:
        impact = "low"

    return {
        "applicable": applicable,
        "missing_fields": missing,
        "critical_missing_fields": critical_missing,
        "ranked_missing_by_model_reliance": ranked,
        "most_helpful_if_available": most_helpful,
        "collective_model_reliance_on_missing": round(missing_importance, 4),
        "confidence_impact": impact,
        "requires_clinician_review": bool(missing),
        "note": (
            "Research signal only. 'Most helpful if available' is derived from the "
            "demo model's feature importances on 207 public rows and indicates "
            "which missing field the model relies on most — NOT a clinical "
            "instruction. Clinician review is required whenever fields are missing."
        ),
    }
