"""Feature pipelines for final UHL synthetic triage-vitals acuity training."""
from __future__ import annotations

import os
import re
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from ml_training.uhl_synthetic.dataset import FEATURE_VALUE_RANGES, MODEL_INPUT_COLUMNS

NUMERIC_COLUMNS = [
    "age",
    "month",
    "hour",
    "temperature",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "pain",
]
CATEGORICAL_COLUMNS = ["time_bin", "season", "presenting_complaint"]
SMOTENC_BASE_COLUMNS = [
    column for column in MODEL_INPUT_COLUMNS if column not in {"time_bin", "season"}
]
SMOTENC_CATEGORICAL_COLUMNS = ["month", "hour", "presenting_complaint"]
TEXT_COLUMN = "presenting_complaint"
RAW_INPUT_TYPE = "uhl_synthetic_triage_vitals_dataframe"
ARTIFACT_CONTRACT_VERSION = 8
TIME_FEATURE_NAMES = [
    *NUMERIC_COLUMNS,
    "month_sin",
    "month_cos",
    "hour_sin",
    "hour_cos",
]

FORBIDDEN_FEATURE_TERMS = (
    "acuity",
    "target",
    "label",
    "admit",
    "admitted",
    "over6",
    "over9",
    "over24",
    "hoursined",
    "ttt",
    "ttc",
    "review",
    "confidence",
    "support",
    "status",
    "restriction",
)


def categorical_column_indices() -> list[int]:
    """Positional indices of the categorical columns in the cleaned raw frame.

    SMOTENC needs these so that synthetic minority rows copy a real category
    value instead of interpolating between one-hot indicator columns.
    """
    return [MODEL_INPUT_COLUMNS.index(column) for column in CATEGORICAL_COLUMNS]


def time_bin_from_hour_value(hour: int) -> str:
    hour = int(hour)
    if hour < 0 or hour > 23:
        raise ValueError("hour must be in 0..23")
    if hour <= 2:
        return "LATE NIGHT"
    if hour <= 6:
        return "EARLY MORNING"
    if hour <= 11:
        return "MORNING"
    if hour <= 16:
        return "AFTERNOON"
    if hour <= 20:
        return "EVENING"
    return "NIGHT"


def season_from_month_value(month: int) -> str:
    month = int(month)
    if month < 1 or month > 12:
        raise ValueError("month must be in 1..12")
    if month in {12, 1, 2}:
        return "WINTER"
    if month in {3, 4, 5}:
        return "SPRING"
    if month in {6, 7, 8}:
        return "SUMMER"
    return "AUTUMN"


def validate_uhl_input_columns(columns: Iterable[str] | None = None) -> None:
    cols = list(MODEL_INPUT_COLUMNS if columns is None else columns)
    if cols != MODEL_INPUT_COLUMNS:
        raise ValueError(
            "UHL raw dataframe schema mismatch; expected exactly "
            f"{MODEL_INPUT_COLUMNS}, got {cols}"
        )


def _normalise_text(value: object) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[_/;-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_frame(X) -> pd.DataFrame:
    if isinstance(X, pd.DataFrame):
        validate_uhl_input_columns(X.columns)
        frame = X.loc[:, MODEL_INPUT_COLUMNS].copy()
    else:
        arr = np.asarray(X, dtype=object)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.ndim != 2 or arr.shape[1] != len(MODEL_INPUT_COLUMNS):
            raise ValueError(
                "UHL raw array schema mismatch; expected "
                f"{len(MODEL_INPUT_COLUMNS)} columns, got shape {arr.shape}"
            )
        frame = pd.DataFrame(arr, columns=MODEL_INPUT_COLUMNS)
    for col in NUMERIC_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        values = frame[col]
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"UHL input column {col!r} contains missing or non-finite values")
    for col, (low, high) in FEATURE_VALUE_RANGES.items():
        if (~frame[col].between(low, high)).any():
            raise ValueError(f"UHL input column {col!r} must be in {low}..{high}")
    for col in CATEGORICAL_COLUMNS:
        frame[col] = frame[col].map(_normalise_text)
        if frame[col].eq("").any():
            raise ValueError(f"UHL input column {col!r} contains missing or blank values")
    return frame


def _add_cyclical_time(values):
    arr = np.asarray(values, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim != 2 or arr.shape[1] != len(NUMERIC_COLUMNS):
        raise ValueError(
            "UHL numeric transformer expected "
            f"{len(NUMERIC_COLUMNS)} columns, got shape {arr.shape}"
        )
    month = arr[:, NUMERIC_COLUMNS.index("month")]
    hour = arr[:, NUMERIC_COLUMNS.index("hour")]
    month_angle = 2.0 * np.pi * np.nan_to_num(month, nan=0.0) / 12.0
    hour_angle = 2.0 * np.pi * np.nan_to_num(hour, nan=0.0) / 24.0
    return np.column_stack([
        arr,
        np.sin(month_angle),
        np.cos(month_angle),
        np.sin(hour_angle),
        np.cos(hour_angle),
    ])


def _cyclical_time_feature_names(transformer, input_features=None):
    return np.asarray(TIME_FEATURE_NAMES, dtype=object)


class FrameCleaner(BaseEstimator, TransformerMixin):
    def fit(self, X, y=None):
        _clean_frame(X)
        # Stateless transformer, but scikit-learn >= 1.6 calls check_is_fitted()
        # on pipeline steps, and that helper looks for a trailing-underscore
        # attribute. Without this the whole pipeline raises NotFittedError on
        # newer scikit-learn even though fit() succeeded.
        self.n_features_in_ = len(MODEL_INPUT_COLUMNS)
        self.is_fitted_ = True
        return self

    def __sklearn_is_fitted__(self) -> bool:
        return bool(getattr(self, "is_fitted_", False))

    def transform(self, X):
        return _clean_frame(X)

    def get_feature_names_out(self, input_features=None):
        return np.asarray(MODEL_INPUT_COLUMNS, dtype=object)


class TemporalConsistentSMOTENC(BaseEstimator):
    """SMOTENC that cannot synthesize impossible derived time combinations.

    `time_bin` and `season` are deterministic functions of `hour` and `month`
    in both source data and serving. They are excluded from neighbour synthesis,
    month/hour are treated as discrete categories, and both derived fields are
    rebuilt after resampling.
    """

    def __init__(self, *, random_state=None, k_neighbors=5, sampling_strategy="auto"):
        self.random_state = random_state
        self.k_neighbors = k_neighbors
        self.sampling_strategy = sampling_strategy

    def fit_resample(self, X, y):
        from imblearn.over_sampling import SMOTENC

        frame = _clean_frame(X)
        base = frame.loc[:, SMOTENC_BASE_COLUMNS].copy()
        base["month"] = base["month"].round().astype(int)
        base["hour"] = base["hour"].round().astype(int)
        categorical_indices = [
            SMOTENC_BASE_COLUMNS.index(column)
            for column in SMOTENC_CATEGORICAL_COLUMNS
        ]
        sampler = SMOTENC(
            categorical_features=categorical_indices,
            random_state=self.random_state,
            k_neighbors=self.k_neighbors,
            sampling_strategy=self.sampling_strategy,
        )
        resampled, y_resampled = sampler.fit_resample(base, y)
        if not isinstance(resampled, pd.DataFrame):
            resampled = pd.DataFrame(resampled, columns=SMOTENC_BASE_COLUMNS)
        resampled["month"] = pd.to_numeric(resampled["month"], errors="raise").round().astype(int)
        resampled["hour"] = pd.to_numeric(resampled["hour"], errors="raise").round().astype(int)
        resampled["time_bin"] = resampled["hour"].map(time_bin_from_hour_value)
        resampled["season"] = resampled["month"].map(season_from_month_value)
        restored = _clean_frame(resampled.loc[:, MODEL_INPUT_COLUMNS])
        self.sampler_ = sampler
        self.sampling_strategy_ = sampler.sampling_strategy_
        self.synthetic_temporal_invariants_ = {
            "time_bin_derived_from_hour": True,
            "season_derived_from_month": True,
            "month_and_hour_treated_as_discrete": True,
        }
        self.is_fitted_ = True
        return restored, y_resampled

    def __sklearn_is_fitted__(self) -> bool:
        return bool(getattr(self, "is_fitted_", False))


class SafeTruncatedSVD(BaseEstimator, TransformerMixin):
    """TruncatedSVD that lowers n_components for tiny smoke-test matrices."""

    def __init__(self, n_components: int = 128, random_state: int | None = None):
        self.n_components = int(n_components)
        self.random_state = random_state
        self.model_: TruncatedSVD | None = None
        self.actual_n_components_: int | None = None

    def fit(self, X, y=None):
        n_samples, n_features = X.shape
        max_components = max(1, min(n_samples - 1, n_features - 1))
        self.actual_n_components_ = max(1, min(self.n_components, max_components))
        self.model_ = TruncatedSVD(
            n_components=self.actual_n_components_,
            random_state=self.random_state,
        )
        self.model_.fit(X, y)
        return self

    def __sklearn_is_fitted__(self) -> bool:
        # model_/actual_n_components_ are initialised to None in __init__, so the
        # default trailing-underscore heuristic would report "fitted" before fit.
        return self.model_ is not None

    def transform(self, X):
        if self.model_ is None:
            raise RuntimeError("SafeTruncatedSVD has not been fitted")
        return self.model_.transform(X)

    def get_feature_names_out(self, input_features=None):
        count = self.actual_n_components_ or self.n_components
        return np.asarray([f"svd_{i}" for i in range(count)], dtype=object)


class ToDense(BaseEstimator, TransformerMixin):
    def fit(self, X, y=None):
        # See FrameCleaner.fit: needed for scikit-learn >= 1.6 check_is_fitted().
        self.is_fitted_ = True
        return self

    def __sklearn_is_fitted__(self) -> bool:
        return bool(getattr(self, "is_fitted_", False))

    def transform(self, X):
        return X.toarray() if sparse.issparse(X) else np.asarray(X)

    def get_feature_names_out(self, input_features=None):
        if input_features is None:
            return np.asarray([], dtype=object)
        return np.asarray(input_features, dtype=object)


def _one_hot(*, dense: bool):
    kwargs = {
        "handle_unknown": "ignore",
        "min_frequency": int(os.environ.get("UHL_ONEHOT_MIN_FREQUENCY", "1")),
    }
    try:
        return OneHotEncoder(sparse_output=not dense, **kwargs)
    except TypeError:
        return OneHotEncoder(sparse=not dense, **kwargs)


def make_structured_preprocessor(*, dense: bool = False) -> Pipeline:
    categorical = Pipeline([
        ("imputer", SimpleImputer(strategy="constant", fill_value="missing")),
        ("onehot", _one_hot(dense=dense)),
    ])
    numeric = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("time", FunctionTransformer(
            _add_cyclical_time,
            validate=False,
            feature_names_out=_cyclical_time_feature_names,
        )),
        ("scale", StandardScaler()),
    ])
    transformer = ColumnTransformer(
        transformers=[
            ("numeric", numeric, NUMERIC_COLUMNS),
            ("categorical", categorical, CATEGORICAL_COLUMNS),
        ],
        remainder="drop",
        sparse_threshold=0.0 if dense else 0.3,
    )
    steps = [("clean", FrameCleaner()), ("features", transformer)]
    if dense:
        steps.append(("dense", ToDense()))
    return Pipeline(steps)


def make_text_preprocessor() -> Pipeline:
    categorical = Pipeline([
        ("imputer", SimpleImputer(strategy="constant", fill_value="missing")),
        ("onehot", _one_hot(dense=False)),
    ])
    numeric = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("time", FunctionTransformer(
            _add_cyclical_time,
            validate=False,
            feature_names_out=_cyclical_time_feature_names,
        )),
        ("scale", StandardScaler(with_mean=False)),
    ])
    transformer = ColumnTransformer(
        transformers=[
            ("numeric", numeric, NUMERIC_COLUMNS),
            ("categorical", categorical, ["time_bin", "season"]),
            ("complaint_word", TfidfVectorizer(
                lowercase=True,
                ngram_range=(1, 3),
                min_df=int(os.environ.get("UHL_TFIDF_MIN_DF", "3")),
                max_features=int(os.environ.get("UHL_TFIDF_WORD_MAX_FEATURES", "50000")),
                strip_accents="unicode",
                sublinear_tf=True,
            ), TEXT_COLUMN),
            ("complaint_char", TfidfVectorizer(
                analyzer="char_wb",
                lowercase=True,
                ngram_range=(3, 5),
                min_df=int(os.environ.get("UHL_TFIDF_MIN_DF", "3")),
                max_features=int(os.environ.get("UHL_TFIDF_CHAR_MAX_FEATURES", "50000")),
                strip_accents="unicode",
                sublinear_tf=True,
            ), TEXT_COLUMN),
        ],
        remainder="drop",
        sparse_threshold=0.3,
    )
    return Pipeline([("clean", FrameCleaner()), ("features", transformer)])


def safe_feature_names(pipeline, fallback: Iterable[str] | None = None) -> list[str]:
    get_names = getattr(pipeline, "get_feature_names_out", None)
    if get_names is None:
        raise ValueError("Fitted UHL feature pipeline does not expose feature names.")
    try:
        names = [str(v) for v in get_names()]
    except Exception as exc:
        fallback_note = ""
        if fallback is not None:
            fallback_note = f" Fallback names were not used: {list(fallback)}"
        raise ValueError(
            "Fitted UHL feature pipeline could not produce feature names; "
            "feature leakage checks and importance reports cannot be trusted."
            + fallback_note
        ) from exc
    bad = [
        name for name in names
        if any(term in name.lower() for term in FORBIDDEN_FEATURE_TERMS)
    ]
    if bad:
        raise ValueError(f"UHL feature leakage guard blocked feature names: {bad[:20]}")
    return names
