"""
Priority-6 hardening proofs:
  - model artefact: fail closed on compatibility-check ERROR (not just incompatible)
  - feature-name/order parity verified at predict time
  - model hash/provenance check (when MIMIC_FULL_MODEL_SHA256 set)
  - follow-up updated vitals: strict allow-list + numeric ranges
  - review fields: status enum + length limits + comment redaction
"""
import hashlib
import tempfile
from pathlib import Path

import joblib
import numpy as np
import pytest
import sklearn
from sklearn.ensemble import RandomForestClassifier

from ml_training.feature_engineering import FEATURE_NAMES
from app.agents.ml_prediction_agent import _run_full_mimic_prediction
from app.schemas.workflow import TriageTimeInput


def _ti():
    return TriageTimeInput(subject_id=1, stay_id=1,
                           source_dataset="MIMIC-IV-ED-Full-v2.2",
                           chiefcomplaint="chest pain", heartrate=88, o2sat=97, sbp=120)


def _model():
    X = np.random.RandomState(0).rand(40, len(FEATURE_NAMES))
    y = np.random.RandomState(0).randint(1, 6, 40)
    return RandomForestClassifier(n_estimators=8, random_state=0).fit(X, y)


def _bundle(tmp, feature_names=None, sklearn_version=None):
    art = Path(tmp) / "m.joblib"
    joblib.dump({"model": _model(),
                 "feature_names": feature_names if feature_names is not None else list(FEATURE_NAMES),
                 "sklearn_version": sklearn_version or sklearn.__version__}, art)
    return art


class TestModelLoadHardening:
    def test_good_model_predicts(self, tmp_path, monkeypatch):
        art = _bundle(tmp_path)
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        monkeypatch.delenv("MIMIC_FULL_MODEL_SHA256", raising=False)
        r = _run_full_mimic_prediction(_ti())
        assert r.prediction_available is True
        assert r.predicted_mimic_acuity in (1, 2, 3, 4, 5)

    def test_feature_mismatch_fails_closed(self, tmp_path, monkeypatch):
        art = _bundle(tmp_path, feature_names=["wrong", "features"])
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        monkeypatch.delenv("MIMIC_FULL_MODEL_SHA256", raising=False)
        r = _run_full_mimic_prediction(_ti())
        assert r.prediction_available is False
        assert "feature" in r.model_name

    def test_missing_feature_names_fails_closed(self, tmp_path, monkeypatch):
        art = Path(tmp_path) / "nf.joblib"
        joblib.dump({"model": _model(), "sklearn_version": sklearn.__version__}, art)
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        monkeypatch.delenv("MIMIC_FULL_MODEL_SHA256", raising=False)
        r = _run_full_mimic_prediction(_ti())
        assert r.prediction_available is False
        assert "no_feature_names" in r.model_name

    def test_compatibility_check_error_fails_closed(self, tmp_path, monkeypatch):
        # corrupt file -> check_artifact raises -> must fail closed, not fall through
        art = Path(tmp_path) / "corrupt.joblib"
        art.write_bytes(b"not a joblib file")
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        monkeypatch.delenv("MIMIC_FULL_MODEL_SHA256", raising=False)
        r = _run_full_mimic_prediction(_ti())
        assert r.prediction_available is False
        assert "check_failed" in r.model_name or "error" in r.model_name

    def test_hash_mismatch_fails_closed(self, tmp_path, monkeypatch):
        art = _bundle(tmp_path)
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        monkeypatch.setenv("MIMIC_FULL_MODEL_SHA256", "deadbeef")
        r = _run_full_mimic_prediction(_ti())
        assert r.prediction_available is False
        assert "hash_mismatch" in r.model_name

    def test_hash_match_predicts(self, tmp_path, monkeypatch):
        art = _bundle(tmp_path)
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        monkeypatch.setenv("MIMIC_FULL_MODEL_SHA256",
                           hashlib.sha256(art.read_bytes()).hexdigest())
        r = _run_full_mimic_prediction(_ti())
        assert r.prediction_available is True


class TestFollowupVitalsValidation:
    def test_valid_vitals_accepted(self):
        from app.api.case_routes import FollowupBody
        b = FollowupBody(updated_vitals={"heartrate": 110, "o2sat": 94})
        assert b.updated_vitals == {"heartrate": 110.0, "o2sat": 94.0}

    def test_unknown_key_rejected(self):
        from app.api.case_routes import FollowupBody
        with pytest.raises(Exception):
            FollowupBody(updated_vitals={"injected_key": 1})

    def test_out_of_range_rejected(self):
        from app.api.case_routes import FollowupBody
        with pytest.raises(Exception):
            FollowupBody(updated_vitals={"o2sat": 250})

    def test_non_numeric_rejected(self):
        from app.api.case_routes import FollowupBody
        with pytest.raises(Exception):
            FollowupBody(updated_vitals={"heartrate": "DROP TABLE"})


class TestReviewFieldValidation:
    def test_valid_review_accepted(self):
        from app.schemas.review import HumanReviewRequest
        r = HumanReviewRequest(stay_id=1, reviewer_role="ed-doctor",
                               review_status="REVIEWED", review_comment="looks fine")
        assert r.review_status == "REVIEWED"

    def test_invalid_status_rejected(self):
        from app.schemas.review import HumanReviewRequest
        with pytest.raises(Exception):
            HumanReviewRequest(stay_id=1, reviewer_role="x",
                               review_status="HACKED", review_comment="x")

    def test_overlong_comment_rejected(self):
        from app.schemas.review import HumanReviewRequest
        with pytest.raises(Exception):
            HumanReviewRequest(stay_id=1, reviewer_role="x",
                               review_status="REVIEWED", review_comment="a" * 5000)

    def test_comment_is_redacted(self):
        from app.schemas.review import HumanReviewRequest
        r = HumanReviewRequest(stay_id=1, reviewer_role="x", review_status="REVIEWED",
                               review_comment="patient John Smith called 555-123-4567")
        assert "John Smith" not in r.review_comment
        assert "555-123-4567" not in r.review_comment
