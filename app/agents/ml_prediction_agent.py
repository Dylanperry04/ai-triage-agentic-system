"""
ML Research Prediction Agent for the UHL synthetic triage-vitals cohort.

Loads the pinned UHL CatBoost serving artefact and returns:
  - predicted research acuity estimate (1-5)
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


def _effective_decision_rule(decision_rule):
    """Apply a deployment-level threshold override to the artefact's rule.

    The artefact ships tau=0.05, which on the untouched test set forces 92.7% of
    ALL cases to acuity 1-2 (specificity 0.117). That operating point lives
    inside the joblib whose SHA256 is pinned in registry.json, so it cannot be
    changed by editing the artefact without breaking the integrity pin.

    This changes only the threshold VALUE -- never the rule type or class labels
    -- and returns the artefact's original value so it can be stamped into every
    prediction. A reviewer can then always see which operating point produced a
    given output, rather than the change being invisible after the fact.
    """
    if not isinstance(decision_rule, dict):
        return decision_rule, None
    if str(decision_rule.get("type") or "") != "high_acuity_threshold":
        return decision_rule, None
    try:
        from app.config import settings
        override = getattr(settings, "high_acuity_threshold_override", None)
    except Exception:
        override = None
    if override is None:
        return decision_rule, None
    try:
        artefact_value = float(decision_rule.get("threshold"))
    except (TypeError, ValueError):
        return decision_rule, None
    if abs(float(override) - artefact_value) < 1e-12:
        return decision_rule, None
    adjusted = dict(decision_rule)
    adjusted["threshold"] = float(override)
    return adjusted, artefact_value


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


UHL_URGENT_CLASS_PROBABILITY_THRESHOLD = 0.25


def _select_uhl_serving_acuity(
    class_probabilities: dict[str, float],
    *,
    threshold: float = UHL_URGENT_CLASS_PROBABILITY_THRESHOLD,
) -> int:
    """Use the modal class unless a more urgent class clears the threshold."""
    probabilities = {
        int(label): float(probability)
        for label, probability in class_probabilities.items()
        if int(label) in {1, 2, 3, 4, 5}
    }
    if not probabilities:
        raise ValueError("UHL model returned no class probabilities")
    modal = min(probabilities, key=lambda label: (-probabilities[label], label))
    urgent_qualifiers = [
        label
        for label, probability in probabilities.items()
        if label < modal and probability >= threshold
    ]
    return min(urgent_qualifiers) if urgent_qualifiers else modal


def _successful_mimic_prediction(model, pred: int, probabilities, *, model_name: str,
                                 model_version: str | None = None,
                                 decision_rule: dict | None = None,
                                 artefact_threshold: float | None = None) -> MLPredictionResult:
    class_probs = _class_probability_dict(model, probabilities)
    high_prob = None
    if probabilities is not None:
        p1 = _prob_for_class(model, probabilities, 1)
        p2 = _prob_for_class(model, probabilities, 2)
        if p1 is not None or p2 is not None:
            high_prob = float((p1 or 0.0) + (p2 or 0.0))
    top_confidence = max(class_probs.values()) if class_probs else None

    # Which class the top_class_confidence actually belongs to. When the
    # artefact carries a non-argmax decision rule the assigned acuity can differ
    # from this, and the caller must be able to say so rather than presenting
    # one class's probability under another class's label.
    argmax_acuity = None
    if class_probs:
        best_key = max(class_probs, key=lambda k: class_probs[k])
        try:
            argmax_acuity = int(best_key)
        except (TypeError, ValueError):
            argmax_acuity = None
    assigned_prob = class_probs.get(str(int(pred))) if class_probs else None

    rule = decision_rule if isinstance(decision_rule, dict) else {}
    rule_type = str(rule.get("type") or "argmax")
    rule_threshold = rule.get("threshold")
    try:
        rule_threshold = float(rule_threshold) if rule_threshold is not None else None
    except (TypeError, ValueError):
        rule_threshold = None

    return MLPredictionResult(
        model_name=model_name,
        model_version=model_version or "not_loaded",
        prediction_available=True,
        predicted_mimic_acuity=pred,
        mimic_acuity_probabilities=class_probs,
        high_acuity_research_estimate=high_prob,
        top_class_confidence=top_confidence,
        argmax_acuity=argmax_acuity,
        argmax_probability=top_confidence,
        assigned_acuity_probability=assigned_prob,
        decision_rule_type=rule_type,
        decision_rule_threshold=rule_threshold,
        decision_rule_threshold_source=(
            "deployment_override" if artefact_threshold is not None else "artefact"
        ),
        artefact_decision_rule_threshold=artefact_threshold,
        decision_rule_changed_prediction=bool(
            argmax_acuity is not None and int(argmax_acuity) != int(pred)
        ),
        prediction_scale="ACUITY_1_5",
        model_note="UHL acuity model. Clinician review required.",
    )


def run_ml_prediction(triage_input: TriageTimeInput) -> MLPredictionResult:
    """
    Dispatch ML prediction by source dataset.

    The only live prediction path is the pinned UHL synthetic dataset and its
    exact serving bundle. Unknown datasets withhold prediction rather than
    extrapolating.
    """
    ds = triage_input.source_dataset
    if ds == "UHL_SYNTHETIC_TRIAGE_VITALS_ACUITY_FINAL_20260402":
        return _run_uhl_prediction(triage_input)
    # Inactive compatibility seam for archived 22.4 unit tests and historic
    # records. The case resolver never serves this source in the UHL release.
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


def _run_uhl_prediction(triage_input: TriageTimeInput) -> MLPredictionResult:
    """Run the pinned UHL model through its exact 13-field serving contract."""
    import hashlib
    from types import SimpleNamespace

    from app.constants import (
        DATASET_SOURCE,
        FEATURE_SCHEMA_HASH,
        MODEL_INPUT_COLUMNS,
    )
    from ml_training.uhl_synthetic.serving import (
        uhl_dataframe_from_triage_inputs,
        validate_uhl_serving_bundle,
    )

    model_path = settings.uhl_model_path
    if not model_path.is_file():
        return MLPredictionResult(
            model_name="uhl_model_unavailable",
            prediction_available=False,
            model_note="The pinned UHL model artefact is missing; clinician review required.",
        )
    try:
        actual_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if actual_sha != settings.expected_model_sha256:
            raise ValueError("model SHA-256 does not match the pinned UHL release")

        bundle = joblib.load(model_path)
        validate_uhl_serving_bundle(bundle, require_deployable=True)
        if bundle.get("dataset_source") != DATASET_SOURCE:
            raise ValueError("model dataset source does not match the UHL application")
        if bundle.get("dataset_sha256") != settings.expected_dataset_sha256:
            raise ValueError("model dataset hash does not match the packaged UHL cohort")
        if list(bundle.get("raw_input_columns") or []) != list(MODEL_INPUT_COLUMNS):
            raise ValueError("model input order does not match the UHL serving contract")
        if bundle.get("raw_input_schema_hash") != FEATURE_SCHEMA_HASH:
            raise ValueError("model feature schema hash does not match the UHL release")

        item = SimpleNamespace(
            age=triage_input.age,
            arrival_time=triage_input.intime,
            presenting_complaint=triage_input.chiefcomplaint,
            temperature=triage_input.temperature,
            temperature_unit=triage_input.temperature_unit,
            heartrate=triage_input.heartrate,
            resprate=triage_input.resprate,
            o2sat=triage_input.o2sat,
            sbp=triage_input.sbp,
            dbp=triage_input.dbp,
            pain=triage_input.pain,
        )
        frame = uhl_dataframe_from_triage_inputs(
            [item],
            allowed_complaints=bundle.get("seen_presenting_complaints"),
            fitted_age_support=bundle.get("fitted_age_support"),
        )
        model = bundle["model"]
        probabilities = model.predict_proba(frame)
        class_probabilities = _class_probability_dict(model, probabilities)
        prediction = _select_uhl_serving_acuity(class_probabilities)
        decision_rule = {
            "type": "modal_with_urgent_class_threshold",
            "threshold": UHL_URGENT_CLASS_PROBABILITY_THRESHOLD,
            "threshold_scope": "individual_more_urgent_class",
        }
        if prediction not in {1, 2, 3, 4, 5}:
            raise ValueError(f"model returned out-of-range acuity {prediction!r}")
        kind, version = _bundle_model_identity(bundle)
        return _successful_mimic_prediction(
            model,
            prediction,
            probabilities,
            model_name=kind or "uhl_synthetic_acuity_model",
            model_version=version,
            decision_rule=decision_rule,
        )
    except Exception as exc:
        return MLPredictionResult(
            model_name="uhl_model_error",
            prediction_available=False,
            model_note=(
                f"The pinned UHL model could not produce an estimate: {exc}. "
                "Clinician review required."
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
            artefact_rule = bundle.get("decision_rule") if isinstance(bundle, dict) else None
            artefact_rule, _artefact_tau = _effective_decision_rule(artefact_rule)
            pred = _apply_artifact_decision_rule(
                model, pred, probabilities, artefact_rule,
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
                decision_rule=artefact_rule,
                artefact_threshold=_artefact_tau,
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
        artefact_rule = bundle.get("decision_rule") if isinstance(bundle, dict) else None
        artefact_rule, _artefact_tau = _effective_decision_rule(artefact_rule)
        pred = _apply_artifact_decision_rule(
            model, pred, probabilities, artefact_rule,
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
            decision_rule=artefact_rule,
            artefact_threshold=_artefact_tau,
        )
    except Exception as exc:
        return MLPredictionResult(
            model_name="full_mimic_model_error",
            prediction_available=False,
            model_note=f"Configured model could not produce an estimate: {exc}. "
                       "Clinician review required.",
        )
