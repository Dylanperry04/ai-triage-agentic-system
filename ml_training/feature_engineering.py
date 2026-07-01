"""
Full MIMIC-IV-ED ML feature engineering.

Only triage-time fields that ACTUALLY EXIST in the MIMIC-IV-ED schema are used:
  - triage.csv.gz: temperature, heartrate, resprate, o2sat, sbp, dbp, pain,
    chiefcomplaint   (acuity is the LABEL, never a feature)
  - edstays.csv.gz: gender, arrival_transport

Features that were KTAS-derived and are absent/constant for MIMIC have been
REMOVED (they added no signal and broke train/serve parity assumptions):
  age (MIMIC ED triage has no age column; it lives in the hospital patients
  table, not the ED module), patients_per_hour, injury_code/injury_yes,
  mental_code/AVPU (mental_alert/verbal/pain/unresponsive), group_code/
  group_regional_ed, and the KTAS arrival categories (walking/public-ambulance/
  private-vehicle/private-ambulance keyed off a 1-7 arrival_mode_code that MIMIC
  does not have).

MIMIC arrival_transport values are AMBULANCE, WALK IN, HELICOPTER, UNKNOWN,
OTHER — encoded here as the one-hot below.

Leakage/outcome columns (triage.acuity as input, edstays.disposition, outtime,
hadm_id, diagnoses, medrecon, pyxis, full-stay vitalsign) are NEVER features.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

COMPLAINT_KEYWORDS = {
    "cc_chest": ["chest", "palpitation", "angina", "cp"],
    "cc_cardiac_arrest": ["arrest", "cardiac arrest", "unresponsive", "cpr"],
    "cc_dyspnea": ["dyspnea", "shortness", "breath", "sob", "wheeze"],
    "cc_respiratory_failure": ["respiratory failure", "hypoxia", "hypoxic", "cyanosis"],
    "cc_fever": ["fever", "chill", "febrile"],
    "cc_infection": ["sepsis", "infection", "cellulitis", "pneumonia", "abscess"],
    "cc_abdominal": ["abd", "abdominal", "epigastric", "flank", "rlq", "llq", "melena", "hematemesis"],
    "cc_urogenital": ["urinary", "dysuria", "hematuria", "testicular", "vaginal"],
    "cc_obstetric": ["pregnan", "ob", "labor", "vaginal bleeding"],
    "cc_neuro": ["mental", "syncope", "seizure", "weakness", "dysarthria", "hemiparesis", "facial", "numb", "stroke", "delirium"],
    "cc_dizziness": ["dizziness", "vertigo", "lightheaded"],
    "cc_trauma": ["injury", "wound", "laceration", "fracture", "burn", "trauma", "dislocation", "fall"],
    "cc_headache": ["headache", "migraine"],
    "cc_eye_ent": ["eye", "vision", "ear", "throat", "epistaxis"],
    "cc_vomiting": ["vomit", "nausea", "diarrhea"],
    "cc_bleeding": ["bleed", "bleeding", "hematochezia", "melena", "low hb"],
    "cc_allergy": ["allergy", "anaphylaxis", "hives", "rash"],
    "cc_back_pain": ["back pain", "sciatica"],
    "cc_psych": ["anxiety", "psych", "depression", "agitation", "hallucination"],
    "cc_self_harm": ["suicid", "intoxication", "overdose", "drug"],
}

# MIMIC-IV-ED feature set — only fields present in the real schema.
FEATURE_NAMES = [
    "temperature_c",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "temperature_missing",
    "heartrate_missing",
    "resprate_missing",
    "o2sat_missing",
    "sbp_missing",
    "dbp_missing",
    "nrs_pain",
    "pain_present",
    "pain_missing",
    "vital_missing_count",
    "shock_index",
    "shock_index_high_flag",
    "pulse_pressure",
    "respiratory_distress_flag",
    "fever_flag",
    "hypothermia_flag",
    "hypotension_flag",
    "tachycardia_flag",
    "bradycardia_flag",
    "fever_tachycardia_interaction",
    "hypoxia_tachypnea_interaction",
    "hypotension_tachycardia_interaction",
    "sex_male",
    "sex_female",
    "arrival_ambulance",
    "arrival_walk_in",
    "arrival_helicopter",
    "arrival_other_unknown",
] + list(COMPLAINT_KEYWORDS.keys())

# Outcome/label/identifier columns that must NEVER appear in FEATURE_NAMES.
IDENTIFIER_FEATURE_NAMES = {"subject_id", "stay_id", "hadm_id"}
LEAKAGE_FEATURE_EXACT_BLOCKLIST = {
    "acuity", "label", "target", "disposition", "outtime", "intime",
    "ed_los", "hospital_expire_flag", "mortality", "diagnosis", "diagnoses",
    "medrecon", "pyxis", "vitals_timeseries", "charttime", "future",
    "death", "length_of_stay", "admission", *IDENTIFIER_FEATURE_NAMES,
}
LEAKAGE_FEATURE_SUBSTRINGS = (
    "acuity", "target", "label", "disposition", "outtime", "intime",
    "diagnos", "medrecon", "pyxis", "charttime", "vitals_timeseries",
    "mortality", "death", "expire", "outcome", "future", "ed_los",
    "length_of_stay", "admission", "admit",
)


def validate_feature_schema(feature_names: list[str] | None = None) -> None:
    names = list(feature_names or FEATURE_NAMES)
    lowered = [str(n).strip().lower() for n in names]
    exact = sorted(set(lowered) & LEAKAGE_FEATURE_EXACT_BLOCKLIST)
    patterned = sorted(
        name for name in lowered
        if any(pattern in name for pattern in LEAKAGE_FEATURE_SUBSTRINGS)
    )
    blocked = sorted(set(exact + patterned))
    if blocked:
        raise ValueError(
            "LEAKAGE DETECTED: blocked identifier/outcome/future/target-like "
            f"feature(s) present in FEATURE_NAMES: {blocked}. This must be fixed "
            "before training or serving."
        )


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
    """Extract deterministic numeric features from a MIMIC-IV-ED triage-time row.

    The SAME function is used for training and serving, so train/serve parity is
    structural: any change here changes both. Returns exactly FEATURE_NAMES, in
    order, all floats. Missing numeric values are encoded as -1.0 (a sentinel,
    since MIMIC vitals are non-negative). Never returns a leakage/label column.
    """
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
    if pain_present is None and nrs_pain is not None:
        pain_present = 1 if nrs_pain > 0 else 0
    pain_missing = nrs_pain is None and pain_present is None

    vital_values = [temperature_c, heartrate, resprate, o2sat, sbp, dbp]
    vital_missing_count = float(sum(1 for v in vital_values if v is None))
    shock_index = (
        float(heartrate) / float(sbp)
        if heartrate is not None and sbp is not None and sbp > 0
        else -1.0
    )
    pulse_pressure = (
        float(sbp) - float(dbp)
        if sbp is not None and dbp is not None
        else -1.0
    )
    fever_flag = 1.0 if temperature_c is not None and temperature_c >= 38.0 else 0.0
    hypothermia_flag = 1.0 if temperature_c is not None and temperature_c <= 35.0 else 0.0
    hypotension_flag = 1.0 if sbp is not None and sbp < 90 else 0.0
    tachycardia_flag = 1.0 if heartrate is not None and heartrate >= 120 else 0.0
    bradycardia_flag = 1.0 if heartrate is not None and heartrate < 50 else 0.0
    hypoxia_flag = 1.0 if o2sat is not None and o2sat < 90 else 0.0
    tachypnea_flag = 1.0 if resprate is not None and resprate >= 30 else 0.0

    gender = str(row.get("gender") or "").strip().upper()
    arrival = str(row.get("arrival_transport") or "").strip().upper()

    features = {
        "temperature_c": temperature_c if temperature_c is not None else -1.0,
        "heartrate": heartrate if heartrate is not None else -1.0,
        "resprate": resprate if resprate is not None else -1.0,
        "o2sat": o2sat if o2sat is not None else -1.0,
        "sbp": sbp if sbp is not None else -1.0,
        "dbp": dbp if dbp is not None else -1.0,
        "temperature_missing": 1.0 if temperature_c is None else 0.0,
        "heartrate_missing": 1.0 if heartrate is None else 0.0,
        "resprate_missing": 1.0 if resprate is None else 0.0,
        "o2sat_missing": 1.0 if o2sat is None else 0.0,
        "sbp_missing": 1.0 if sbp is None else 0.0,
        "dbp_missing": 1.0 if dbp is None else 0.0,
        "nrs_pain": nrs_pain if nrs_pain is not None else -1.0,
        "pain_present": float(pain_present) if pain_present is not None else -1.0,
        "pain_missing": 1.0 if pain_missing else 0.0,
        "vital_missing_count": vital_missing_count,
        "shock_index": shock_index,
        "shock_index_high_flag": 1.0 if shock_index >= 1.0 else 0.0,
        "pulse_pressure": pulse_pressure,
        "respiratory_distress_flag": 1.0 if (
            (resprate is not None and resprate >= 30)
            or (o2sat is not None and o2sat < 90)
        ) else 0.0,
        "fever_flag": fever_flag,
        "hypothermia_flag": hypothermia_flag,
        "hypotension_flag": hypotension_flag,
        "tachycardia_flag": tachycardia_flag,
        "bradycardia_flag": bradycardia_flag,
        "fever_tachycardia_interaction": fever_flag * tachycardia_flag,
        "hypoxia_tachypnea_interaction": hypoxia_flag * tachypnea_flag,
        "hypotension_tachycardia_interaction": hypotension_flag * tachycardia_flag,
        "sex_male": 1.0 if gender == "M" else 0.0,
        "sex_female": 1.0 if gender == "F" else 0.0,
        # MIMIC-IV-ED arrival_transport one-hot (AMBULANCE / WALK IN / HELICOPTER /
        # UNKNOWN / OTHER). HELICOPTER kept distinct (it is high-acuity transport).
        "arrival_ambulance": 1.0 if arrival == "AMBULANCE" else 0.0,
        "arrival_walk_in": 1.0 if arrival in {"WALK IN", "WALKIN", "WALK-IN"} else 0.0,
        "arrival_helicopter": 1.0 if arrival == "HELICOPTER" else 0.0,
        "arrival_other_unknown": 1.0 if arrival in {"OTHER", "UNKNOWN", ""} else 0.0,
    }
    complaint = str(row.get("chiefcomplaint") or "")
    for feature_name, terms in COMPLAINT_KEYWORDS.items():
        features[feature_name] = _contains_any(complaint, terms)

    validate_feature_schema(FEATURE_NAMES)

    return {name: float(features[name]) for name in FEATURE_NAMES}


def build_feature_matrix(rows: list[dict]) -> tuple[np.ndarray, list[str]]:
    feature_dicts = [extract_features_from_row(r) for r in rows]
    X = np.array([[fd[f] for f in FEATURE_NAMES] for fd in feature_dicts], dtype=float)
    return X, FEATURE_NAMES
def build_feature_frame(cases: list[dict]) -> tuple["np.ndarray", list, list[str]]:
    """Bridge for the full-MIMIC scaffolding: given a list of nested case dicts
    (EDTriageCase.model_dump shape), build the feature matrix X, the acuity label
    vector y (None where unlabelled), and the feature-name list.

    Each case's triage-time fields live under case['triage']; the acuity label is
    case['triage']['acuity']. Demographic fields (gender/arrival_transport) come
    from case['edstay']. No identifiers or retrospective columns are used.
    """
    rows = []
    labels = []
    for case in cases:
        # Accept either an EDTriageCase object or its dict form.
        if hasattr(case, "model_dump"):
            case = case.model_dump(mode="json")
        triage = dict(case.get("triage") or {})
        edstay = case.get("edstay") or {}
        # merge the few demographic feature inputs the extractor reads
        if "gender" not in triage and edstay.get("gender") is not None:
            triage["gender"] = edstay.get("gender")
        if "arrival_transport" not in triage and edstay.get("arrival_transport") is not None:
            triage["arrival_transport"] = edstay.get("arrival_transport")
        rows.append(triage)
        labels.append(triage.get("acuity"))
    X, feature_names = build_feature_matrix(rows)
    return X, labels, feature_names


def build_feature_frame_with_meta(cases: list[dict]):
    """Like build_feature_frame, but ALSO returns per-row split metadata:
    subject_id (for patient-grouped splitting so a patient's repeat visits never
    span train/test) and intime (for temporal splitting). subject_id and intime
    are NEVER features — they are returned separately for the splitter only.

    Returns (X, y, feature_names, subject_ids, intimes).
    """
    rows, labels, subject_ids, intimes = [], [], [], []
    for case in cases:
        if hasattr(case, "model_dump"):
            case = case.model_dump(mode="json")
        triage = dict(case.get("triage") or {})
        edstay = case.get("edstay") or {}
        if "gender" not in triage and edstay.get("gender") is not None:
            triage["gender"] = edstay.get("gender")
        if "arrival_transport" not in triage and edstay.get("arrival_transport") is not None:
            triage["arrival_transport"] = edstay.get("arrival_transport")
        rows.append(triage)
        labels.append(triage.get("acuity"))
        sid = case.get("subject_id")
        if sid is None:
            sid = edstay.get("subject_id")
        if sid is None:
            sid = triage.get("subject_id")
        subject_ids.append(sid)
        intimes.append(edstay.get("intime"))
    X, feature_names = build_feature_matrix(rows)
    return X, labels, feature_names, subject_ids, intimes
