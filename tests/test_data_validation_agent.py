"""
Tests for the Data Validation Agent.

Run with: pytest tests/test_data_validation_agent.py -v
"""
import pytest
from app.schemas.internal import TriageTimeInput
from app.agents.data_validation_agent import run_data_validation_agent


def _make_input(**kwargs) -> TriageTimeInput:
    defaults = {
        "subject_id": 1, "stay_id": 1,
        "chiefcomplaint": "chest pain",
        "temperature": 98.6, "heartrate": 80.0,
        "resprate": 16.0, "o2sat": 98.0,
        "sbp": 120.0, "dbp": 80.0, "pain": "5",
    }
    defaults.update(kwargs)
    return TriageTimeInput(**defaults)


class TestDataValidationAgent:
    def test_complete_input_passes(self):
        t = _make_input()
        result = run_data_validation_agent(t)
        assert result.validation_status == "TRIAGE_INPUT_DATA_COMPLETE"
        assert result.requires_human_data_review is False
        assert result.missing_required_fields == []

    def test_missing_pain_score_flagged(self):
        t = _make_input(pain=None)
        result = run_data_validation_agent(t)
        assert "pain" in result.missing_required_fields
        assert result.requires_human_data_review is True
        assert result.validation_status == "NEEDS_HUMAN_DATA_REVIEW"

    def test_missing_temperature_flagged(self):
        t = _make_input(temperature=None)
        result = run_data_validation_agent(t)
        assert "temperature" in result.missing_required_fields

    def test_unknown_chief_complaint_non_informative(self):
        t = _make_input(chiefcomplaint="UNKNOWN-CC")
        result = run_data_validation_agent(t)
        assert "chiefcomplaint" in result.non_informative_fields
        assert result.requires_human_data_review is True

    def test_empty_chief_complaint_missing(self):
        t = _make_input(chiefcomplaint="")
        result = run_data_validation_agent(t)
        assert result.requires_human_data_review is True

    def test_validation_never_assigns_triage_category(self):
        """Data validation agent must never produce a triage category."""
        t = _make_input()
        result = run_data_validation_agent(t)
        result_dict = result.model_dump()
        assert "category" not in result_dict
        assert "priority" not in result_dict
        assert "manchester" not in str(result_dict).lower()
