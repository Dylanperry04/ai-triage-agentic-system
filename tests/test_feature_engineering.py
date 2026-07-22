"""
Tests for full MIMIC-IV-ED feature engineering.

Covers (per external review):
  - KTAS-derived features are GONE (availability against the real MIMIC schema)
  - feature availability: every feature is produced from MIMIC triage/edstays fields
  - variance: features are not all-constant across realistic MIMIC rows
  - missingness: missing vitals are handled with the sentinel + counted
  - exact train/serve parity: the serving path and training path use the identical
    extractor and feature order
  - leakage: outcome/label/identifier columns never appear in the output
"""
import numpy as np

from ml_training.feature_engineering import (
    FEATURE_NAMES, extract_features_from_row, build_feature_matrix,
    build_feature_frame, LEAKAGE_FEATURE_EXACT_BLOCKLIST,
    validate_feature_schema,
)


def _mimic_row(**over):
    base = {
        "temperature": 98.6, "temperature_unit": "F", "heartrate": 88.0,
        "resprate": 18.0, "o2sat": 97.0, "sbp": 120.0, "dbp": 75.0,
        "pain": "5", "chiefcomplaint": "CHEST PAIN", "gender": "F",
        "arrival_transport": "AMBULANCE", "acuity": 2,
        "subject_id": 10000001, "stay_id": 30000001,
    }
    base.update(over)
    return base


class TestKtasFeaturesRemoved:
    def test_no_ktas_derived_features_remain(self):
        forbidden = {
            "age", "patients_per_hour", "injury_yes", "mental_alert",
            "mental_verbal_response", "mental_pain_response", "mental_unresponsive",
            "group_regional_ed", "arrival_walking", "arrival_public_ambulance",
            "arrival_private_vehicle", "arrival_private_ambulance",
        }
        assert not forbidden.intersection(set(FEATURE_NAMES)), (
            "KTAS-derived features must be removed from the MIMIC feature set")

    def test_feature_set_is_mimic_real(self):
        # core MIMIC triage vitals + chiefcomplaint keyword features
        for f in ("temperature_c", "heartrate", "resprate", "o2sat", "sbp", "dbp",
                  "nrs_pain", "vital_missing_count", "sex_male", "sex_female",
                  "arrival_ambulance", "arrival_walk_in", "shock_index",
                  "pulse_pressure", "hypoxia_tachypnea_interaction",
                  "respiratory_distress_flag", "mean_arterial_pressure",
                  "three_or_more_vitals_missing", "pain_range_provided",
                  "cc_chest"):
            assert f in FEATURE_NAMES


class TestFeatureAvailability:
    def test_every_feature_produced_from_mimic_row(self):
        feats = extract_features_from_row(_mimic_row())
        assert list(feats.keys()) == FEATURE_NAMES
        assert all(isinstance(v, float) for v in feats.values())

    def test_arrival_transport_mapping_matches_mimic_values(self):
        assert extract_features_from_row(_mimic_row(arrival_transport="AMBULANCE"))["arrival_ambulance"] == 1.0
        assert extract_features_from_row(_mimic_row(arrival_transport="WALK IN"))["arrival_walk_in"] == 1.0
        assert extract_features_from_row(_mimic_row(arrival_transport="HELICOPTER"))["arrival_helicopter"] == 1.0
        assert extract_features_from_row(_mimic_row(arrival_transport="UNKNOWN"))["arrival_other_unknown"] == 1.0


class TestVariance:
    def test_features_vary_across_realistic_rows(self):
        rows = [
            _mimic_row(heartrate=60, o2sat=99, sbp=130, arrival_transport="WALK IN",
                       chiefcomplaint="ankle pain"),
            _mimic_row(heartrate=140, o2sat=85, sbp=80, arrival_transport="AMBULANCE",
                       chiefcomplaint="chest pain, dyspnea"),
            _mimic_row(heartrate=95, o2sat=94, sbp=110, arrival_transport="HELICOPTER",
                       chiefcomplaint="trauma", gender="M"),
        ]
        X, names = build_feature_matrix(rows)
        # No column should be constant across these deliberately varied rows for
        # the active vitals/arrival/complaint signals.
        variances = X.var(axis=0)
        # the engineered set must have at least several varying columns
        assert int((variances > 0).sum()) >= 8


class TestMissingness:
    def test_missing_vitals_use_sentinel_and_are_counted(self):
        feats = extract_features_from_row(_mimic_row(
            heartrate=None, o2sat=None, sbp=None, dbp=None,
            temperature=None, resprate=None))
        assert feats["heartrate"] == -1.0
        assert feats["o2sat"] == -1.0
        assert feats["vital_missing_count"] == 6.0
        assert feats["all_vitals_missing"] == 1.0
        assert feats["three_or_more_vitals_missing"] == 1.0

    def test_partial_missingness_counts_correctly(self):
        feats = extract_features_from_row(_mimic_row(o2sat=None, sbp=None))
        assert feats["vital_missing_count"] == 2.0
        assert feats["all_vitals_missing"] == 0.0
        assert feats["three_or_more_vitals_missing"] == 0.0

    def test_safe_triage_time_derived_features(self):
        feats = extract_features_from_row(_mimic_row(
            heartrate=140, sbp=70, dbp=60, o2sat=88, resprate=32, temperature=40,
            temperature_unit="C",
        ))
        assert feats["shock_index"] == 2.0
        assert feats["pulse_pressure"] == 10.0
        assert feats["mean_arterial_pressure"] == (70 + 2 * 60) / 3
        assert feats["shock_index_high_flag"] == 1.0
        assert feats["narrow_pulse_pressure_flag"] == 1.0
        assert feats["map_low_flag"] == 1.0
        assert feats["respiratory_distress_flag"] == 1.0
        assert feats["fever_flag"] == 1.0
        assert feats["temperature_extreme_flag"] == 1.0
        assert feats["hypotension_flag"] == 1.0
        assert feats["tachycardia_flag"] == 1.0
        assert feats["tachypnea_flag"] == 1.0
        assert feats["severe_tachypnea_flag"] == 1.0
        assert feats["severe_hypoxia_flag"] == 1.0
        assert feats["fever_tachycardia_interaction"] == 1.0
        assert feats["hypoxia_tachypnea_interaction"] == 1.0
        assert feats["hypotension_tachycardia_interaction"] == 1.0

    def test_implausible_vitals_are_missing_with_outlier_flags(self):
        feats = extract_features_from_row(_mimic_row(
            temperature=36.5, temperature_unit="F",
            heartrate=1228, resprate=1820, o2sat=9322, sbp=151103, dbp=879,
        ))
        assert feats["temperature_c"] == -1.0
        assert feats["dbp"] == -1.0
        assert feats["vital_missing_count"] == 6.0
        assert feats["temperature_outlier"] == 1.0
        assert feats["heartrate_outlier"] == 1.0
        assert feats["resprate_outlier"] == 1.0
        assert feats["o2sat_outlier"] == 1.0
        assert feats["sbp_outlier"] == 1.0
        assert feats["dbp_outlier"] == 1.0

    def test_dirty_pain_text_preserves_signal_without_numeric_guessing(self):
        feats = extract_features_from_row(_mimic_row(pain=None, pain_raw="Critical"))
        assert feats["nrs_pain"] == -1.0
        assert feats["pain_missing"] == 1.0
        assert feats["pain_unable_to_assess"] == 1.0
        assert feats["pain_critical_text"] == 1.0
        assert feats["pain_raw_text_flag"] == 1.0

    def test_out_of_range_pain_is_flagged_not_used(self):
        feats = extract_features_from_row(_mimic_row(pain="13"))
        assert feats["nrs_pain"] == -1.0
        assert feats["pain_outlier"] == 1.0

    def test_pain_text_denies_and_ranges_preserve_signal(self):
        denied = extract_features_from_row(_mimic_row(pain="denies"))
        ranged = extract_features_from_row(_mimic_row(pain="6-7"))
        assert denied["nrs_pain"] == 0.0
        assert denied["pain_denies"] == 1.0
        assert denied["pain_missing"] == 0.0
        assert ranged["nrs_pain"] == 6.5
        assert ranged["pain_range_provided"] == 1.0

    def test_complaint_keywords_are_less_broad_and_more_specific(self):
        low_specificity = extract_features_from_row(_mimic_row(chiefcomplaint="chest rash"))
        high_specificity = extract_features_from_row(_mimic_row(chiefcomplaint="chest pain, transfer"))
        assert low_specificity["cc_chest"] == 0.0
        assert high_specificity["cc_chest"] == 1.0
        assert high_specificity["cc_chest_pain"] == 1.0
        assert high_specificity["cc_transfer"] == 1.0

    def test_drug_allergy_does_not_trigger_self_harm_keyword(self):
        feats = extract_features_from_row(_mimic_row(chiefcomplaint="drug allergy"))
        assert feats["cc_allergy"] == 1.0
        assert feats["cc_self_harm"] == 0.0


class TestTrainServeParity:
    def test_serving_and_training_use_identical_extractor(self):
        """The serving path (ml_prediction_agent) imports the SAME FEATURE_NAMES
        and extract_features_from_row used by build_feature_frame for training, so
        the produced vector and order are identical."""
        from app.agents import ml_prediction_agent as serving
        from ml_training import feature_engineering as training
        assert serving.FEATURE_NAMES is training.FEATURE_NAMES
        assert serving.extract_features_from_row is training.extract_features_from_row

    def test_train_and_serve_vectors_match_for_same_row(self):
        from app.agents.ml_prediction_agent import extract_features_from_row as serve_extract
        row = _mimic_row()
        serve_feats = serve_extract(row)
        # training vector via build_feature_frame
        case = {"source_dataset": "MIMIC-IV-ED-Full-v2.2",
                "triage": row, "edstay": {"gender": row["gender"],
                "arrival_transport": row["arrival_transport"]}}
        X, _, names = build_feature_frame([case])
        serve_vec = np.array([[serve_feats[f] for f in names]], dtype=float)
        assert np.array_equal(X, serve_vec)


class TestLeakage:
    def test_no_leakage_columns_in_output(self):
        feats = extract_features_from_row(_mimic_row())
        for col in ("acuity", "disposition", "outtime", "hadm_id", "subject_id", "stay_id"):
            assert col not in feats

    def test_leakage_blocklist_disjoint_from_feature_names(self):
        validate_feature_schema(FEATURE_NAMES)
        assert not LEAKAGE_FEATURE_EXACT_BLOCKLIST.intersection(set(FEATURE_NAMES))

    def test_acuity_present_in_row_never_becomes_a_feature(self):
        feats = extract_features_from_row(_mimic_row(acuity=1))
        assert "acuity" not in feats

    def test_target_future_identifier_feature_names_are_rejected(self):
        for bad in (
            "subject_id",
            "stay_id",
            "target_acuity",
            "future_disposition",
            "diagnosis_count",
            "outtime_hour",
            "charttime",
            "future_vitals",
            "length_of_stay",
            "admission_status",
        ):
            try:
                validate_feature_schema(FEATURE_NAMES + [bad])
            except ValueError as exc:
                assert "LEAKAGE DETECTED" in str(exc)
            else:
                raise AssertionError(f"{bad} should have been rejected")
