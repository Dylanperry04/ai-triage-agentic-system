"""
Kaggle KTAS dataset adapter.

Reads the public Kaggle Emergency Service / KTAS `data.csv` file and converts
it into the project's canonical EDTriageCase JSONL format.

Verified file characteristics from the supplied CSV:
  - semicolon separator
  - Latin-1-compatible encoding due values such as '#BOÞ!'
  - decimal comma in `KTAS duration_min`
  - dirty placeholders including '#BOÞ!' and '??'

Safety boundary:
  - KTAS_RN, KTAS_expert, mistriage, Error_group, Diagnosis in ED,
    Disposition, Length of stay_min, and KTAS duration_min are not included in
    TriageTimeInput.
  - KTAS_expert is the main research target. It is not Manchester triage.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import math
import pandas as pd

from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource, DiagnosisRecord

REQUIRED_COLUMNS = [
    "Group", "Sex", "Age", "Patients number per hour", "Arrival mode", "Injury",
    "Chief_complain", "Mental", "Pain", "NRS_pain", "SBP", "DBP", "HR", "RR",
    "BT", "Saturation", "KTAS_RN", "Diagnosis in ED", "Disposition", "KTAS_expert",
    "Error_group", "Length of stay_min", "KTAS duration_min", "mistriage",
]

DIRTY_MISSING_VALUES = {"", " ", "??", "#BOÞ!", "#BOÃÞ!", "#BO�!", "nan", "NaN", "None"}

SEX_MAP = {1: "F", 2: "M"}
SEX_LABEL_MAP = {1: "Female", 2: "Male"}
GROUP_MAP = {1: "Local ED", 2: "Regional ED"}
ARRIVAL_MODE_MAP = {
    1: "Walking",
    2: "Public Ambulance",
    3: "Private Vehicle",
    4: "Private Ambulance",
    5: "Public Transportation (Police etc.)",
    6: "Wheelchair",
    7: "Other",
}
INJURY_MAP = {1: "No", 2: "Yes"}
MENTAL_MAP = {
    1: "Alert",
    2: "Verbal Response",
    3: "Pain Response",
    4: "Unresponsive",
}
# UNVERIFIED: no authoritative source for these 7 codes was found. This is a
# plausible best-guess for display/audit text only. Disposition is excluded
# from TriageTimeInput and from ML features (see RETROSPECTIVE_OR_LEAKAGE_COLUMNS
# and LEAKAGE_FEATURE_BLOCKLIST), so this mapping has no effect on any triage
# decision, safety flag, or model prediction -- it only affects how a
# disposition code is rendered as text in retrospective/audit output.
# Confirm against the dataset's original documentation before relying on these
# labels for any reporting claim.
DISPOSITION_MAP_VERIFIED = False
DISPOSITION_MAP = {
    1: "Discharge",
    2: "Admission to ward",
    3: "Admission to ICU",
    4: "Discharge",
    5: "Transfer",
    6: "Death",
    7: "Surgery",
}
MISTRIAGE_MAP = {0: "correct", 1: "over_triage", 2: "under_triage"}


def _clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, str):
        text = value.strip()
        if text in DIRTY_MISSING_VALUES:
            return None
        return text
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _to_float(value: Any) -> Optional[float]:
    value = _clean_scalar(value)
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    value = _clean_scalar(value)
    if value is None:
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> Optional[str]:
    value = _clean_scalar(value)
    return None if value is None else str(value)


def load_ktas_csv(path: Path) -> pd.DataFrame:
    """Load and validate the raw Kaggle KTAS CSV."""
    if not path.exists():
        raise FileNotFoundError(
            f"Kaggle KTAS CSV not found: {path}\n"
            "Download it with Kaggle or place the supplied data.csv at "
            "data/raw/kaggle_ktas/data.csv."
        )
    df = pd.read_csv(path, sep=";", encoding="latin1", decimal=",")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Kaggle KTAS CSV missing required columns: {missing}")
    return df


def clean_ktas_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Return a cleaned copy with parsed numeric columns and mapped labels."""
    out = df.copy()
    numeric_cols = [
        "Group", "Sex", "Age", "Patients number per hour", "Arrival mode", "Injury",
        "Mental", "Pain", "NRS_pain", "SBP", "DBP", "HR", "RR", "BT", "Saturation",
        "KTAS_RN", "Disposition", "KTAS_expert", "Error_group", "Length of stay_min",
        "KTAS duration_min", "mistriage",
    ]
    for col in numeric_cols:
        out[col] = out[col].apply(_to_float)
    int_like = [
        "Group", "Sex", "Age", "Patients number per hour", "Arrival mode", "Injury",
        "Mental", "Pain", "KTAS_RN", "Disposition", "KTAS_expert", "Error_group", "mistriage",
    ]
    for col in int_like:
        out[col] = out[col].apply(lambda v: None if v is None else int(v))
    for col in ["Chief_complain", "Diagnosis in ED"]:
        out[col] = out[col].apply(_to_str)

    out["sex_label"] = out["Sex"].map(SEX_LABEL_MAP)
    out["gender"] = out["Sex"].map(SEX_MAP)
    out["group_label"] = out["Group"].map(GROUP_MAP)
    out["arrival_mode_label"] = out["Arrival mode"].map(ARRIVAL_MODE_MAP)
    out["injury_label"] = out["Injury"].map(INJURY_MAP)
    out["mental_label"] = out["Mental"].map(MENTAL_MAP)
    out["disposition_label"] = out["Disposition"].map(DISPOSITION_MAP)
    out["mistriage_label"] = out["mistriage"].map(MISTRIAGE_MAP)
    out["ktas_emergency"] = out["KTAS_expert"].apply(lambda v: None if v is None else int(v <= 3))
    out["ktas_high_acuity"] = out["KTAS_expert"].apply(lambda v: None if v is None else int(v <= 2))
    return out


def validate_ktas_dataframe(df: pd.DataFrame) -> dict:
    """Create a schema/data-quality report for governance."""
    clean = clean_ktas_dataframe(df)
    numeric_quality = {}
    for col in ["NRS_pain", "SBP", "DBP", "HR", "RR", "BT", "Saturation"]:
        parsed = clean[col]
        numeric_quality[col] = {
            "missing_or_invalid_count": int(parsed.isna().sum()),
            "missing_or_invalid_percent": round(float(parsed.isna().mean() * 100), 2),
            "min": None if parsed.dropna().empty else float(parsed.min()),
            "max": None if parsed.dropna().empty else float(parsed.max()),
        }
    return {
        "dataset": "Kaggle Emergency Service - KTAS Triage Application",
        "source_file_shape": [int(df.shape[0]), int(df.shape[1])],
        "required_columns_present": all(c in df.columns for c in REQUIRED_COLUMNS),
        "columns": list(df.columns),
        "delimiter": ";",
        "encoding_used": "latin1",
        "decimal_policy": "comma decimals parsed where present; dirty placeholders converted to null",
        "numeric_quality": numeric_quality,
        "ktas_expert_distribution": {str(k): int(v) for k, v in Counter(clean["KTAS_expert"].dropna()).items()},
        "ktas_rn_distribution": {str(k): int(v) for k, v in Counter(clean["KTAS_RN"].dropna()).items()},
        "mistriage_distribution": {str(k): int(v) for k, v in Counter(clean["mistriage_label"].dropna()).items()},
        "leakage_columns_blocked_from_features": [
            "KTAS_RN", "KTAS_expert", "mistriage", "Error_group", "Diagnosis in ED",
            "Disposition", "Length of stay_min", "KTAS duration_min",
        ],
        "clinical_safety_note": (
            "This dataset uses KTAS, not Manchester Triage Scale. No KTAS-to-Manchester "
            "mapping is implemented or assumed. All outputs remain not for clinical use."
        ),
    }


def dataframe_to_cases(df: pd.DataFrame, n: int | None = None) -> List[EDTriageCase]:
    clean = clean_ktas_dataframe(df)
    if n is not None:
        clean = clean.head(n).copy()
    cases: List[EDTriageCase] = []

    for idx, row in clean.reset_index(drop=False).iterrows():
        source_row_index = int(row.get("index", idx))
        stay_id = source_row_index + 1
        subject_id = 900000 + stay_id

        sex_code = _to_int(row.get("Sex"))
        arrival_code = _to_int(row.get("Arrival mode"))
        disposition_code = _to_int(row.get("Disposition"))
        ktas_expert = _to_int(row.get("KTAS_expert"))
        ktas_rn = _to_int(row.get("KTAS_RN"))
        mistriage = _to_int(row.get("mistriage"))
        error_group = _to_int(row.get("Error_group"))

        edstay = EDStaySource(
            subject_id=subject_id,
            stay_id=stay_id,
            gender=SEX_MAP.get(sex_code),
            race=None,
            arrival_transport=ARRIVAL_MODE_MAP.get(arrival_code),
            disposition=DISPOSITION_MAP.get(disposition_code),
        )
        triage = TriageSource(
            subject_id=subject_id,
            stay_id=stay_id,
            temperature=_to_float(row.get("BT")),
            temperature_unit="C",
            heartrate=_to_float(row.get("HR")),
            resprate=_to_float(row.get("RR")),
            o2sat=_to_float(row.get("Saturation")),
            sbp=_to_float(row.get("SBP")),
            dbp=_to_float(row.get("DBP")),
            pain=(None if _to_float(row.get("NRS_pain")) is None else str(int(float(row.get("NRS_pain"))))),
            chiefcomplaint=_to_str(row.get("Chief_complain")),
            age=_to_float(row.get("Age")),
            group_code=_to_int(row.get("Group")),
            group_label=GROUP_MAP.get(_to_int(row.get("Group"))),
            patients_per_hour=_to_float(row.get("Patients number per hour")),
            arrival_mode_code=arrival_code,
            injury_code=_to_int(row.get("Injury")),
            injury_label=INJURY_MAP.get(_to_int(row.get("Injury"))),
            mental_code=_to_int(row.get("Mental")),
            mental_label=MENTAL_MAP.get(_to_int(row.get("Mental"))),
            pain_present=_to_int(row.get("Pain")),
            nrs_pain=_to_float(row.get("NRS_pain")),
            acuity=None,
        )
        diagnosis_text = _to_str(row.get("Diagnosis in ED"))
        diagnoses: list[DiagnosisRecord] = []
        if diagnosis_text:
            diagnoses.append(DiagnosisRecord(
                subject_id=subject_id,
                stay_id=stay_id,
                seq_num=1,
                icd_title=diagnosis_text,
            ))
        cases.append(EDTriageCase(
            source_dataset="Kaggle-KTAS",
            stay_id=stay_id,
            subject_id=subject_id,
            edstay=edstay,
            triage=triage,
            diagnoses=diagnoses,
            audit_metadata={
                "source_dataset": "Kaggle Emergency Service - KTAS Triage Application",
                "source_row_index_zero_based": source_row_index,
                "temperature_unit": "C",
                "triage_input_policy": "KTAS labels and outcomes excluded from TriageTimeInput.",
                "manchester_policy": "KTAS is not Manchester; no mapping is implemented.",
            },
            retrospective_metadata={
                "ktas_rn": ktas_rn,
                "ktas_expert": ktas_expert,
                "ktas_emergency": None if ktas_expert is None else int(ktas_expert <= 3),
                "ktas_high_acuity": None if ktas_expert is None else int(ktas_expert <= 2),
                "mistriage": mistriage,
                "mistriage_label": MISTRIAGE_MAP.get(mistriage),
                "error_group": error_group,
                "diagnosis_in_ed": diagnosis_text,
                "length_of_stay_min": _to_float(row.get("Length of stay_min")),
                "ktas_duration_min": _to_float(row.get("KTAS duration_min")),
                "disposition_code": disposition_code,
                "disposition_label": DISPOSITION_MAP.get(disposition_code),
            },
        ))
    return cases


def load_ktas_cases(csv_path: Path, n: int | None = None) -> tuple[list[EDTriageCase], dict]:
    raw = load_ktas_csv(csv_path)
    report = validate_ktas_dataframe(raw)
    cases = dataframe_to_cases(raw, n=n)
    report["cases_built"] = len(cases)
    return cases, report
