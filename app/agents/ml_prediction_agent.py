"""
ML Research Prediction Agent for full MIMIC-IV-ED.

Loads a full-MIMIC acuity artefact from MIMIC_FULL_MODEL_PATH and returns:
  - predicted MIMIC/ESI acuity estimate (1-5)
  - unavailable/fail-closed status when no compatible artefact is configured

These are not Manchester triage labels and are not clinical decisions.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import joblib
import numpy as np

from app.config import settings
from app.schemas.internal import TriageTimeInput
from app.schemas.workflow import MLPredictionResult
from ml_training.feature_engineering import FEATURE_NAMES, extract_features_from_row


def _load_registry() -> Optional[dict]:
    path = settings.model_registry_path
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _resolve_model_path(raw_path: str) -> Path:
    p = Path(raw_path)
    return p if p.is_absolute() else settings.models_dir / p


def _triage_input_to_row(t: TriageTimeInput) -> dict:
    return {
        "age": t.age,
        "gender": t.gender,
        "group_code": t.group_code,
        "patients_per_hour": t.patients_per_hour,
        "arrival_transport": t.arrival_transport,
        "arrival_mode_code": t.arrival_mode_code,
        "injury_code": t.injury_code,
        "mental_code": t.mental_code,
        "chiefcomplaint": t.chiefcomplaint,
        "temperature": t.temperature,
        "temperature_unit": t.temperature_unit,
        "heartrate": t.heartrate,
        "resprate": t.resprate,
        "o2sat": t.o2sat,
        "sbp": t.sbp,
        "dbp": t.dbp,
        "pain": t.pain,
        "pain_present": t.pain_present,
        "nrs_pain": t.nrs_pain,
    }


def _predict_proba_safe(model, X):
    try:
        if hasattr(model, "predict_proba"):
            return model.predict_proba(X)
    except Exception:
        return None
    return None


def _class_probability_dict(model, probabilities) -> dict[str, float]:
    if probabilities is None:
        return {}
    classes = getattr(model, "classes_", None)
    if classes is None:
        classes = list(range(probabilities.shape[1]))
    return {str(int(cls)): float(probabilities[0, i]) for i, cls in enumerate(classes)}


def _prob_for_class(model, probabilities, wanted_class: int) -> Optional[float]:
    if probabilities is None:
        return None
    classes = getattr(model, "classes_", None)
    if classes is None:
        return None
    for i, cls in enumerate(classes):
        if int(cls) == int(wanted_class):
            return float(probabilities[0, i])
    return None


def run_ml_prediction(triage_input: TriageTimeInput) -> MLPredictionResult:
    """
    Dispatch ML prediction by source dataset.

    The ONLY real prediction path is full MIMIC-IV-ED (credentialed), routed to the
    full-MIMIC model via MIMIC_FULL_MODEL_PATH and failing closed when that model
    is not present/compatible. Demo/KTAS datasets are no longer prediction sources
    and are never served by the live system; any non-full-MIMIC dataset withholds
    a prediction (no extrapolation, clinician review required).
    """
    ds = triage_input.source_dataset
    if ds == "MIMIC-IV-ED-Full-v2.2":
        return _run_full_mimic_prediction(triage_input)
    # Every other dataset (including the retired demo/KTAS sets and any unknown
    # label) has no live model. Withhold rather than extrapolate.
    return MLPredictionResult(
        model_name="no_model_for_dataset",
        prediction_available=False,
        model_note=(
            f"Dataset '{ds}' is not a supported prediction source. The only live "
            "prediction path is full MIMIC-IV-ED (credentialed). No estimate is "
            "shown; clinician review required. The deterministic safety review and "
            "audit logging still run."
        ),
    )


def _run_full_mimic_prediction(triage_input: TriageTimeInput) -> MLPredictionResult:
    """Route full-MIMIC cases to the full-MIMIC model. The full model is trained on
    the credentialed environment and is NOT bundled here; if its artefact is not
    present/configured, fail closed (no estimate, clinician review required) — never
    fall back to the demo or KTAS model."""
    import os
    from pathlib import Path
    model_path = os.environ.get("MIMIC_FULL_MODEL_PATH")
    resolved_model_path = Path(model_path).expanduser() if model_path else None
    if not resolved_model_path or not resolved_model_path.exists():
        return MLPredictionResult(
            model_name="full_mimic_model_unavailable",
            prediction_available=False,
            model_note=(
                "Full MIMIC-IV-ED model artefact is not available in this "
                "environment (set MIMIC_FULL_MODEL_PATH on the credentialed/approved "
                "environment). No estimate is shown; clinician review required. The "
                "demo/KTAS models are never substituted for full-MIMIC cases."
            ),
        )
    expected_sha = os.environ.get("MIMIC_FULL_MODEL_SHA256", "").strip().lower()
    if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true" and not expected_sha:
        return MLPredictionResult(
            model_name="full_mimic_model_hash_required",
            prediction_available=False,
            model_note=(
                "PATIENT_DATA_MODE requires MIMIC_FULL_MODEL_SHA256 before loading "
                "a joblib model artefact. Refusing to deserialize an unpinned model; "
                "clinician review required."
            ),
        )
    if expected_sha:
        try:
            import hashlib
            h = hashlib.sha256(resolved_model_path.read_bytes()).hexdigest()
        except Exception as exc:
            return MLPredictionResult(
                model_name="full_mimic_model_hash_unreadable",
                prediction_available=False,
                model_note=(f"Could not hash the model artefact ({exc}); refusing "
                            "to use it. Clinician review required."),
            )
        if h != expected_sha:
            return MLPredictionResult(
                model_name="full_mimic_model_hash_mismatch",
                prediction_available=False,
                model_note=(
                    "Full MIMIC model artefact hash does not match "
                    "MIMIC_FULL_MODEL_SHA256. Refusing to use a model whose "
                    "provenance cannot be confirmed; clinician review required."
                ),
            )
    # Artefact compatibility check before use. FAIL CLOSED: if the check itself
    # errors, refuse to use the model (an unverifiable artefact must not predict).
    try:
        from ml_training.full_mimic.check_artifact_compatibility import check_artifact
        compat = check_artifact(resolved_model_path)
    except Exception as exc:
        return MLPredictionResult(
            model_name="full_mimic_model_check_failed",
            prediction_available=False,
            model_note=(
                f"Full MIMIC model artefact could not be verified ({exc}). Refusing "
                "to use an unverifiable artefact; clinician review required."
            ),
        )
    if not compat["compatible"]:
        return MLPredictionResult(
            model_name="full_mimic_model_incompatible",
            prediction_available=False,
            model_note=(
                f"Full MIMIC model artefact is incompatible: {compat['reason']}. "
                "Refusing to use it; clinician review required."
            ),
        )

    # Load and predict. Verify the artefact's feature names match the serving
    # feature contract EXACTLY (name AND order) before predicting, so a model
    # trained on a different feature set fails closed instead of mispredicting.
    try:
        import joblib
        from ml_training.feature_engineering import extract_features_from_row, FEATURE_NAMES
        import numpy as np
        bundle = joblib.load(resolved_model_path)
        model = bundle["model"] if isinstance(bundle, dict) else bundle
        artefact_features = bundle.get("feature_names") if isinstance(bundle, dict) else None
        if artefact_features is None:
            return MLPredictionResult(
                model_name="full_mimic_model_no_feature_names",
                prediction_available=False,
                model_note=("Model artefact does not record its feature_names; cannot "
                            "verify train/serve feature parity. Refusing to use it; "
                            "clinician review required."),
            )
        if list(artefact_features) != list(FEATURE_NAMES):
            return MLPredictionResult(
                model_name="full_mimic_model_feature_mismatch",
                prediction_available=False,
                model_note=("Model artefact feature names/order do not match the "
                            "serving feature contract. Refusing to use a model with a "
                            "different feature set; retrain on the current features. "
                            "Clinician review required."),
            )
        feats = extract_features_from_row(triage_input.model_dump(mode="json"))
        X = np.array([[feats[f] for f in FEATURE_NAMES]], dtype=float)
        pred = int(np.asarray(model.predict(X)).ravel()[0])
        if pred not in {1, 2, 3, 4, 5}:
            return MLPredictionResult(
                model_name="full_mimic_model_invalid_prediction",
                prediction_available=False,
                model_note=(
                    f"Full MIMIC model returned out-of-range acuity {pred!r}; "
                    "refusing to display it. Clinician review required."
                ),
            )
        return MLPredictionResult(
            model_name="mimic_full_acuity_model",
            prediction_available=True,
            predicted_mimic_acuity=pred,
            prediction_scale="MIMIC_ACUITY_1_5",
            model_note="Full MIMIC-IV-ED research model. Not clinically validated; "
                       "clinician review required.",
        )
    except Exception as exc:
        return MLPredictionResult(
            model_name="full_mimic_model_error",
            prediction_available=False,
            model_note=f"Full MIMIC model could not produce an estimate: {exc}. "
                       "Clinician review required.",
        )
