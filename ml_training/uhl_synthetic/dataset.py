"""Loader and contract checks for the final UHL synthetic triage-vitals dataset.

The final training file is synthetic. Its label, ``master_estimated_acuity``,
is treated as the research "gospel" target for this prototype. The model inputs
are restricted to information available at triage in this synthetic design:

    Age, month, hour, time_bin, season, PresentingComplaint_base,
    temperature, heartrate, resprate, o2sat, sbp, dbp, pain

Outcome/process columns such as admission, ED length of stay, time to triage,
and time to clinician are never exposed as predictors.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

DATASET_SOURCE = "UHL_SYNTHETIC_TRIAGE_VITALS_ACUITY_FINAL_20260402"
TRIAGE_TIMEZONE = "Europe/Dublin"
TRIAGE_TIMESTAMP_POLICY = (
    "naive Europe/Dublin timestamps: ambiguous fall-back times use standard "
    "time (fold=1); nonexistent spring-forward times shift forward to the "
    "first valid local time; timezone-aware inputs convert to Europe/Dublin"
)
TARGET_COLUMN = "master_estimated_acuity"
SOURCE_INPUT_COLUMNS = [
    "Age",
    "month",
    "hour",
    "time_bin",
    "season",
    "PresentingComplaint_base",
    "temperature",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "pain",
]
MODEL_INPUT_COLUMNS = [
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
]
VITAL_SOURCE_COLUMNS = [
    "temperature",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "pain",
]
FEATURE_VALUE_RANGES = {
    "age": (0, 130),
    "month": (1, 12),
    "hour": (0, 23),
    "temperature": (80, 110),
    "heartrate": (1, 300),
    "resprate": (1, 90),
    "o2sat": (1, 100),
    "sbp": (1, 300),
    "dbp": (1, 220),
    "pain": (0, 10),
}
OUTCOME_LEAKAGE_COLUMNS = [
    "HoursinEd",
    "Admitted",
    "Over6Hours",
    "Over9Hours",
    "Over24",
    "Over75",
    "TTT_minutes",
    "TTC_minutes",
]
REQUIRED_COLUMNS = [
    "AttendanceID",
    "DATE",
    *SOURCE_INPUT_COLUMNS,
    TARGET_COLUMN,
]
SPLITS = ("TRAIN", "VALIDATION", "TEST")
EXPECTED_LABELS = (1, 2, 3, 4, 5)
OUT_OF_MODEL_SCOPE_COMPLAINTS = ("DOA",)
_NUMERIC_SOURCE_TO_MODEL = {
    "Age": "age",
    "month": "month",
    "hour": "hour",
    "temperature": "temperature",
    "heartrate": "heartrate",
    "resprate": "resprate",
    "o2sat": "o2sat",
    "sbp": "sbp",
    "dbp": "dbp",
    "pain": "pain",
}


class UhlDatasetError(RuntimeError):
    """Raised when the UHL file cannot be used safely for training."""


@dataclass(frozen=True)
class UhlDataset:
    X: pd.DataFrame
    y: np.ndarray
    sample_weight: np.ndarray
    row_ids: list[str]
    split: np.ndarray
    metadata: dict


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_assignment_hash(row_ids: Iterable[str], split: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for row_id, split_name in zip(row_ids, split):
        digest.update(str(row_id).encode("utf-8", errors="replace"))
        digest.update(b"\t")
        digest.update(str(split_name).encode("utf-8", errors="replace"))
        digest.update(b"\n")
    return digest.hexdigest()


def feature_schema_hash(columns: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(columns).encode("utf-8")).hexdigest()


def _as_bool(series: pd.Series) -> pd.Series:
    normalized = series.astype("string").str.strip().str.upper()
    parsed = normalized.map({"TRUE": True, "FALSE": False})
    if parsed.isna().any():
        bad = sorted(normalized[parsed.isna()].dropna().unique().tolist())
        raise UhlDatasetError(
            "training_eligible must contain only TRUE/FALSE; "
            f"unexpected values: {bad[:10]}"
        )
    return parsed.astype(bool)


def _non_empty_unique(series: pd.Series) -> set[str]:
    values = series.dropna().astype(str).str.strip()
    return {value for value in values if value}


def _read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    suffixes = "".join(path.suffixes).lower()
    if suffix in {".xlsx", ".xls"} or suffixes.endswith(".xlsx"):
        sheet_name = os.environ.get("UHL_EXCEL_SHEET", "in")
        try:
            return pd.read_excel(path, sheet_name=sheet_name)
        except ValueError as exc:
            if "Worksheet named" not in str(exc):
                raise
            return pd.read_excel(path, sheet_name=0)
    return pd.read_csv(path, compression="infer", low_memory=False)


def _validate_required_columns(df: pd.DataFrame, path: Path) -> None:
    if not path.is_file():
        raise UhlDatasetError(f"UHL data file not found: {path}")
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise UhlDatasetError(f"UHL dataset is missing required column(s): {missing}")


def _normalise_features(df: pd.DataFrame) -> pd.DataFrame:
    features = pd.DataFrame(index=df.index)
    for source, model_column in _NUMERIC_SOURCE_TO_MODEL.items():
        features[model_column] = pd.to_numeric(df[source], errors="coerce")
    features["time_bin"] = (
        df["time_bin"].fillna("").astype(str).str.strip().str.upper()
    )
    features["season"] = (
        df["season"].fillna("").astype(str).str.strip().str.upper()
    )
    features["presenting_complaint"] = (
        df["PresentingComplaint_base"].fillna("").astype(str).str.strip().str.upper()
    )
    return features[MODEL_INPUT_COLUMNS]


def _validate_temporal_consistency(
    df: pd.DataFrame,
    parsed_dates: pd.Series,
) -> None:
    """Reject train/serve skew in the four derived triage-time fields.

    Serving derives month, hour, time_bin, and season from ``arrival_time``. If the
    training file carries a contradictory value, the fitted model would learn
    combinations that production can never create.
    """
    month = pd.to_numeric(df["month"], errors="coerce")
    hour = pd.to_numeric(df["hour"], errors="coerce")
    if month.isna().any() or hour.isna().any():
        raise UhlDatasetError("Eligible UHL rows contain non-numeric month/hour values")
    if not np.isclose(month, np.round(month)).all() or not np.isclose(
        hour, np.round(hour)
    ).all():
        raise UhlDatasetError("Eligible UHL rows contain non-integer month/hour values")
    month = month.round().astype(int)
    hour = hour.round().astype(int)
    dates = parsed_dates.loc[df.index]
    month_mismatch = month.ne(dates.dt.month)
    hour_mismatch = hour.ne(dates.dt.hour)
    if month_mismatch.any() or hour_mismatch.any():
        raise UhlDatasetError(
            "Eligible UHL rows contain month/hour values inconsistent with DATE "
            f"(month={int(month_mismatch.sum())}, hour={int(hour_mismatch.sum())})"
        )
    def expected_time_bin_value(value: int) -> str:
        if value <= 2:
            return "LATE NIGHT"
        if value <= 6:
            return "EARLY MORNING"
        if value <= 11:
            return "MORNING"
        if value <= 16:
            return "AFTERNOON"
        if value <= 20:
            return "EVENING"
        return "NIGHT"

    def expected_season_value(value: int) -> str:
        if value in {12, 1, 2}:
            return "WINTER"
        if value in {3, 4, 5}:
            return "SPRING"
        if value in {6, 7, 8}:
            return "SUMMER"
        return "AUTUMN"

    expected_time_bin = hour.map(expected_time_bin_value)
    actual_time_bin = df["time_bin"].fillna("").astype(str).str.strip().str.upper()
    expected_season = month.map(expected_season_value)
    actual_season = df["season"].fillna("").astype(str).str.strip().str.upper()
    time_bin_mismatch = actual_time_bin.ne(expected_time_bin)
    season_mismatch = actual_season.ne(expected_season)
    if time_bin_mismatch.any() or season_mismatch.any():
        raise UhlDatasetError(
            "Eligible UHL rows contain derived time fields inconsistent with "
            f"month/hour (time_bin={int(time_bin_mismatch.sum())}, "
            f"season={int(season_mismatch.sum())})"
        )


def _resolve_training_timestamps(
    parsed_dates: pd.Series,
    index: pd.Index,
) -> tuple[pd.Series, dict]:
    """Apply the exact deterministic DST policy shared with serving."""
    naive = pd.DatetimeIndex(parsed_dates.loc[index])
    ambiguous_probe = naive.tz_localize(
        TRIAGE_TIMEZONE,
        ambiguous="NaT",
        nonexistent="shift_forward",
    )
    nonexistent_probe = naive.tz_localize(
        TRIAGE_TIMEZONE,
        ambiguous=False,  # type: ignore[arg-type]  # pandas accepts a scalar bool
        nonexistent="NaT",
    )
    resolved = naive.tz_localize(
        TRIAGE_TIMEZONE,
        ambiguous=False,  # type: ignore[arg-type]  # pandas accepts a scalar bool
        nonexistent="shift_forward",
    )
    return pd.Series(resolved, index=index), {
        "policy": TRIAGE_TIMESTAMP_POLICY,
        "timezone": TRIAGE_TIMEZONE,
        "ambiguous_naive_count": int(pd.isna(ambiguous_probe).sum()),
        "nonexistent_naive_count": int(pd.isna(nonexistent_probe).sum()),
        "resolution_is_deterministic": True,
    }


def _validate_feature_values(features: pd.DataFrame) -> None:
    numeric_columns = list(_NUMERIC_SOURCE_TO_MODEL.values())
    for column in numeric_columns:
        values = pd.to_numeric(features[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise UhlDatasetError(
                f"Eligible UHL rows contain missing or non-finite {column} values"
            )
        features[column] = values

    for column, (low, high) in FEATURE_VALUE_RANGES.items():
        values = features[column]
        if (~values.between(low, high)).any():
            raise UhlDatasetError(
                f"Eligible UHL rows contain {column} values outside {low}..{high}"
            )

    for column in ("time_bin", "season", "presenting_complaint"):
        values = features[column].fillna("").astype(str).str.strip()
        if values.eq("").any():
            raise UhlDatasetError(f"Eligible UHL rows contain blank {column} values")


def _synthetic_vital_validity_mask(df: pd.DataFrame) -> tuple[pd.Series, dict[str, int]]:
    valid = pd.Series(True, index=df.index)
    invalid_counts: dict[str, int] = {}
    for column in VITAL_SOURCE_COLUMNS:
        low, high = FEATURE_VALUE_RANGES[column]
        values = pd.to_numeric(df[column], errors="coerce")
        finite = pd.Series(np.isfinite(values.to_numpy(dtype=float)), index=df.index)
        column_valid = values.notna() & finite & values.between(low, high)
        invalid_count = int((~column_valid).sum())
        if invalid_count:
            invalid_counts[column] = invalid_count
        valid &= column_valid
    return valid, invalid_counts


def _out_of_model_scope_mask(df: pd.DataFrame) -> tuple[pd.Series, dict[str, int]]:
    """Exclude deterministic pathways that the ML model must never predict.

    The only DOA records in this source have all-zero physiological values and
    acuity 1.  Treating those zeros as ordinary vitals is invalid, while
    silently accepting DOA at serving time would send an unseen category into
    the fitted encoder.  DOA therefore follows a deterministic non-ML pathway.
    """
    complaint = (
        df["PresentingComplaint_base"].fillna("").astype(str).str.strip().str.upper()
    )
    out_of_scope = complaint.isin(OUT_OF_MODEL_SCOPE_COMPLAINTS)
    counts = {
        code: int((complaint == code).sum())
        for code in OUT_OF_MODEL_SCOPE_COMPLAINTS
        if int((complaint == code).sum())
    }
    return ~out_of_scope, counts


def _coerce_target(df: pd.DataFrame) -> np.ndarray:
    y = pd.to_numeric(df[TARGET_COLUMN], errors="coerce")
    if y.isna().any():
        bad = int(y.isna().sum())
        raise UhlDatasetError(f"{bad} eligible rows have non-numeric acuity labels")
    arr = y.to_numpy(dtype=float)
    if not np.isclose(arr, np.round(arr)).all():
        raise UhlDatasetError("Eligible UHL rows contain non-integer acuity labels")
    labels = np.round(arr).astype(int)
    invalid_labels = sorted(set(int(v) for v in np.unique(labels)) - set(EXPECTED_LABELS))
    if invalid_labels:
        raise UhlDatasetError(f"Eligible UHL rows contain invalid acuity labels: {invalid_labels}")
    missing_labels = sorted(set(EXPECTED_LABELS) - set(int(v) for v in np.unique(labels)))
    if missing_labels:
        raise UhlDatasetError(f"Eligible UHL data is missing acuity class(es): {missing_labels}")
    return labels


def _make_stratified_split(y: np.ndarray, *, seed: int) -> np.ndarray:
    rng = np.random.default_rng(int(seed))
    split = np.empty(len(y), dtype=object)
    for label in EXPECTED_LABELS:
        idx = np.flatnonzero(y == int(label))
        if len(idx) < 3:
            raise UhlDatasetError(
                f"Cannot create TRAIN/VALIDATION/TEST split: class {label} has only {len(idx)} row(s)"
            )
        rng.shuffle(idx)
        n_test = max(1, int(round(len(idx) * 0.15)))
        n_val = max(1, int(round(len(idx) * 0.15)))
        if len(idx) - n_val - n_test < 1:
            n_test = 1
            n_val = 1
        n_train = len(idx) - n_val - n_test
        split[idx[:n_train]] = "TRAIN"
        split[idx[n_train:n_train + n_val]] = "VALIDATION"
        split[idx[n_train + n_val:]] = "TEST"
    return split


def _resolve_split(df: pd.DataFrame, y: np.ndarray, *, seed: int) -> tuple[np.ndarray, str]:
    if "data_split" in df.columns:
        split = df["data_split"].fillna("").astype(str).str.strip().str.upper()
        invalid = sorted(set(split.unique()) - set(SPLITS))
        if invalid:
            raise UhlDatasetError(f"UHL data_split contains invalid value(s): {invalid}")
        return split.to_numpy(dtype=object), "source_data_split"
    return _make_stratified_split(y, seed=seed), "deterministic_stratified_row_split"


def _sample_by_split_and_class(
    df: pd.DataFrame,
    *,
    per_split_class: int,
    seed: int,
) -> pd.DataFrame:
    """Quick-test sample that PRESERVES the real class imbalance.

    The previous implementation drew the same fixed number of rows from every
    class, so the quick training set was perfectly balanced. That made the
    smoke test blind to exactly the bugs it is supposed to catch: SMOTE
    generated zero synthetic rows and class weighting had no effect, so a
    broken resampler or weighting path passed preflight silently.

    Here the per-split budget is allocated in proportion to the real class
    distribution, with a small floor so every class still appears in every
    split (the split-coverage check and the samplers both need that).
    """
    if per_split_class <= 0:
        return df
    min_rows = max(2, int(os.environ.get("UHL_QUICK_MIN_ROWS_PER_CLASS", "3")))
    pieces = []
    for split_name in SPLITS:
        split_df = df[df["data_split"] == split_name]
        if split_df.empty:
            continue
        budget = int(per_split_class) * len(EXPECTED_LABELS)
        buckets = {
            label: split_df[split_df[TARGET_COLUMN].astype(int) == label]
            for label in EXPECTED_LABELS
        }
        present = {label: bucket for label, bucket in buckets.items() if not bucket.empty}
        if not present:
            continue
        total = sum(len(bucket) for bucket in present.values())
        allocation: dict[int, int] = {}
        for label, bucket in present.items():
            share = int(round(budget * (len(bucket) / total)))
            allocation[label] = max(min(min_rows, len(bucket)), min(share, len(bucket)))
        # Trim proportionally if the floors pushed the split over budget, but
        # never below the floor and never to zero.
        while sum(allocation.values()) > budget:
            label = max(
                allocation,
                key=lambda key: (allocation[key], -int(key)),
            )
            if allocation[label] <= min(min_rows, len(present[label])):
                break
            allocation[label] -= 1
        for label, count in allocation.items():
            if count <= 0:
                continue
            pieces.append(present[label].sample(n=count, random_state=seed + int(label)))
    if not pieces:
        raise UhlDatasetError("Quick-test sampling found no eligible rows")
    sampled = pd.concat(pieces, axis=0).sort_index()
    return sampled.reset_index(drop=True)


_PAEDIATRIC_COMPLAINTS = {"UNWELL CHILD", "LIMPING CHILD"}
_ADULT_COMPLAINTS = {"UNWELL ADULT"}
_PAEDIATRIC_AGE_LIMIT = 16


def _dataset_integrity_audit(df: pd.DataFrame) -> dict:
    """Count internal contradictions in the source data WITHOUT deleting rows.

    These rows are kept: the acuity label is the user's research target and
    silently dropping tens of thousands of rows would change the experiment.
    The counts are recorded so the run's provenance states plainly what is in
    the training data rather than implying it is fully coherent.
    """
    complaint = df["PresentingComplaint_base"].fillna("").astype(str).str.strip().str.upper()
    age = pd.to_numeric(df["Age"], errors="coerce")
    acuity = pd.to_numeric(df[TARGET_COLUMN], errors="coerce")

    paediatric_complaint_adult_age = int(
        (complaint.isin(_PAEDIATRIC_COMPLAINTS) & (age >= _PAEDIATRIC_AGE_LIMIT)).sum()
    )
    adult_complaint_paediatric_age = int(
        (complaint.isin(_ADULT_COMPLAINTS) & (age < _PAEDIATRIC_AGE_LIMIT)).sum()
    )
    pregnancy_implausible_age = int(
        ((complaint == "PREGNANCY") & ((age < 10) | (age > 60))).sum()
    )

    temperature_c = (pd.to_numeric(df["temperature"], errors="coerce") - 32.0) * 5.0 / 9.0
    heartrate = pd.to_numeric(df["heartrate"], errors="coerce")
    resprate = pd.to_numeric(df["resprate"], errors="coerce")
    o2sat = pd.to_numeric(df["o2sat"], errors="coerce")
    sbp = pd.to_numeric(df["sbp"], errors="coerce")
    news2_red = (
        (resprate <= 8) | (resprate >= 25)
        | (o2sat <= 91)
        | (temperature_c <= 35.0)
        | (sbp <= 90) | (sbp >= 220)
        | (heartrate <= 40) | (heartrate >= 131)
    )
    low_acuity_with_red_vital = int(
        ((age >= _PAEDIATRIC_AGE_LIMIT) & news2_red & acuity.isin([4, 5])).sum()
    )

    duplicate_complaint_codes = sorted(
        {
            code
            for code in complaint.unique()
            if code and code.replace("INSECTS", "INSECT") != code
            and code.replace("INSECTS", "INSECT") in set(complaint.unique())
        }
    )

    return {
        "rows_audited": int(len(df)),
        "rows_removed_by_this_audit": 0,
        "audit_is_advisory_only": True,
        "paediatric_complaint_with_adult_age": paediatric_complaint_adult_age,
        "adult_complaint_with_paediatric_age": adult_complaint_paediatric_age,
        "pregnancy_complaint_with_implausible_age": pregnancy_implausible_age,
        "adult_low_acuity_with_news2_red_vital": low_acuity_with_red_vital,
        "near_duplicate_complaint_codes": duplicate_complaint_codes,
        "note": (
            "These rows are retained. master_estimated_acuity is the research "
            "target supplied with the dataset; these counts describe known "
            "internal contradictions in the synthetic source data and must be "
            "quoted alongside any performance claim."
        ),
    }


def _split_class_counts(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    return {
        split_name: {
            str(label): int(
                ((df["data_split"] == split_name) & (df[TARGET_COLUMN] == label)).sum()
            )
            for label in EXPECTED_LABELS
        }
        for split_name in SPLITS
    }


def _validate_split_coverage(df: pd.DataFrame) -> None:
    for split_name in SPLITS:
        split_labels = set(
            int(v) for v in df.loc[df["data_split"] == split_name, TARGET_COLUMN].unique()
        )
        missing = sorted(set(EXPECTED_LABELS) - split_labels)
        if missing:
            raise UhlDatasetError(
                f"Split {split_name} is missing acuity class(es): {missing}"
            )


def _resolve_sample_weight(df: pd.DataFrame) -> tuple[np.ndarray, str]:
    if "label_confidence_weight" not in df.columns:
        return np.ones(len(df), dtype=float), "uniform_1.0_no_confidence_weight_column"
    weights = pd.to_numeric(df["label_confidence_weight"], errors="coerce")
    if weights.isna().any() or not np.isfinite(weights.to_numpy(dtype=float)).all():
        raise UhlDatasetError("Eligible UHL rows contain missing or non-finite label_confidence_weight values")
    if (weights < 0).any():
        raise UhlDatasetError("Eligible UHL rows contain negative label_confidence_weight values")
    return weights.to_numpy(dtype=float), "label_confidence_weight"


def load_uhl_dataset(
    path: Path,
    *,
    quick_test: bool = False,
    quick_rows_per_split_class: int = 40,
    seed: int = 42,
) -> UhlDataset:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise UhlDatasetError(f"UHL data file not found: {path}")
    df = _read_table(path)
    _validate_required_columns(df, path)

    if "training_eligible" in df.columns:
        eligible = _as_bool(df["training_eligible"])
        eligibility_source = "training_eligible"
    else:
        eligible = pd.Series(True, index=df.index)
        eligibility_source = "all_rows_no_training_eligible_column"
    df = df.loc[eligible].copy()
    if df.empty:
        raise UhlDatasetError("No training-eligible UHL rows found")

    row_ids_series = df["AttendanceID"].fillna("").astype(str).str.strip()
    if row_ids_series.eq("").any():
        raise UhlDatasetError("Eligible UHL rows contain missing AttendanceID values")
    if row_ids_series.duplicated().any():
        dupes = row_ids_series[row_ids_series.duplicated()].head(5).tolist()
        raise UhlDatasetError(f"Eligible UHL rows contain duplicate AttendanceID values: {dupes}")

    parsed_dates = pd.to_datetime(df["DATE"], errors="coerce")
    if parsed_dates.isna().any():
        raise UhlDatasetError(
            f"{int(parsed_dates.isna().sum())} eligible UHL rows have invalid DATE values"
        )

    rows_after_eligibility_filter = int(len(df))
    in_scope, out_of_scope_counts = _out_of_model_scope_mask(df)
    rows_removed_out_of_model_scope = int((~in_scope).sum())
    if rows_removed_out_of_model_scope:
        df = df.loc[in_scope].copy()
        if df.empty:
            raise UhlDatasetError("No in-scope UHL rows remain after deterministic-path filtering")

    vital_valid, invalid_vital_counts = _synthetic_vital_validity_mask(df)
    rows_removed_invalid_synthetic_vitals = int((~vital_valid).sum())
    if rows_removed_invalid_synthetic_vitals:
        df = df.loc[vital_valid].copy()
        if df.empty:
            raise UhlDatasetError(
                "No eligible UHL rows remain after invalid synthetic vital filtering"
            )

    _validate_temporal_consistency(df, parsed_dates)
    resolved_dates, timestamp_resolution = _resolve_training_timestamps(
        parsed_dates,
        df.index,
    )
    # The source fields must first agree with the source DATE (validated
    # above). The model fields are then derived from the explicitly resolved
    # local timestamp, so spring-forward/nonexistent cases are handled exactly
    # as they are at serving time.
    resolved_month = resolved_dates.dt.month.astype(int)
    resolved_hour = resolved_dates.dt.hour.astype(int)
    df["month"] = resolved_month
    df["hour"] = resolved_hour
    df["time_bin"] = resolved_hour.map(
        lambda value: (
            "LATE NIGHT" if value <= 2 else
            "EARLY MORNING" if value <= 6 else
            "MORNING" if value <= 11 else
            "AFTERNOON" if value <= 16 else
            "EVENING" if value <= 20 else "NIGHT"
        )
    )
    df["season"] = resolved_month.map(
        lambda value: (
            "WINTER" if value in {12, 1, 2} else
            "SPRING" if value in {3, 4, 5} else
            "SUMMER" if value in {6, 7, 8} else "AUTUMN"
        )
    )

    y_arr = _coerce_target(df)
    df[TARGET_COLUMN] = y_arr
    split_arr, split_kind = _resolve_split(df, y_arr, seed=seed)
    df["data_split"] = split_arr

    rows_after_synthetic_vital_filter = int(len(df))
    if quick_test:
        df = _sample_by_split_and_class(
            df,
            per_split_class=quick_rows_per_split_class,
            seed=seed,
        )
        y_arr = df[TARGET_COLUMN].astype(int).to_numpy()
        split_arr = df["data_split"].astype(str).to_numpy()

    _validate_split_coverage(df)
    X = _normalise_features(df)
    _validate_feature_values(X)
    fitted_age_support = {
        "minimum": float(X["age"].min()),
        "maximum": float(X["age"].max()),
        "outside_support_policy": "reject_and_route_to_deterministic_review",
    }

    weights, weight_source = _resolve_sample_weight(df)
    row_ids = df["AttendanceID"].fillna("").astype(str).str.strip().tolist()
    split_arr = df["data_split"].astype(str).to_numpy()
    class_counts = _split_class_counts(df)
    zero_counts = {
        column: int((pd.to_numeric(df[column], errors="coerce") == 0).sum())
        for column in VITAL_SOURCE_COLUMNS
    }
    optional_status = (
        sorted(_non_empty_unique(df["data_status"]))
        if "data_status" in df.columns
        else None
    )
    integrity_audit = _dataset_integrity_audit(df)
    metadata = {
        "dataset_source": DATASET_SOURCE,
        "dataset_path": str(path),
        "dataset_sha256": sha256_file(path),
        "rows_after_eligibility_filter": rows_after_eligibility_filter,
        "rows_removed_out_of_model_scope": rows_removed_out_of_model_scope,
        "out_of_model_scope_complaint_counts": out_of_scope_counts,
        "out_of_model_scope_policy": (
            "DOA is excluded from model fitting and serving. It requires a "
            "deterministic emergency/deceased pathway outside ML."
        ),
        "rows_removed_invalid_synthetic_vitals": rows_removed_invalid_synthetic_vitals,
        "invalid_synthetic_vital_counts": invalid_vital_counts,
        "rows_after_synthetic_vital_filter": rows_after_synthetic_vital_filter,
        "rows_used_for_training": int(len(df)),
        "quick_test_mode": bool(quick_test),
        "quick_rows_per_split_class": (
            int(quick_rows_per_split_class) if quick_test else None
        ),
        "eligibility_source": eligibility_source,
        "split_kind": split_kind,
        "split_seed": int(seed),
        "split_counts": {split_name: int((split_arr == split_name).sum()) for split_name in SPLITS},
        "class_counts_by_split": class_counts,
        "target_column": TARGET_COLUMN,
        "source_input_columns": list(SOURCE_INPUT_COLUMNS),
        "approved_model_inputs": list(SOURCE_INPUT_COLUMNS),
        "model_input_columns": list(MODEL_INPUT_COLUMNS),
        "synthetic_triage_vitals_used": True,
        "triage_timezone": TRIAGE_TIMEZONE,
        "triage_timestamp_policy": TRIAGE_TIMESTAMP_POLICY,
        "triage_timestamp_resolution": timestamp_resolution,
        "fitted_age_support": fitted_age_support,
        "temporal_consistency_validated": True,
        "month_hour_match_date": True,
        "time_bin_season_match_month_hour": True,
        "seen_presenting_complaints": sorted(
            set(X["presenting_complaint"].astype(str).str.strip().str.upper())
        ),
        "synthetic_triage_vitals_policy": (
            "Synthetic vital columns are included as triage-time predictors by "
            "the user's final experimental design; this is synthetic research "
            "data and not clinician-validated patient data."
        ),
        "excluded_outcome_columns": [
            column for column in OUTCOME_LEAKAGE_COLUMNS if column in df.columns
        ],
        "sample_weight_source": weight_source,
        "vital_zero_counts": zero_counts,
        "dataset_integrity_audit": integrity_audit,
        "data_status_values": optional_status,
        "split_assignment_sha256": split_assignment_hash(row_ids, split_arr),
        "feature_schema_hash": feature_schema_hash(MODEL_INPUT_COLUMNS),
        "patient_level_split": False,
        "patient_level_split_note": (
            "The final synthetic UHL file supplies AttendanceID but no longitudinal "
            "patient identifier; the split is row-level and stratified by acuity "
            "when no source data_split column is present."
        ),
        "contains_raw_patient_rows": False,
    }
    return UhlDataset(
        X=X,
        y=y_arr,
        sample_weight=weights,
        row_ids=row_ids,
        split=split_arr,
        metadata=metadata,
    )
