"""Tests for KTAS ML feature engineering and leakage blocking."""
from ml_training.feature_engineering import (
    FEATURE_NAMES,
    extract_features_from_row,
    build_feature_matrix,
    build_training_dataframe,
)


class TestFeatureEngineering:
    def test_feature_names_are_current_ktas_set(self):
        assert len(FEATURE_NAMES) == 34
        assert "temperature_c" in FEATURE_NAMES
        assert "cc_chest" in FEATURE_NAMES
        assert "label_ktas_expert" not in FEATURE_NAMES
        assert "label_ktas_rn" not in FEATURE_NAMES
        assert "Disposition" not in FEATURE_NAMES

    def test_no_retrospective_features(self):
        forbidden = {
            "KTAS_RN", "KTAS_expert", "mistriage", "Error_group", "Diagnosis in ED",
            "Disposition", "Length of stay_min", "KTAS duration_min", "diagnosis_in_ed",
        }
        assert not forbidden.intersection(set(FEATURE_NAMES))

    def test_present_ktas_features_used(self):
        row = {
            "age": 71,
            "patients_per_hour": 3,
            "temperature": 36.6,
            "temperature_unit": "C",
            "heartrate": 84,
            "resprate": 18,
            "o2sat": 100,
            "sbp": 160,
            "dbp": 100,
            "nrs_pain": 2,
            "pain_present": 1,
            "gender": "M",
            "arrival_mode_code": 3,
            "arrival_transport": "Private Vehicle",
            "injury_code": 2,
            "mental_code": 1,
            "group_code": 2,
            "chiefcomplaint": "chest pain",
            "KTAS_expert": 4,
            "Disposition": 1,
        }
        features = extract_features_from_row(row)
        assert features["age"] == 71.0
        assert features["temperature_c"] == 36.6
        assert features["sex_male"] == 1.0
        assert features["arrival_private_vehicle"] == 1.0
        assert features["injury_yes"] == 1.0
        assert features["mental_alert"] == 1.0
        assert features["group_regional_ed"] == 1.0
        assert features["cc_chest"] == 1.0
        assert "KTAS_expert" not in features

    def test_fahrenheit_converts_to_celsius(self):
        features = extract_features_from_row({"temperature": 98.6, "temperature_unit": "F"})
        assert round(features["temperature_c"], 1) == 37.0

    def test_missing_values_use_sentinel(self):
        features = extract_features_from_row({})
        assert features["age"] == -1.0
        assert features["nrs_pain"] == -1.0
        assert features["vital_missing_count"] == 6.0

    def test_build_feature_matrix_shape(self):
        X, names = build_feature_matrix([{"age": 20}, {"age": 30}])
        assert X.shape == (2, len(FEATURE_NAMES))
        assert names == FEATURE_NAMES

    def test_training_dataframe_uses_ktas_labels_not_features(self):
        df = build_training_dataframe([
            {
                "age": 71,
                "heartrate": 84,
                "label_ktas_expert": 4,
                "label_ktas_emergency": 0,
                "label_ktas_high_acuity": 0,
                "label_mistriage": 1,
                "KTAS_RN": 2,
            }
        ])
        assert "label_ktas_expert" in df.columns
        assert "label_ktas_emergency" in df.columns
        assert "KTAS_RN" not in df.columns
        assert "age" in df.columns

    def test_leakage_tripwire_actually_fires(self):
        """
        Proves the leakage guard in extract_features_from_row is a live check,
        not dead code. Temporarily corrupts the module's FEATURE_NAMES list to
        include a blocklisted field and confirms a ValueError is raised, then
        restores the original list so other tests are unaffected.
        """
        import ml_training.feature_engineering as fe
        original_feature_names = fe.FEATURE_NAMES
        try:
            fe.FEATURE_NAMES = original_feature_names + ["KTAS_expert"]
            try:
                fe.extract_features_from_row({"age": 50, "KTAS_expert": 4})
                assert False, "Expected ValueError for leaked feature, but none was raised"
            except ValueError as exc:
                assert "LEAKAGE DETECTED" in str(exc)
                assert "KTAS_expert" in str(exc)
        finally:
            fe.FEATURE_NAMES = original_feature_names
