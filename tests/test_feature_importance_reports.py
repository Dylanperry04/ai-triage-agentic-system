import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer

from ml_training.full_mimic.feature_importance import extract_feature_importance_report


def test_linear_model_feature_importance_available():
    x = np.array([[0.0, 1.0], [1.0, 0.0], [0.2, 1.0], [1.0, 0.2]])
    y = np.array([1, 2, 1, 2])
    model = Pipeline([
        ("identity", FunctionTransformer(validate=False)),
        ("estimator", LogisticRegression().fit(x, y)),
    ])
    report = extract_feature_importance_report(
        model_name="logistic_regression",
        model=model,
        base_feature_names=["temperature_c", "heartrate"],
        labels=[1, 2],
        top_n=2,
    )
    assert report["status"] == "available"
    assert len(report["top_features"]) == 2
    assert {row["feature"] for row in report["top_features"]} == {"temperature_c", "heartrate"}


def test_svd_model_does_not_fake_token_importance():
    report = extract_feature_importance_report(
        model_name="raw_tfidf_svd_xgboost",
        model=object(),
        base_feature_names=["temperature_c"],
        labels=[1, 2],
    )
    assert report["status"] == "not_directly_interpretable"
    assert report["top_features"] == []


def test_feature_importance_rejects_leakage_names():
    x = np.array([[0.0], [1.0], [0.2], [1.0]])
    y = np.array([1, 2, 1, 2])
    model = Pipeline([
        ("identity", FunctionTransformer(validate=False)),
        ("estimator", LogisticRegression().fit(x, y)),
    ])
    with pytest.raises(ValueError):
        extract_feature_importance_report(
            model_name="logistic_regression",
            model=model,
            base_feature_names=["disposition"],
            labels=[1, 2],
        )
