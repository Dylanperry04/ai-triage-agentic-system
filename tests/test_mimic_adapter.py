"""
Tests for app/data_pipeline/mimic_adapter.py.

Uses a small, hand-built DataFrame fixture matching the EXACT real column
shapes verified against the actual downloaded mimic-iv-ed-demo-2.2.zip
(not invented column names), rather than depending on the real data files
being present in the test environment.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.data_pipeline.mimic_adapter import (
    SOURCE_DATASET_LABEL,
    _parse_pain,
    _to_float,
    _to_int,
    _to_str,
    dataframe_to_cases,
)


@pytest.fixture
def edstays_df():
    return pd.DataFrame([
        {"subject_id": 1001, "hadm_id": 5001.0, "stay_id": 9001,
         "intime": "2125-01-01 10:00:00", "outtime": "2125-01-01 14:00:00",
         "gender": "F", "race": "WHITE", "arrival_transport": "AMBULANCE",
         "disposition": "ADMITTED"},
        {"subject_id": 1002, "hadm_id": None, "stay_id": 9002,
         "intime": "2125-01-02 09:00:00", "outtime": "2125-01-02 11:00:00",
         "gender": "M", "race": "BLACK/AFRICAN AMERICAN", "arrival_transport": "WALK IN",
         "disposition": "HOME"},
    ])


@pytest.fixture
def triage_df():
    return pd.DataFrame([
        {"subject_id": 1001, "stay_id": 9001, "temperature": 99.1, "heartrate": 110.0,
         "resprate": 22.0, "o2sat": 91.0, "sbp": 88.0, "dbp": 60.0, "pain": "8",
         "acuity": 1.0, "chiefcomplaint": "CHEST PAIN"},
        {"subject_id": 1002, "stay_id": 9002, "temperature": 98.2, "heartrate": 78.0,
         "resprate": 16.0, "o2sat": 99.0, "sbp": 120.0, "dbp": 80.0, "pain": "unable",
         "acuity": 3.0, "chiefcomplaint": "ANKLE PAIN"},
    ])


@pytest.fixture
def vitalsign_df():
    return pd.DataFrame([
        {"subject_id": 1001, "stay_id": 9001, "charttime": "2125-01-01 11:00:00",
         "temperature": 99.5, "heartrate": 112.0, "resprate": 24.0, "o2sat": 90.0,
         "sbp": 85.0, "dbp": 58.0, "rhythm": "Sinus Tachycardia", "pain": "9"},
        {"subject_id": 1001, "stay_id": 9001, "charttime": "2125-01-01 12:30:00",
         "temperature": 99.0, "heartrate": 105.0, "resprate": 20.0, "o2sat": 93.0,
         "sbp": 92.0, "dbp": 62.0, "rhythm": None, "pain": "6"},
    ])


@pytest.fixture
def diagnosis_df():
    return pd.DataFrame([
        {"subject_id": 1001, "stay_id": 9001, "seq_num": 1, "icd_code": "I21.9",
         "icd_version": 10, "icd_title": "ACUTE MYOCARDIAL INFARCTION, UNSPECIFIED"},
    ])


@pytest.fixture
def medrecon_df():
    return pd.DataFrame([
        {"subject_id": 1001, "stay_id": 9001, "charttime": "2125-01-01 10:05:00",
         "name": "ASPIRIN", "gsn": "1234", "ndc": "00000-0000-00",
         "etc_rn": 1, "etccode": None, "etcdescription": None},
    ])


@pytest.fixture
def pyxis_df():
    return pd.DataFrame([
        {"subject_id": 1001, "stay_id": 9001, "charttime": "2125-01-01 10:10:00",
         "med_rn": 1, "name": "Aspirin", "gsn_rn": 1, "gsn": 4380.0},
    ])


class TestScalarParsers:
    def test_to_float_handles_none_and_nan(self):
        assert _to_float(None) is None
        assert _to_float(float("nan")) is None
        assert _to_float("12.5") == 12.5

    def test_to_int_truncates_float_strings(self):
        assert _to_int("9001.0") == 9001
        assert _to_int(None) is None

    def test_to_str_strips_and_handles_nan(self):
        assert _to_str("  HOME  ") == "HOME"
        assert _to_str(None) is None
        assert _to_str(float("nan")) is None


class TestParsePain:
    def test_valid_numeric_pain_passes_through(self):
        assert _parse_pain("8") == "8"
        assert _parse_pain("0") == "0"
        assert _parse_pain("10") == "10"

    def test_out_of_range_numeric_pain_is_none(self):
        """Verified real dirty value: triage.pain contains '13', outside 0-10."""
        assert _parse_pain("13") is None

    def test_non_numeric_junk_pain_is_none(self):
        """Verified real dirty values found in triage.pain."""
        for junk in ["unable", "UA", "Critical", "o", "uta", "ett"]:
            assert _parse_pain(junk) is None

    def test_missing_pain_is_none(self):
        assert _parse_pain(None) is None
        assert _parse_pain(float("nan")) is None


class TestDataframeToCases:
    def test_builds_one_case_per_edstays_row(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
        )
        assert len(cases) == 2
        assert {c.stay_id for c in cases} == {9001, 9002}

    def test_source_dataset_label_is_set_correctly(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
        )
        for case in cases:
            assert case.source_dataset == SOURCE_DATASET_LABEL
            assert case.source_dataset == "MIMIC-IV-ED-Demo-v2.2"

    def test_triage_time_input_never_contains_acuity_or_disposition(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        """
        The core leakage guarantee, checked against the actual adapter
        output (not just the schema), exactly as the project's existing
        KTAS leakage tests do for ktas_adapter.py.
        """
        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
        )
        for case in cases:
            ti_dict = case.to_triage_time_input().model_dump()
            assert "acuity" not in ti_dict
            assert "disposition" not in ti_dict
            assert "outtime" not in ti_dict
            assert "hadm_id" not in ti_dict

    def test_retrospective_labels_correctly_carry_acuity_and_disposition(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
        )
        case_9001 = next(c for c in cases if c.stay_id == 9001)
        retro = case_9001.to_retrospective_labels()
        assert retro.original_acuity == 1.0
        assert retro.disposition == "ADMITTED"
        assert retro.outtime == "2125-01-01 14:00:00"
        assert len(retro.diagnoses) == 1
        assert retro.diagnoses[0].icd_code == "I21.9"

    def test_triage_time_input_carries_real_vitals_correctly(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
        )
        case_9001 = next(c for c in cases if c.stay_id == 9001)
        ti = case_9001.to_triage_time_input()
        assert ti.chiefcomplaint == "CHEST PAIN"
        assert ti.heartrate == 110.0
        assert ti.o2sat == 91.0
        assert ti.temperature_unit == "F"
        assert ti.pain == "8"

    def test_dirty_pain_value_becomes_none_not_a_crash(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
        )
        case_9002 = next(c for c in cases if c.stay_id == 9002)
        ti = case_9002.to_triage_time_input()
        assert ti.pain is None  # "unable" in the fixture, correctly unparseable

    def test_vitalsign_rows_stored_separately_not_merged_into_triage_input(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        """
        Confirms the repeated-monitoring vitalsign rows are stored in
        vitals_timeseries (for potential future research use) but never
        flow into TriageTimeInput, which must reflect only the single
        triage.csv snapshot.
        """
        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
        )
        case_9001 = next(c for c in cases if c.stay_id == 9001)
        assert len(case_9001.vitals_timeseries) == 2  # both vitalsign rows for this stay
        ti = case_9001.to_triage_time_input()
        # triage.csv's heartrate for 9001 is 110.0; vitalsign's readings are
        # 112.0 and 105.0. The triage-time input must reflect triage.csv,
        # not be overwritten or averaged with the later monitoring readings.
        assert ti.heartrate == 110.0

    def test_stay_with_no_diagnosis_medrecon_or_pyxis_records_gets_empty_lists(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
        )
        case_9002 = next(c for c in cases if c.stay_id == 9002)
        assert case_9002.diagnoses == []
        assert case_9002.medrecon == []
        assert case_9002.pyxis == []
        assert case_9002.vitals_timeseries == []

    def test_n_parameter_limits_case_count(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df, n=1,
        )
        assert len(cases) == 1

    def test_real_unmodified_orchestrator_runs_on_mimic_case_without_special_casing(
        self, edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
    ):
        """
        Confirms the dual-pipeline architecture promise: a MIMIC-sourced
        case flows through the existing, completely unmodified
        run_workflow() orchestrator the same way a KTAS case does, with
        no MIMIC-specific code path in the orchestrator itself.
        """
        from app.agents.orchestrator import run_workflow

        cases = dataframe_to_cases(
            edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df,
        )
        case_9001 = next(c for c in cases if c.stay_id == 9001)  # o2sat=91, sbp=88 -> concern/critical
        result = run_workflow(case_9001, include_llm_explanation=False)
        assert result.stay_id == 9001
        assert result.triage_input.source_dataset == "MIMIC-IV-ED-Demo-v2.2"
        # sbp=88 is below the critical threshold (<90) per manchester_engine.py
        assert result.decision.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"
