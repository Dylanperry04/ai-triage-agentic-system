"""
Tests for the leakage guard.

The leakage guard is a critical safety control. If it fails, retrospective
outcome data could contaminate the triage-time workflow, making trained
models appear to perform better than they actually would in deployment.

Run with: pytest tests/test_leakage_guard.py -v
"""
import pytest
from app.schemas.internal import TriageTimeInput
from app.rules.leakage_guard import validate_triage_time_input


class TestLeakageGuard:
    def test_clean_input_passes(self):
        """A TriageTimeInput with only triage-time fields should pass."""
        t = TriageTimeInput(
            subject_id=1,
            stay_id=1,
            chiefcomplaint="chest pain",
            temperature=98.6,
            heartrate=80.0,
            resprate=16.0,
            o2sat=98.0,
            sbp=120.0,
            dbp=80.0,
            pain="5",
        )
        assert validate_triage_time_input(t) is True

    def test_triage_input_schema_has_no_retrospective_fields(self):
        """
        The TriageTimeInput model must not contain any retrospective fields.
        This test catches schema drift where someone accidentally adds
        a leakage field to the model.
        """
        from app.schemas.mimic_ed import RETROSPECTIVE_OR_LEAKAGE_COLUMNS
        t = TriageTimeInput(subject_id=1, stay_id=1)
        field_names = set(t.model_dump().keys())
        leakage_fields = set(RETROSPECTIVE_OR_LEAKAGE_COLUMNS)
        intersection = field_names & leakage_fields
        assert intersection == set(), (
            f"TriageTimeInput contains retrospective fields: {intersection}. "
            "These must be removed from the schema immediately."
        )

    def test_ed_triage_case_separates_data_correctly(self):
        """
        EDTriageCase.to_triage_time_input() must not expose retrospective data.
        EDTriageCase.to_retrospective_labels() must contain the outcome data.
        """
        from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource

        case = EDTriageCase(
            stay_id=1,
            subject_id=1,
            edstay=EDStaySource(
                subject_id=1,
                stay_id=1,
                disposition="ADMITTED",
                outtime="2024-01-01 12:00:00",
            ),
            triage=TriageSource(
                subject_id=1,
                stay_id=1,
                acuity=2.0,
                chiefcomplaint="chest pain",
                heartrate=80.0,
            ),
        )

        triage_input = case.to_triage_time_input()
        retro_labels = case.to_retrospective_labels()

        # Triage input must not contain outcome data
        triage_dict = triage_input.model_dump()
        assert "disposition" not in triage_dict
        assert "outtime" not in triage_dict
        assert "acuity" not in triage_dict

        # Retrospective labels must contain outcome data
        assert retro_labels.original_acuity == 2.0
        assert retro_labels.disposition == "ADMITTED"

    def test_ktas_adapter_output_separates_data_correctly(self):
        """
        Exercises the real Kaggle KTAS adapter (dataframe_to_cases), not just
        hand-built schema objects, to confirm KTAS-specific retrospective
        fields (KTAS_expert, KTAS_RN, mistriage, Error_group, Disposition,
        Diagnosis in ED, Length of stay, KTAS duration) never reach
        TriageTimeInput, and do correctly reach RetrospectiveLabels.
        """
        import pandas as pd
        from app.data_pipeline.ktas_adapter import dataframe_to_cases

        row = {
            "Group": 2, "Sex": 1, "Age": 71,
            "Patients number per hour": 3, "Arrival mode": 3, "Injury": 2,
            "Chief_complain": "right ocular pain", "Mental": 1, "Pain": 1,
            "NRS_pain": "2", "SBP": "160", "DBP": "100", "HR": "84", "RR": "18",
            "BT": "36.6", "Saturation": "100", "KTAS_RN": 2,
            "Diagnosis in ED": "Corneal abrasion", "Disposition": 1,
            "KTAS_expert": 1, "Error_group": 4, "Length of stay_min": 86,
            "KTAS duration_min": "5,00", "mistriage": 1,
        }
        df = pd.DataFrame([row])
        cases = dataframe_to_cases(df)
        assert len(cases) == 1
        case = cases[0]

        triage_input = case.to_triage_time_input()
        triage_dict = triage_input.model_dump()
        for forbidden in (
            "ktas_expert", "ktas_rn", "mistriage", "error_group",
            "disposition", "disposition_code", "diagnosis_in_ed",
            "length_of_stay_min", "ktas_duration_min",
        ):
            assert forbidden not in triage_dict, (
                f"KTAS retrospective field '{forbidden}' leaked into TriageTimeInput"
            )

        retro = case.to_retrospective_labels()
        assert retro.ktas_expert == 1
        assert retro.ktas_rn == 2
        assert retro.mistriage == 1
        assert retro.error_group == 4
        assert retro.diagnosis_in_ed == "Corneal abrasion"
        assert retro.disposition_code == 1
