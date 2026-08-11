"""Regression guards for the second external audit's confirmed findings.

Most of these pin defects introduced by the v22.1 fixes themselves — an
incomplete refactor (the acuity drill), a cap applied at one layer but not the
other (the queue), and validation that was never written (the threshold).
"""
from __future__ import annotations

import pytest

from app.agents.autogen_multi_agent_team import (
    MAX_EXPLANATION_CHARS,
    MAX_EXPLANATION_SENTENCES,
    condense_explanation,
)
from app.analytics.itd_evidence import build_itd_evidence
from app.config import _parse_high_acuity_threshold


class TestThresholdValidation:
    """Every invalid value previously failed SILENTLY at the comparison."""

    @pytest.mark.parametrize("raw,expected", [("0.25", 0.25), ("0", 0.0), ("1", 1.0)])
    def test_valid_values_accepted(self, raw, expected):
        assert _parse_high_acuity_threshold(raw) == expected

    def test_artefact_defers_to_the_model_file(self):
        assert _parse_high_acuity_threshold("artefact") is None
        assert _parse_high_acuity_threshold("  ARTEFACT ") is None

    def test_nan_rejected(self):
        """NaN made every `high_prob >= tau` False, disabling the safety rule."""
        with pytest.raises(ValueError, match="finite"):
            _parse_high_acuity_threshold("nan")

    def test_infinity_rejected(self):
        with pytest.raises(ValueError, match="finite"):
            _parse_high_acuity_threshold("inf")

    @pytest.mark.parametrize("raw", ["-1", "-0.01", "1.5", "100"])
    def test_out_of_range_rejected(self, raw):
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            _parse_high_acuity_threshold(raw)

    def test_garbage_names_the_variable(self):
        with pytest.raises(ValueError, match="MIMIC_HIGH_ACUITY_THRESHOLD"):
            _parse_high_acuity_threshold("abc")


class TestExplanationLengthIsActuallyBounded:
    def test_one_enormous_sentence_is_capped(self):
        """A sentence cap alone does not bound length."""
        huge = "The patient is " + "very " * 600 + "unwell."
        assert len(huge) > 3000
        out = condense_explanation(huge)
        assert len(out) <= MAX_EXPLANATION_CHARS + 1

    def test_compliant_text_is_untouched(self):
        good = " ".join(f"Finding number {i} is recorded." for i in range(1, 5))
        assert condense_explanation(good) == good

    def test_sentence_cap_still_applies(self):
        many = " ".join(f"Sentence {i} here." for i in range(1, 12))
        out = condense_explanation(many)
        assert len([p for p in out.split(".") if p.strip()]) <= MAX_EXPLANATION_SENTENCES

    def test_truncation_does_not_leave_a_dangling_fragment(self):
        huge = "The patient is " + "very " * 600 + "unwell."
        out = condense_explanation(huge)
        assert out.endswith(("…", ".", "!", "?"))


class TestItdUsesLatestStatePerCase:
    def _states(self):
        return [
            # Older row: escalation open, vitals overdue.
            {"record_kind": "workflow_state", "case_uid": "c1",
             "timestamp_utc": "2026-08-07T09:00:00+00:00",
             "escalation_status": "requested", "overdue_vitals_alert_active": True},
            # Newer row for the SAME case: resolved and cleared.
            {"record_kind": "workflow_state", "case_uid": "c1",
             "timestamp_utc": "2026-08-07T11:00:00+00:00",
             "escalation_status": "closed", "overdue_vitals_alert_active": False},
        ]

    def test_closed_escalation_is_not_reported_as_open(self):
        ev = build_itd_evidence(self._states(), window_days=None)
        assert ev["open_escalations"] == 0, "counted a historical row, not the latest state"

    def test_cleared_overdue_alert_is_not_reported_as_active(self):
        ev = build_itd_evidence(self._states(), window_days=None)
        assert ev["overdue_vitals_alerts_active"] == 0

    def test_genuinely_open_case_is_still_reported(self):
        states = [{"record_kind": "workflow_state", "case_uid": "c2",
                   "timestamp_utc": "2026-08-07T09:00:00+00:00",
                   "escalation_status": "requested", "overdue_vitals_alert_active": True}]
        ev = build_itd_evidence(states, window_days=None)
        assert ev["open_escalations"] == 1
        assert ev["overdue_vitals_alerts_active"] == 1

    def test_two_cases_are_counted_independently(self):
        states = self._states() + [
            {"record_kind": "workflow_state", "case_uid": "c3",
             "timestamp_utc": "2026-08-07T10:00:00+00:00",
             "escalation_status": "pending", "overdue_vitals_alert_active": False}]
        assert build_itd_evidence(states, window_days=None)["open_escalations"] == 1


class TestAgentTurnsGetTheSameTreatmentAsTheSummary:
    def test_directive_advice_in_a_turn_is_withheld_and_recorded(self):
        from app.api.safe_dto import safe_multiagent_explanation_response
        dto = safe_multiagent_explanation_response(
            case_uid="c1", source_dataset="MIMIC-IV-ED-Full-v2.2",
            team_result={
                "status": "PASS",
                "final_explanation": "The estimate reflects the recorded vitals.",
                "agent_turns": [{"agent": "Intake", "text": "Give aspirin immediately."}],
                "safety_failures": [],
            },
        )
        turn_text = dto["agent_turns"][0]["text"]
        assert "aspirin" not in turn_text.lower(), "directive advice reached the clinician"
        assert dto["safety_failures"], "the finding must be recorded, not silently dropped"

    def test_safe_turns_survive_but_are_length_capped(self):
        from app.api.safe_dto import safe_multiagent_explanation_response
        long_turn = "The recorded heart rate is elevated " + "and sustained " * 300 + "."
        dto = safe_multiagent_explanation_response(
            case_uid="c1", source_dataset="MIMIC-IV-ED-Full-v2.2",
            team_result={"status": "PASS", "final_explanation": "Short summary.",
                         "agent_turns": [{"agent": "Validation", "text": long_turn}],
                         "safety_failures": []},
        )
        assert len(dto["agent_turns"][0]["text"]) <= MAX_EXPLANATION_CHARS + 1
        assert "heart rate" in dto["agent_turns"][0]["text"].lower()
