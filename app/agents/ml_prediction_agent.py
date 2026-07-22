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
        "pain_raw": t.pain_raw,
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


def _aligned_probability_row(model, probabilities, labels: list[int]):
    if probabilities is None:
        return None
    row = np.asarray(probabilities, dtype=float)
    if row.ndim == 2:
        row = row[0]
    if row.ndim != 1:
        return None
    classes = getattr(model, "classes_", None)
    if classes is None and len(row) == len(labels):
        classes = labels
    if classes is None or len(classes) != len(row):
        return None
    aligned = np.zeros(len(labels), dtype=float)
    label_to_index = {int(label): i for i, label in enumerate(labels)}
    for src_i, cls in enumerate(classes):
        dst_i = label_to_index.get(int(cls))
        if dst_i is not None:
            aligned[dst_i] = row[src_i]
    return aligned


def _apply_artifact_decision_rule(model, pred: int, probabilities, decision_rule) -> int:
    if not decision_rule or decision_rule.get("type") == "argmax":
        return int(pred)
    rule_type = decision_rule.get("type")
    labels = [int(v) for v in decision_rule.get("labels", [1, 2, 3, 4, 5])]
    aligned = _aligned_probability_row(model, probabilities, labels)
    if aligned is None:
        raise ValueError(f"decision rule {rule_type!r} requires class probabilities")
    if rule_type == "high_acuity_threshold":
        threshold = float(decision_rule["threshold"])
        high_cols = [i for i, label in enumerate(labels) if label <= 2]
        if not high_cols:
            return int(pred)
        high_prob = float(aligned[high_cols].sum())
        if high_prob >= threshold:
            best_high_idx = high_cols[int(np.argmax(aligned[high_cols]))]
            return int(labels[best_high_idx])
        return int(pred)
    if rule_type == "ordinal_cost_sensitive":
        cost_matrix = decision_rule.get("cost_matrix")
        if not cost_matrix:
            under_penalty = float(decision_rule["under_triage_penalty"])
            over_penalty = float(decision_rule.get("over_triage_penalty", 1.0))
            cost_matrix = []
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
                cost_matrix.append(row)
        expected_cost = aligned.dot(np.asarray(cost_matrix, dtype=float))
        return int(labels[int(np.argmin(expected_cost))])
    raise ValueError(f"unsupported decision rule: {rule_type!r}")


def _bundle_model_identity(bundle) -> tuple[str | None, str | None]:
    """Best-effort model identity from the loaded artefact bundle.

    Returns (model_kind, model_version). The training pipeline stamps
    training_run_id and model_kind into the bundle; use those so audit and
    UI provenance never show the schema default "not_loaded" for a model that
    actually predicted.
    """
    if not isinstance(bundle, dict):
        return None, None
    kind = bundle.get("model_kind") or bundle.get("model_name")
    version = (
        bundle.get("model_version")
        or bundle.get("training_run_id")
        or bundle.get("raw_input_schema_hash")
    )
    return (str(kind) if kind else None), (str(version) if version else None)


def _successful_mimic_prediction(model, pred: int, probabilities, *, model_name: str,
                                 model_version: str | None = None) -> MLPredictionResult:
    class_probs = _class_probability_dict(model, probabilities)
    high_prob = None
    if probabilities is not None:
        p1 = _prob_for_class(model, probabilities, 1)
        p2 = _prob_for_class(model, probabilities, 2)
        if p1 is not None or p2 is not None:
            high_prob = float((p1 or 0.0) + (p2 or 0.0))
    top_confidence = max(class_probs.values()) if class_probs else None
    return MLPredictionResult(
        model_name=model_name,
        model_version=model_version or "not_loaded",
        prediction_available=True,
        predicted_mimic_acuity=pred,
        mimic_acuity_probabilities=class_probs,
        high_acuity_research_estimate=high_prob,
        top_class_confidence=top_confidence,
        prediction_scale="MIMIC_ACUITY_1_5",
        model_note="Credentialed research acuity model. Not clinically validated; "
                   "clinician review required.",
    )


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
            "No live prediction model is available for this dataset/profile. No "
            "estimate is shown; clinician review required. The deterministic "
            "safety review and audit logging still run."
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
                "Credentialed model artefact is not available in this environment "
                "(set the configured model path on the approved environment). No "
                "estimate is shown; clinician review required. Other model "
                "artefacts are never substituted for credentialed cases."
            ),
        )
    expected_sha = os.environ.get("MIMIC_FULL_MODEL_SHA256", "").strip().lower()
    if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true" and not expected_sha:
        return MLPredictionResult(
            model_name="full_mimic_model_hash_required",
            prediction_available=False,
            model_note=(
                "Patient-data mode requires a configured model checksum before "
                "loading a joblib model artefact. Refusing to deserialize an "
                "unpinned model; clinician review required."
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
                    "Configured model artefact hash does not match the expected "
                    "checksum. Refusing to use a model whose provenance cannot be "
                    "confirmed; clinician review required."
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
                f"Configured model artefact could not be verified ({exc}). Refusing "
                "to use an unverifiable artefact; clinician review required."
            ),
        )
    if not compat["compatible"]:
        return MLPredictionResult(
            model_name="full_mimic_model_incompatible",
            prediction_available=False,
            model_note=(
                f"Configured model artefact is incompatible: {compat['reason']}. "
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
        contract_version = int(
            bundle.get("artifact_contract_version", 1) if isinstance(bundle, dict) else 1
        )
        if contract_version == 2:
            from ml_training.full_mimic.raw_triage_pipeline import (
                RAW_TRIAGE_INPUT_COLUMNS,
                RAW_TRIAGE_INPUT_TYPE,
                raw_triage_dataframe_from_triage_inputs,
            )
            input_type = bundle.get("input_type") if isinstance(bundle, dict) else None
            raw_cols = list(bundle.get("raw_input_columns", [])) if isinstance(bundle, dict) else []
            if input_type != RAW_TRIAGE_INPUT_TYPE or raw_cols != list(RAW_TRIAGE_INPUT_COLUMNS):
                return MLPredictionResult(
                    model_name="full_mimic_model_raw_schema_mismatch",
                    prediction_available=False,
                    model_note=(
                        "v2 model artefact raw dataframe schema does not match "
                        "the serving contract. Refusing to use it; clinician "
                        "review required."
                    ),
                )
            X_raw = raw_triage_dataframe_from_triage_inputs([triage_input])
            pred = int(np.asarray(model.predict(X_raw)).ravel()[0])
            probabilities = _predict_proba_safe(model, X_raw)
            pred = _apply_artifact_decision_rule(
                model, pred, probabilities,
                bundle.get("decision_rule") if isinstance(bundle, dict) else None,
            )
            if pred not in {1, 2, 3, 4, 5}:
                return MLPredictionResult(
                    model_name="full_mimic_model_invalid_prediction",
                    prediction_available=False,
                    model_note=(
                        f"Configured model returned out-of-range acuity {pred!r}; "
                        "refusing to display it. Clinician review required."
                    ),
                )
            _kind, _version = _bundle_model_identity(bundle)
            return _successful_mimic_prediction(
                model,
                pred,
                probabilities,
                model_name=_kind or "mimic_full_acuity_model_v2_raw_tfidf",
                model_version=_version,
            )
        if contract_version != 1:
            return MLPredictionResult(
                model_name="full_mimic_model_unsupported_contract",
                prediction_available=False,
                model_note=(
                    f"Unsupported model artefact contract version {contract_version}. "
                    "Refusing to use it; clinician review required."
                ),
            )
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
        probabilities = _predict_proba_safe(model, X)
        pred = _apply_artifact_decision_rule(
            model, pred, probabilities,
            bundle.get("decision_rule") if isinstance(bundle, dict) else None,
        )
        if pred not in {1, 2, 3, 4, 5}:
            return MLPredictionResult(
                model_name="full_mimic_model_invalid_prediction",
                prediction_available=False,
                model_note=(
                    f"Configured model returned out-of-range acuity {pred!r}; "
                    "refusing to display it. Clinician review required."
                ),
            )
        _kind, _version = _bundle_model_identity(bundle)
        return _successful_mimic_prediction(
            model,
            pred,
            probabilities,
            model_name=_kind or "mimic_full_acuity_model",
            model_version=_version,
        )
    except Exception as exc:
        return MLPredictionResult(
            model_name="full_mimic_model_error",
            prediction_available=False,
            model_note=f"Configured model could not produce an estimate: {exc}. "
                       "Clinician review required.",
        )
