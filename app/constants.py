from __future__ import annotations

import hashlib

APP_VERSION = "22.4.0-uhl"
DATASET_SOURCE = "UHL_SYNTHETIC_TRIAGE_VITALS_ACUITY_FINAL_20260402"
DATASET_REVISION = "corrected_acuity_vitals_20260810"
DATASET_SHA256 = "f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c"
SOURCE_CSV_SHA256 = "d77c3d8c443bb802d6f39b4857d0fef05d179166095378796f424652e28716ec"
MODEL_SHA256 = "7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b"
FEATURE_SCHEMA_HASH = "fd3d1365fe744d5eb75a83b8cfb1ebf9b84695a405c802b633bd2bb78f89debd"
TRIAGE_TIMEZONE = "Europe/Dublin"
CACHE_SCHEMA_VERSION = 3

SOURCE_COLUMNS = (
    "AttendanceID",
    "DATE",
    "year",
    "month",
    "hour",
    "time_bin",
    "season",
    "EdLocationName_base",
    "EdLocationName_token",
    "PresentingComplaint_base",
    "PresentingComplaint_token",
    "Age",
    "HoursinEd",
    "Admitted",
    "Over6Hours",
    "Over9Hours",
    "Over24",
    "Over75",
    "TTT_minutes",
    "TTC_minutes",
    "master_estimated_acuity",
    "temperature",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "pain",
)

MODEL_INPUT_COLUMNS = (
    "age",
    "month",
    "hour",
    "time_bin",
    "season",
    "presenting_complaint",
    "temperature",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "pain",
)

MODEL_FEATURE_RANGES = {
    "age": (0.0, 110.0),
    "temperature": (80.0, 110.0),
    "heartrate": (1.0, 300.0),
    "resprate": (1.0, 90.0),
    "o2sat": (1.0, 100.0),
    "sbp": (1.0, 300.0),
    "dbp": (1.0, 220.0),
    "pain": (0.0, 10.0),
}

MEASUREMENT_UNITS = {
    "temperature": "F",
    "heartrate": "bpm",
    "resprate": "breaths/min",
    "o2sat": "%",
    "sbp": "mmHg",
    "dbp": "mmHg",
    "pain": "0-10",
}

EXPECTED_SOURCE_ROWS = 777_176
EXPECTED_MODEL_ROWS = 777_174
EXPECTED_TARGET_COUNTS = {"1": 3136, "2": 184773, "3": 527974, "4": 57369, "5": 3924}
OUT_OF_MODEL_SCOPE_COMPLAINTS = frozenset({"DOA"})


def model_feature_schema_hash() -> str:
    return hashlib.sha256("\n".join(MODEL_INPUT_COLUMNS).encode("utf-8")).hexdigest()
