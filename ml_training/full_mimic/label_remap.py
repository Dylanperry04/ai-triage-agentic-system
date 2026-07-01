"""Importable estimator wrapper for classifiers that require 0-indexed labels.

Some libraries, notably XGBoost, prefer contiguous class labels starting at 0.
The clinical acuity contract here is 1-5, so the wrapper maps labels internally
for fitting but exposes real acuity labels from predict() and classes_.
"""
from __future__ import annotations

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, clone


class LabelRemapClassifier(BaseEstimator, ClassifierMixin):
    """Fit an estimator on encoded labels while exposing original labels."""

    def __init__(self, estimator):
        self.estimator = estimator

    def fit(self, X, y):
        self.classes_ = np.array(sorted(set(np.asarray(y).tolist())))
        self._to_encoded_ = {label: i for i, label in enumerate(self.classes_)}
        y_encoded = np.array([self._to_encoded_[label] for label in y])
        self.estimator_ = clone(self.estimator)
        self.estimator_.fit(X, y_encoded)
        return self

    def predict(self, X):
        encoded = np.asarray(self.estimator_.predict(X)).ravel().astype(int)
        return self.classes_[encoded]

    def predict_proba(self, X):
        return self.estimator_.predict_proba(X)
