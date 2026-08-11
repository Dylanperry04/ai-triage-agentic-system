"""
Tests for the Safety Review Agent.

Run with: pytest tests/test_safety_review_agent.py -v
"""
import pytest
from app.schemas.internal import TriageTimeInput
from app.agents.safety_review_agent import run_safety_review


def _make_input(**kwargs) -> TriageTimeInput:
    defaults = {
        "subject_id": 1, "stay_id": 1,
        # Deliberately NOT a high-risk complaint (see
        # HIGH_RISK_COMPLAINT_PATTERNS in app/agents/safety_review_agent.py) --
        # this default is used by several tests below that intend an
        # ordinary, non-high-risk baseline case (leakage guard, notes
        # presence, critical-vitals safety) and have nothing to do with
        # high-risk complaint detection. The original default was
        # "chest pain", which collided with that exact pattern list once
        # a later review pass fixed high-risk complaints to correctly
        # set is_safe_to_present=False regardless of vitals -- the one
        # test that genuinely wants to test chest pain
        # (test_chest_pain_high_risk_flag below) already passes it
        # explicitly and is unaffected by this change.
        "chiefcomplaint": "twisted ankle",
        "temperature": 98.6, "heartrate": 80.0,
        "resprate": 16.0, "o2sat": 98.0,
        "sbp": 120.0, "dbp": 80.0, "pain": "5",
    }
    defaults.update(kwargs)
    return TriageTimeInput(**defaults)


class TestSafetyReviewAgent:
    def test_complete_input_is_safe_to_present(self):
        t = _make_input()
        result = run_safety_review(t)
        assert result.is_safe_to_present is True
        assert result.leakage_guard_passed is True

    def test_missing_spo2_is_flagged(self):
        t = _make_input(o2sat=None)
        result = run_safety_review(t)
        assert "MISSING_CRITICAL_VITAL:o2sat" in result.data_quality_flags
        assert "o2sat" in result.critical_missing_vitals
        assert result.is_safe_to_present is False

    def test_missing_chief_complaint_is_flagged(self):
        t = _make_input(chiefcomplaint=None)
        result = run_safety_review(t)
        assert "MISSING_CHIEF_COMPLAINT" in result.data_quality_flags
        assert result.is_safe_to_present is False

    def test_chest_pain_high_risk_flag(self):
        t = _make_input(chiefcomplaint="chest pain")
        result = run_safety_review(t)
        assert any("CHEST_PAIN" in f for f in result.data_quality_flags)

    def test_overdose_high_risk_flag(self):
        t = _make_input(chiefcomplaint="overdose paracetamol")
        result = run_safety_review(t)
        assert any("OVERDOSE" in f for f in result.data_quality_flags)

    def test_leakage_guard_passes_on_clean_input(self):
        t = _make_input()
        result = run_safety_review(t)
        assert result.leakage_guard_passed is True

    def test_notes_always_present(self):
        t = _make_input()
        result = run_safety_review(t)
        assert len(result.notes) >= 1

    def test_critical_physiology_makes_not_safe_to_present(self):
        """
        SpO2=80 is present (not missing) but critically abnormal.
        is_safe_to_present must be False — consistent with CRITICAL_PHYSIOLOGY_FLAGGED
        from the rules engine.
        """
        t = _make_input(o2sat=80.0)
        result = run_safety_review(t)
        assert result.is_safe_to_present is False
        assert any("CRITICAL_PHYSIOLOGY" in f for f in result.data_quality_flags)

    def test_normal_critical_vitals_are_safe_to_present(self):
        t = _make_input()  # all vitals normal
        result = run_safety_review(t)
        assert result.is_safe_to_present is True

    def test_critical_hr_makes_not_safe_to_present(self):
        t = _make_input(heartrate=135.0)
        result = run_safety_review(t)
        assert result.is_safe_to_present is False
