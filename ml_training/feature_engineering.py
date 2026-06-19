"""
KTAS ML feature engineering.

Only triage-time fields are used. Blocked/leakage fields include KTAS_RN,
KTAS_expert, mistriage, Error_group, Diagnosis in ED, Disposition, Length of
stay, and KTAS duration.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

COMPLAINT_KEYWORDS = {
    "cc_chest": ["chest", "palpitation", "angina"],
    "cc_dyspnea": ["dyspnea", "shortness", "breath", "sob"],
    "cc_fever": ["fever", "chill", "febrile"],
    "cc_abdominal": ["abd", "abdominal", "epigastric", "flank", "rlq", "llq", "melena", "hematemesis"],
    "cc_neuro": ["mental", "syncope", "seizure", "weakness", "dysarthria", "hemiparesis", "facial", "numb", "stroke", "delirium"],
    "cc_trauma": ["injury", "wound", "laceration", "fracture", "burn", "trauma", "dislocation"],
    "cc_headache": ["headache", "ha"],
    "cc_vomiting": ["vomit", "nausea", "diarrhea"],
    "cc_bleeding": ["bleed", "bleeding", "hematochezia", "melena", "low hb"],
    "cc_self_harm": ["suicid", "intoxication", "overdose", "drug"],
}

FEATURE_NAMES = [
    "age",
    "patients_per_hour",
    "temperature_c",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "nrs_pain",
    "pain_present",
    "vital_missing_count",
    "sex_male",
    "sex_female",
    "arrival_walking",
    "arrival_public_ambulance",
    "arrival_private_vehicle",
    "arrival_private_ambulance",
    "arrival_other",
    "injury_yes",
    "mental_alert",
    "mental_verbal_response",
    "mental_pain_response",
    "mental_unresponsive",
    "group_regional_ed",
] + list(COMPLAINT_KEYWORDS.keys())

LEAKAGE_FEATURE_BLOCKLIST = {
    "KTAS_RN", "KTAS_expert", "mistriage", "Error_group", "Diagnosis in ED",
    "Disposition", "Length of stay_min", "KTAS duration_min", "label_ktas_rn",
    "label_ktas_expert", "label_mistriage", "label_error_group",
    "disposition", "diagnosis_in_ed", "ktas_duration_min", "length_of_stay_min",
}


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(str(val).replace(",", "."))
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> Optional[int]:
    f = _safe_float(val)
    return None if f is None else int(f)


def _temperature_to_c(value, unit: str | None) -> Optional[float]:
    temp = _safe_float(value)
    if temp is None:
        return None
    unit_norm = (unit or "").strip().upper()
    if unit_norm in {"F", "FAHRENHEIT"}:
        return (temp - 32.0) * 5.0 / 9.0
    return temp


def _contains_any(text: str, terms: list[str]) -> float:
    lower = (text or "").lower()
    return 1.0 if any(term in lower for term in terms) else 0.0


def extract_features_from_row(row: dict) -> dict:
    """Extract deterministic numeric features from a triage-time row.

    Training rows legitimately contain label/audit columns (e.g. label_ktas_expert)
    alongside feature columns, so their presence in `row` is not itself an error.
    What must never happen is one of those columns ending up in the *returned*
    feature dict. That is enforced at the end of this function, not here.
    """
    age = _safe_float(row.get("age"))
    patients_per_hour = _safe_float(row.get("patients_per_hour"))
    temperature_c = _temperature_to_c(row.get("temperature"), row.get("temperature_unit", "C"))
    heartrate = _safe_float(row.get("heartrate"))
    resprate = _safe_float(row.get("resprate"))
    o2sat = _safe_float(row.get("o2sat"))
    sbp = _safe_float(row.get("sbp"))
    dbp = _safe_float(row.get("dbp"))
    nrs_pain = _safe_float(row.get("nrs_pain"))
    if nrs_pain is None:
        nrs_pain = _safe_float(row.get("pain"))
    pain_present = _safe_int(row.get("pain_present"))

    vital_values = [temperature_c, heartrate, resprate, o2sat, sbp, dbp]
    vital_missing_count = float(sum(1 for v in vital_values if v is None))

    gender = str(row.get("gender") or "").strip().upper()
    arrival = str(row.get("arrival_transport") or "").strip().upper()
    arrival_code = _safe_int(row.get("arrival_mode_code"))
    injury_code = _safe_int(row.get("injury_code"))
    mental_code = _safe_int(row.get("mental_code"))
    group_code = _safe_int(row.get("group_code"))

    features = {
        "age": age if age is not None else -1.0,
        "patients_per_hour": patients_per_hour if patients_per_hour is not None else -1.0,
        "temperature_c": temperature_c if temperature_c is not None else -1.0,
        "heartrate": heartrate if heartrate is not None else -1.0,
        "resprate": resprate if resprate is not None else -1.0,
        "o2sat": o2sat if o2sat is not None else -1.0,
        "sbp": sbp if sbp is not None else -1.0,
        "dbp": dbp if dbp is not None else -1.0,
        "nrs_pain": nrs_pain if nrs_pain is not None else -1.0,
        "pain_present": float(pain_present) if pain_present is not None else -1.0,
        "vital_missing_count": vital_missing_count,
        "sex_male": 1.0 if gender == "M" else 0.0,
        "sex_female": 1.0 if gender == "F" else 0.0,
        "arrival_walking": 1.0 if arrival_code == 1 or "WALK" in arrival else 0.0,
        "arrival_public_ambulance": 1.0 if arrival_code == 2 or "PUBLIC AMBULANCE" in arrival else 0.0,
        "arrival_private_vehicle": 1.0 if arrival_code == 3 or "PRIVATE VEHICLE" in arrival else 0.0,
        "arrival_private_ambulance": 1.0 if arrival_code == 4 or "PRIVATE AMBULANCE" in arrival else 0.0,
        "arrival_other": 1.0 if arrival_code in {5, 6, 7} or arrival == "OTHER" else 0.0,
        "injury_yes": 1.0 if injury_code == 2 else 0.0,
        "mental_alert": 1.0 if mental_code == 1 else 0.0,
        "mental_verbal_response": 1.0 if mental_code == 2 else 0.0,
        "mental_pain_response": 1.0 if mental_code == 3 else 0.0,
        "mental_unresponsive": 1.0 if mental_code == 4 else 0.0,
        "group_regional_ed": 1.0 if group_code == 2 else 0.0,
    }
    complaint = str(row.get("chiefcomplaint") or "")
    for feature_name, terms in COMPLAINT_KEYWORDS.items():
        features[feature_name] = _contains_any(complaint, terms)

    leaked = LEAKAGE_FEATURE_BLOCKLIST.intersection(FEATURE_NAMES)
    if leaked:
        raise ValueError(
            f"LEAKAGE DETECTED: blocklisted field(s) present in FEATURE_NAMES: {sorted(leaked)}. "
            "This must be fixed in ml_training/feature_engineering.py before training."
        )

    return {name: float(features[name]) for name in FEATURE_NAMES}


def build_feature_matrix(rows: list[dict]) -> tuple[np.ndarray, list[str]]:
    feature_dicts = [extract_features_from_row(r) for r in rows]
    X = np.array([[fd[f] for f in FEATURE_NAMES] for fd in feature_dicts], dtype=float)
    return X, FEATURE_NAMES


def build_training_dataframe(jsonl_records: list[dict]) -> pd.DataFrame:
    rows = []
    for rec in jsonl_records:
        features = extract_features_from_row(rec)
        row = dict(features)
        row["label_ktas_expert"] = rec.get("label_ktas_expert")
        row["label_ktas_emergency"] = rec.get("label_ktas_emergency")
        row["label_ktas_high_acuity"] = rec.get("label_ktas_high_acuity")
        row["label_mistriage"] = rec.get("label_mistriage")
        rows.append(row)
    return pd.DataFrame(rows)
