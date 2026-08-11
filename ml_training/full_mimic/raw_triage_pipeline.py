"""Serving-eligible raw triage dataframe pipelines for full MIMIC-IV-ED.

This module defines the v2 model artefact input contract:
``artifact_contract_version=2`` and ``input_type="raw_triage_dataframe"``.
The dataframe contains only triage/registration-time columns. It deliberately
excludes identifiers, labels, outcomes, diagnoses, medication tables, and
post-triage vital-sign rows.
"""
from __future__ import annotations

import re
from typing import Iterable

import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.calibration import CalibratedClassifierCV
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, SGDClassifier
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC

from ml_training.feature_engineering import (
    FEATURE_NAMES,
    extract_features_from_row,
    validate_feature_schema,
)


RAW_TRIAGE_INPUT_COLUMNS = [
    "temperature",
    "temperature_unit",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "pain",
    "pain_raw",
    "pain_present",
    "nrs_pain",
    "gender",
    "arrival_transport",
    "chiefcomplaint",
]

RAW_TRIAGE_INPUT_TYPE = "raw_triage_dataframe"
RAW_TRIAGE_ARTIFACT_CONTRACT_VERSION = 2

_FORBIDDEN_RAW_PATTERNS = (
    "subject_id", "stay_id", "hadm_id", "acuity", "label", "target",
    "disposition", "outtime", "diagnos", "medrecon", "pyxis",
    "charttime", "vitalsign", "future", "admission", "mortality",
    "death", "expire", "length_of_stay",
)

_ABBREVIATION_PATTERNS = (
    (r"(?<![a-z0-9])n\s*/\s*v\s*/\s*d(?![a-z0-9])", "nausea vomiting diarrhea"),
    (r"(?<![a-z0-9])n\s*/\s*v(?![a-z0-9])", "nausea vomiting"),
    (r"(?<![a-z0-9])s\s*/\s*p(?![a-z0-9])", "status post"),
    (r"(?<![a-z0-9])brbpr(?![a-z0-9])", "bright red blood per rectum"),
    (r"(?<![a-z0-9])ams(?![a-z0-9])", "altered mental status"),
    (r"(?<![a-z0-9])sob(?![a-z0-9])", "shortness of breath"),
    (r"(?<![a-z0-9])cp(?![a-z0-9])", "chest pain"),
    (r"(?<![a-z0-9])si(?![a-z0-9])", "suicidal ideation"),
    (r"(?<![a-z0-9])etoh(?![a-z0-9])", "alcohol intoxication"),
    (r"(?<![a-z0-9])mvc(?![a-z0-9])", "motor vehicle collision"),
    (r"(?<![a-z0-9])mva(?![a-z0-9])", "motor vehicle accident"),
    (r"(?<![a-z0-9])loc(?![a-z0-9])", "loss of consciousness"),
)


def validate_raw_triage_columns(columns: Iterable[str] | None = None) -> None:
    """Fail closed if a v2 raw dataframe schema contains leakage-like columns."""
    cols = list(columns or RAW_TRIAGE_INPUT_COLUMNS)
    if cols != RAW_TRIAGE_INPUT_COLUMNS:
        raise ValueError(
            "raw triage dataframe schema mismatch; expected exactly "
            f"{RAW_TRIAGE_INPUT_COLUMNS}, got {cols}"
        )
    lowered = [str(c).strip().lower() for c in cols]
    blocked = sorted(
        c for c in lowered
        if any(pattern in c for pattern in _FORBIDDEN_RAW_PATTERNS)
    )
    if blocked:
        raise ValueError(
            "LEAKAGE DETECTED: raw triage dataframe contains blocked column(s): "
            f"{blocked}"
        )
    validate_feature_schema(FEATURE_NAMES)


def normalise_chiefcomplaint_text(text: object) -> str:
    """Expand common ED abbreviations while preserving the raw text elsewhere."""
    value = str(text or "").lower()
    value = re.sub(r"[_]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    for pattern, replacement in _ABBREVIATION_PATTERNS:
        value = re.sub(pattern, replacement, value)
    return re.sub(r"\s+", " ", value).strip()


def raw_triage_dataframe_from_cases(cases: list[dict]) -> pd.DataFrame:
    """Build a safe raw dataframe from nested EDTriageCase-shaped records."""
    rows = []
    for case in cases:
        if hasattr(case, "model_dump"):
            case = case.model_dump(mode="json")
        triage = dict(case.get("triage") or {})
        edstay = case.get("edstay") or {}
        row = {
            "temperature": triage.get("temperature"),
            "temperature_unit": triage.get("temperature_unit", "F"),
            "heartrate": triage.get("heartrate"),
            "resprate": triage.get("resprate"),
            "o2sat": triage.get("o2sat"),
            "sbp": triage.get("sbp"),
            "dbp": triage.get("dbp"),
            "pain": triage.get("pain"),
            "pain_raw": triage.get("pain_raw"),
            "pain_present": triage.get("pain_present"),
            "nrs_pain": triage.get("nrs_pain"),
            "gender": edstay.get("gender", triage.get("gender")),
            "arrival_transport": edstay.get(
                "arrival_transport", triage.get("arrival_transport")
            ),
            "chiefcomplaint": triage.get("chiefcomplaint"),
        }
        rows.append(row)
    return raw_triage_dataframe_from_rows(rows)


def raw_triage_dataframe_from_rows(rows: list[dict]) -> pd.DataFrame:
    validate_raw_triage_columns(RAW_TRIAGE_INPUT_COLUMNS)
    safe_rows = [
        {col: row.get(col) for col in RAW_TRIAGE_INPUT_COLUMNS}
        for row in rows
    ]
    return pd.DataFrame(safe_rows, columns=RAW_TRIAGE_INPUT_COLUMNS)


def raw_triage_dataframe_from_triage_inputs(triage_inputs: list[object]) -> pd.DataFrame:
    rows = []
    for item in triage_inputs:
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        rows.append({col: item.get(col) for col in RAW_TRIAGE_INPUT_COLUMNS})
    return raw_triage_dataframe_from_rows(rows)


class StructuredFeatureTransformer(BaseEstimator, TransformerMixin):
    """Convert raw triage rows to the existing leakage-audited FEATURE_NAMES."""

    def fit(self, X, y=None):
        validate_raw_triage_columns(RAW_TRIAGE_INPUT_COLUMNS)
        validate_feature_schema(FEATURE_NAMES)
        return self

    def transform(self, X):
        frame = _as_frame(X)
        rows = frame.to_dict(orient="records")
        features = [extract_features_from_row(row) for row in rows]
        return [[fd[name] for name in FEATURE_NAMES] for fd in features]

    def get_feature_names_out(self, input_features=None):
        return list(FEATURE_NAMES)


class ChiefComplaintTextExtractor(BaseEstimator, TransformerMixin):
    """Extract raw or normalised chief-complaint text for vectorisers."""

    def __init__(self, *, normalised: bool = False):
        self.normalised = normalised

    def fit(self, X, y=None):
        return self

    def transform(self, X):
        frame = _as_frame(X)
        series = frame["chiefcomplaint"].fillna("").astype(str)
        if self.normalised:
            return series.map(normalise_chiefcomplaint_text).to_numpy()
        return series.to_numpy()

    def get_feature_names_out(self, input_features=None):
        suffix = "normalised" if self.normalised else "raw"
        return [f"chiefcomplaint_{suffix}"]


def _as_frame(X) -> pd.DataFrame:
    if isinstance(X, pd.DataFrame):
        return X.reindex(columns=RAW_TRIAGE_INPUT_COLUMNS)
    return pd.DataFrame(X, columns=RAW_TRIAGE_INPUT_COLUMNS)


def make_raw_tfidf_logistic_pipeline(
    *,
    min_df: int = 3,
    word_max_features: int = 50_000,
    char_max_features: int = 50_000,
    max_iter: int = 1000,
    solver: str = "saga",
    tol: float = 1e-4,
) -> Pipeline:
    """Structured triage features + raw/normalised complaint TF-IDF."""
    validate_raw_triage_columns(RAW_TRIAGE_INPUT_COLUMNS)
    estimator_kwargs = {
        "max_iter": max_iter,
        "class_weight": "balanced",
        "solver": solver,
        "tol": tol,
    }
    if solver != "liblinear":
        estimator_kwargs["n_jobs"] = -1
    return Pipeline([
        ("features", _raw_tfidf_feature_union(
            min_df=min_df,
            word_max_features=word_max_features,
            char_max_features=char_max_features,
        )),
        ("estimator", LogisticRegression(**estimator_kwargs)),
    ])


def _raw_tfidf_feature_union(
    *,
    min_df: int,
    word_max_features: int,
    char_max_features: int,
) -> FeatureUnion:
    """Shared safe feature block: audited structured features plus text TF-IDF."""
    validate_raw_triage_columns(RAW_TRIAGE_INPUT_COLUMNS)
    return FeatureUnion([
        ("structured", Pipeline([
            ("extract", StructuredFeatureTransformer()),
            ("scale", StandardScaler(with_mean=False)),
        ])),
        ("word_raw", Pipeline([
            ("text", ChiefComplaintTextExtractor(normalised=False)),
            ("tfidf", TfidfVectorizer(
                lowercase=True,
                ngram_range=(1, 3),
                min_df=min_df,
                max_features=word_max_features,
                strip_accents="unicode",
                sublinear_tf=True,
            )),
        ])),
        ("word_normalised", Pipeline([
            ("text", ChiefComplaintTextExtractor(normalised=True)),
            ("tfidf", TfidfVectorizer(
                lowercase=True,
                ngram_range=(1, 3),
                min_df=min_df,
                max_features=word_max_features,
                strip_accents="unicode",
                sublinear_tf=True,
            )),
        ])),
        ("char_normalised", Pipeline([
            ("text", ChiefComplaintTextExtractor(normalised=True)),
            ("tfidf", TfidfVectorizer(
                analyzer="char_wb",
                lowercase=True,
                ngram_range=(3, 5),
                min_df=min_df,
                max_features=char_max_features,
                strip_accents="unicode",
                sublinear_tf=True,
            )),
        ])),
    ])


def make_raw_tfidf_linear_svm_pipeline(
    *,
    min_df: int = 3,
    word_max_features: int = 50_000,
    char_max_features: int = 50_000,
    max_iter: int = 5000,
    cv: int = 3,
) -> Pipeline:
    """Calibrated linear SVM over structured + chief-complaint TF-IDF features."""
    validate_raw_triage_columns(RAW_TRIAGE_INPUT_COLUMNS)
    return Pipeline([
        ("features", _raw_tfidf_feature_union(
            min_df=min_df,
            word_max_features=word_max_features,
            char_max_features=char_max_features,
        )),
        ("estimator", CalibratedClassifierCV(
            estimator=LinearSVC(
                class_weight="balanced",
                random_state=42,
                max_iter=max_iter,
            ),
            method="sigmoid",
            cv=cv,
        )),
    ])


def make_raw_tfidf_sgd_logistic_pipeline(
    *,
    min_df: int = 3,
    word_max_features: int = 50_000,
    char_max_features: int = 50_000,
    max_iter: int = 1000,
    tol: float = 1e-4,
) -> Pipeline:
    """Fast log-loss SGD classifier over structured + complaint TF-IDF features."""
    validate_raw_triage_columns(RAW_TRIAGE_INPUT_COLUMNS)
    return Pipeline([
        ("features", _raw_tfidf_feature_union(
            min_df=min_df,
            word_max_features=word_max_features,
            char_max_features=char_max_features,
        )),
        ("estimator", SGDClassifier(
            loss="log_loss",
            class_weight="balanced",
            random_state=42,
            max_iter=max_iter,
            tol=tol,
        )),
    ])


def make_raw_tfidf_svd_pipeline(
    estimator,
    *,
    min_df: int = 3,
    word_max_features: int = 50_000,
    char_max_features: int = 50_000,
    n_components: int = 256,
) -> Pipeline:
    """TF-IDF + structured features compressed for tree/boosting estimators."""
    validate_raw_triage_columns(RAW_TRIAGE_INPUT_COLUMNS)
    return Pipeline([
        ("features", _raw_tfidf_feature_union(
            min_df=min_df,
            word_max_features=word_max_features,
            char_max_features=char_max_features,
        )),
        ("svd", TruncatedSVD(n_components=n_components, random_state=42)),
        ("scale", StandardScaler()),
        ("estimator", estimator),
    ])


def make_raw_tfidf_svd_smote_pipeline(
    estimator,
    sampler,
    *,
    min_df: int = 3,
    word_max_features: int = 50_000,
    char_max_features: int = 50_000,
    n_components: int = 256,
):
    """TF-IDF/SVD + structured features with training-only SMOTE.

    SMOTE is deliberately placed after SVD, so the sampler sees a dense numeric
    feature space rather than the original sparse text vocabulary. The
    imbalanced-learn Pipeline applies the sampler during fit_resample only;
    validation, test, and serving inputs are transformed without resampling.
    """
    from imblearn.pipeline import Pipeline as ImbPipeline

    validate_raw_triage_columns(RAW_TRIAGE_INPUT_COLUMNS)
    return ImbPipeline([
        ("features", _raw_tfidf_feature_union(
            min_df=min_df,
            word_max_features=word_max_features,
            char_max_features=char_max_features,
        )),
        ("svd", TruncatedSVD(n_components=n_components, random_state=42)),
        ("scale", StandardScaler()),
        ("sampler", sampler),
        ("estimator", estimator),
    ])
