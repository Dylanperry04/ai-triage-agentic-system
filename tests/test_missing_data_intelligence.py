"""Missing-data intelligence now depends on the full-MIMIC model
(MIMIC_FULL_MODEL_PATH), which is credentialed and not present in this repo. With
no model available it must fail SOFT (no crash, empty importances). With a
synthetic model bundle it computes aggregated importances. No credentialed data."""
import joblib
import numpy as np
import pytest

from app.ml.missing_data_intelligence import analyse_missing_data, _model_and_features


@pytest.fixture(autouse=True)
def _no_model_by_default(monkeypatch):
    monkeypatch.delenv("MIMIC_FULL_MODEL_PATH", raising=False)
    yield


class TestFailsSoftWithoutModel:
    def test_model_and_features_empty_without_model(self):
        feats, _, importance = _model_and_features()
        assert feats == []
        assert importance == {}

    def test_analyse_does_not_crash_without_model(self):
        out = analyse_missing_data(["heartrate", "o2sat"], applicable=True)
        assert isinstance(out, dict)
        # research-framed, never a crash
        assert "missing_fields" in out or "note" in out or out is not None


class TestWithSyntheticModel:
    def _make_model(self, tmp_path):
        from sklearn.ensemble import RandomForestClassifier
        X = np.random.RandomState(0).rand(40, 4)
        y = np.random.RandomState(0).randint(1, 6, size=40)
        m = RandomForestClassifier(n_estimators=10, random_state=0).fit(X, y)
        art = tmp_path / "full.joblib"
        joblib.dump({"model": m,
                     "feature_names": ["heartrate", "o2sat", "sbp", "resprate"]}, art)
        return art

    def test_importances_computed_from_synthetic_model(self, tmp_path, monkeypatch):
        art = self._make_model(tmp_path)
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(art))
        feats, _, importance = _model_and_features()
        assert set(feats) == {"heartrate", "o2sat", "sbp", "resprate"}
        assert abs(sum(importance.values()) - 1.0) < 1e-6  # normalised
        assert all(v >= 0 for v in importance.values())
