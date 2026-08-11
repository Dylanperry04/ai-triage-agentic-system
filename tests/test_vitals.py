"""
Tests for the shared vital-sign unit conversion module (app/rules/vitals.py).

This module exists to fix a real bug found during the KTAS migration review:
manchester_engine.py and safety_review_agent.py each had their own identical
copy of a Celsius-conversion function. Two independently maintained copies of
a clinical threshold-conversion function is a safety risk -- if one copy is
edited in a future change and the other is not, the two safety-relevant code
paths would silently diverge in how they interpret temperature, with no test
failure to catch it. These tests confirm there is now exactly one
implementation, and that both consuming modules actually use it.
"""
from app.rules.vitals import temperature_c
from app.schemas.internal import TriageTimeInput


class TestTemperatureConversion:
    def test_celsius_passes_through_unchanged(self):
        t = TriageTimeInput(subject_id=1, stay_id=1, temperature=36.6, temperature_unit="C")
        assert temperature_c(t) == 36.6

    def test_fahrenheit_converts_correctly(self):
        t = TriageTimeInput(subject_id=1, stay_id=1, temperature=98.6, temperature_unit="F")
        assert round(temperature_c(t), 1) == 37.0

    def test_known_clinical_conversion_points(self):
        # These specific points matter because manchester_engine.py's old
        # hardcoded-Fahrenheit thresholds (105.8F, 95.0F, 103.1F, 101.3F) must
        # convert to exactly these Celsius values for the KTAS migration to
        # have preserved the original clinical thresholds correctly.
        cases = [
            (105.8, 41.0),  # critical hyperpyrexia threshold
            (95.0, 35.0),   # critical hypothermia threshold
            (103.1, 39.5),  # concern high-fever threshold
            (101.3, 38.5),  # fever pathway threshold
        ]
        for fahrenheit, expected_celsius in cases:
            t = TriageTimeInput(subject_id=1, stay_id=1, temperature=fahrenheit, temperature_unit="F")
            assert round(temperature_c(t), 1) == expected_celsius

    def test_none_temperature_returns_none(self):
        t = TriageTimeInput(subject_id=1, stay_id=1, temperature=None)
        assert temperature_c(t) is None

    def test_missing_unit_defaults_to_fahrenheit(self):
        # temperature_unit defaults to "F" in TriageTimeInput's schema, which
        # matches MIMIC's convention. This must not silently change.
        t = TriageTimeInput(subject_id=1, stay_id=1, temperature=98.6)
        assert t.temperature_unit == "F"
        assert round(temperature_c(t), 1) == 37.0

    def test_manchester_engine_and_safety_review_agent_use_the_same_function(self):
        """
        Proves both consuming modules import the exact same function object
        from app.rules.vitals, rather than each keeping their own copy that
        could drift apart in a future edit.
        """
        import app.rules.manchester_engine as manchester_engine
        import app.agents.safety_review_agent as safety_review_agent

        assert manchester_engine._temperature_c is temperature_c
        assert safety_review_agent._temperature_c is temperature_c
        assert manchester_engine._temperature_c is safety_review_agent._temperature_c
