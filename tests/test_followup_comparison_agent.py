"""
Tests for app/agents/followup_comparison_agent.py.

The most important test class here is TestBandMatchesLiveEngine, which
does NOT use a frozen copy of threshold numbers. It calls the real
app.rules.manchester_engine functions directly at test time, builds the
empirical ground-truth table fresh, and checks _band() against it. If
anyone changes a threshold in manchester_engine.py without updating
_band() to match, this test fails -- it cannot go stale the way a
hand-written list of "expected" numbers could.
"""
from __future__ import annotations

import pytest

from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource, TriageTimeInput
from app.agents.orchestrator import run_workflow
from app.agents.followup_comparison_agent import (
    _band,
    _compare_vital,
    _direction_from_bands,
    compare_follow_up,
)
from app.schemas.followup import FollowUpLinkRequest
from app.rules.manchester_engine import _critical_vital_flags, _concern_vital_flags


def _make_triage_input(**overrides) -> TriageTimeInput:
    defaults = dict(
        subject_id=1, stay_id=1, chiefcomplaint="test",
        heartrate=84.0, resprate=16.0, o2sat=98.0, sbp=118.0, dbp=76.0,
        temperature=36.7, temperature_unit="C", pain="2",
    )
    defaults.update(overrides)
    return TriageTimeInput(**defaults)


def _make_case(stay_id: int, subject_id: int = 1, **triage_overrides) -> EDTriageCase:
    triage_kwargs = dict(
        subject_id=subject_id, stay_id=stay_id, chiefcomplaint="test",
        heartrate=84.0, resprate=16.0, o2sat=98.0, sbp=118.0, dbp=76.0,
        temperature=36.7, temperature_unit="C", pain="2",
    )
    triage_kwargs.update(triage_overrides)
    return EDTriageCase(
        stay_id=stay_id, subject_id=subject_id, source_dataset="MIMIC-IV-ED-Demo-v2.2",
        edstay=EDStaySource(subject_id=subject_id, stay_id=stay_id,
                             gender="F", arrival_transport="Walk-in"),
        triage=TriageSource(**triage_kwargs),
    )


def _live_band_from_engine(field: str, value: float) -> str:
    """
    Re-derives the empirical ground truth directly from the live engine
    functions, for one field/value, exactly as _band()'s docstring
    promises this test does. No frozen numbers are used here.
    """
    kwargs = dict(temperature=36.7, temperature_unit="C", heartrate=84.0,
                  resprate=16.0, o2sat=98.0, sbp=118.0)
    if field == "temperature":
        # manchester_engine converts via temperature_c(); to test a Celsius
        # value directly we set temperature_unit='C' and pass it straight through.
        kwargs["temperature"] = value
        kwargs["temperature_unit"] = "C"
    else:
        kwargs[field] = value
    t = _make_triage_input(**kwargs)
    if _critical_vital_flags(t):
        return "CRITICAL"
    if _concern_vital_flags(t):
        return "CONCERN"
    return "NORMAL"


class TestBandMatchesLiveEngine:
    """
    Sweeps boundary values for every vital and confirms _band() agrees
    with the live engine functions at every point. This is the test that
    actually caught the original bug (an earlier version of _band used a
    single generic four-number comparison shape that disagreed with the
    engine at most of these boundary points).
    """

    SWEEP_POINTS = [
        ("o2sat", [85, 89.9, 90, 92, 94.9, 95, 99]),
        ("resprate", [5, 7.9, 8, 20, 24.9, 25, 29, 29.1, 30]),
        ("heartrate", [35, 39.9, 40, 90, 99.9, 100, 100.1, 130, 130.1, 135]),
        ("sbp", [85, 89.9, 90, 95, 99.9, 100, 150, 220, 220.1]),
        ("temperature", [34.9, 35.0, 36.7, 39.4, 39.5, 40.9, 41.0, 41.1]),
    ]

    @pytest.mark.parametrize(
        "field,value",
        [(f, v) for f, values in SWEEP_POINTS for v in values],
    )
    def test_band_matches_live_engine_at_boundary(self, field, value):
        expected = _live_band_from_engine(field, value)
        actual = _band(field, value)
        assert actual == expected, (
            f"_band({field!r}, {value}) returned {actual!r} but the live "
            f"engine functions classify this as {expected!r}. _band() has "
            f"drifted from app.rules.manchester_engine and must be updated "
            f"to match."
        )


class TestCompareVital:
    def test_both_missing_is_unchanged(self):
        d = _compare_vital("heartrate", "bpm", None, None)
        assert d.direction == "UNCHANGED"
        assert d.clinically_notable is False

    def test_newly_missing_is_notable(self):
        d = _compare_vital("o2sat", "%", 98.0, None)
        assert d.direction == "NEWLY_MISSING"
        assert d.clinically_notable is True

    def test_newly_available_is_not_notable(self):
        d = _compare_vital("o2sat", "%", None, 98.0)
        assert d.direction == "NEWLY_AVAILABLE"
        assert d.clinically_notable is False

    def test_normal_to_critical_is_worsened_and_notable(self):
        d = _compare_vital("o2sat", "%", 98.0, 85.0)
        assert d.direction == "WORSENED"
        assert d.clinically_notable is True

    def test_critical_to_normal_is_improved_and_not_notable(self):
        d = _compare_vital("o2sat", "%", 85.0, 98.0)
        assert d.direction == "IMPROVED"
        assert d.clinically_notable is False

    def test_normal_to_normal_different_values_is_unchanged_and_not_notable(self):
        """
        Two values that are both NORMAL but numerically different (e.g.
        heart rate 60 -> 65) must not be falsely labelled WORSENED or
        IMPROVED -- this is the case the old abs(x-70) heuristic got
        wrong by inventing a fake direction.
        """
        d = _compare_vital("heartrate", "bpm", 60.0, 65.0)
        assert d.direction == "UNCHANGED"
        assert d.clinically_notable is False

    def test_concern_to_concern_is_unchanged_and_not_notable(self):
        d = _compare_vital("sbp", "mmHg", 92.0, 96.0)
        assert d.direction == "UNCHANGED"
        assert d.clinically_notable is False

    def test_temperature_rising_within_normal_band_is_unchanged_not_improved(self):
        """
        This is the exact bug found during manual testing: 36.7 -> 38.9
        is a real fever rising, both values are NORMAL by the engine's own
        definition (since the engine has no low-temperature concern band
        and 38.9 is below the 39.5 concern threshold), so this must be
        UNCHANGED (honest about the ambiguity), not the wrong IMPROVED.
        """
        d = _compare_vital("temperature", "C", 36.7, 38.9)
        assert d.direction == "UNCHANGED"
        assert d.clinically_notable is False


class TestDirectionFromBands:
    def test_normal_to_concern_is_worsened(self):
        assert _direction_from_bands("NORMAL", "CONCERN", 1.0) == "WORSENED"

    def test_concern_to_critical_is_worsened(self):
        assert _direction_from_bands("CONCERN", "CRITICAL", 1.0) == "WORSENED"

    def test_critical_to_normal_is_improved(self):
        assert _direction_from_bands("CRITICAL", "NORMAL", -1.0) == "IMPROVED"

    def test_same_band_is_unchanged_regardless_of_delta_sign(self):
        assert _direction_from_bands("NORMAL", "NORMAL", 5.0) == "UNCHANGED"
        assert _direction_from_bands("NORMAL", "NORMAL", -5.0) == "UNCHANGED"


class TestWorkflowAction:
    """
    Tests for the workflow_action field, added in response to a request
    to give an explicit next-step label for follow-up comparisons (e.g.
    'reassign triage assignments') without the system making any new
    autonomous clinical decision. workflow_action is derived purely from
    fields already computed elsewhere in compare_follow_up(), never
    independently.
    """

    def test_no_escalation_gives_no_escalation_detected_action(self):
        case_a = _make_case(900001, heartrate=78.0, o2sat=98.0)
        case_b = _make_case(900002, heartrate=80.0, o2sat=97.0)
        result_a = run_workflow(case_a, include_llm_explanation=False)
        result_b = run_workflow(case_b, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test",
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert comparison.workflow_action == "NO_ESCALATION_DETECTED"

    def test_escalation_gives_escalation_required_action(self):
        stable = _make_case(900001, heartrate=78.0, resprate=16.0, o2sat=98.0,
                             sbp=118.0, temperature=36.7)
        critical = _make_case(900002, subject_id=1, heartrate=132.0, resprate=28.0,
                               o2sat=86.0, sbp=82.0, temperature=38.9)
        result_a = run_workflow(stable, include_llm_explanation=False)
        result_b = run_workflow(critical, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test",
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert comparison.workflow_action == "ESCALATION_REQUIRED"

    def test_missing_chief_complaint_on_new_visit_gives_clinician_intervention_action(self):
        """
        A new visit with a missing chief complaint produces
        REQUIRES_CLINICIAN_REVIEW as its classification_status.
        workflow_action must report CLINICIAN_INTERVENTION_REQUIRED for
        this specifically (data-completeness issue), not
        ESCALATION_REQUIRED (a physiological-deterioration issue), even
        though both call for a human to act.
        """
        case_a = _make_case(900001)
        case_b = _make_case(900002, chiefcomplaint=None)
        result_a = run_workflow(case_a, include_llm_explanation=False)
        result_b = run_workflow(case_b, include_llm_explanation=False)
        assert result_b.decision.classification_status == "REQUIRES_CLINICIAN_REVIEW"
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test",
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert comparison.workflow_action == "CLINICIAN_INTERVENTION_REQUIRED"

    def test_workflow_action_never_overrides_requires_clinician_review(self):
        """workflow_action is a label, not a gate -- requires_clinician_review
        must stay True regardless of which workflow_action value results."""
        case_a = _make_case(900001)
        case_b = _make_case(900002)
        result_a = run_workflow(case_a, include_llm_explanation=False)
        result_b = run_workflow(case_b, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test",
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert comparison.requires_clinician_review is True

    def test_workflow_action_is_always_one_of_the_three_enumerated_values(self):
        """No other string should ever be produced."""
        case_a = _make_case(900001)
        case_b = _make_case(900002, o2sat=85.0)
        result_a = run_workflow(case_a, include_llm_explanation=False)
        result_b = run_workflow(case_b, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test",
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert comparison.workflow_action in {
            "ESCALATION_REQUIRED", "CLINICIAN_INTERVENTION_REQUIRED", "NO_ESCALATION_DETECTED",
        }


class TestCompareFollowUp:
    def test_worsening_scenario_detects_escalation(self):
        stable = _make_case(900001, heartrate=78.0, resprate=16.0, o2sat=98.0,
                             sbp=118.0, temperature=36.7)
        critical = _make_case(900002, subject_id=1, heartrate=132.0, resprate=28.0,
                               o2sat=86.0, sbp=82.0, temperature=38.9)
        result_a = run_workflow(stable, include_llm_explanation=False)
        result_b = run_workflow(critical, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test", is_demonstration_scenario=True,
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert comparison.escalation_detected is True
        assert "ESCALATION" in comparison.escalation_note
        assert comparison.requires_clinician_review is True

    def test_stable_scenario_no_escalation(self):
        case_a = _make_case(900001, heartrate=78.0, o2sat=98.0)
        case_b = _make_case(900002, heartrate=80.0, o2sat=97.0)
        result_a = run_workflow(case_a, include_llm_explanation=False)
        result_b = run_workflow(case_b, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test", is_demonstration_scenario=True,
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert comparison.escalation_detected is False
        assert "No escalation detected" in comparison.escalation_note

    def test_demonstration_scenario_flag_appears_in_note(self):
        case_a = _make_case(900001)
        case_b = _make_case(900002)
        result_a = run_workflow(case_a, include_llm_explanation=False)
        result_b = run_workflow(case_b, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test", is_demonstration_scenario=True,
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert "DEMONSTRATION SCENARIO" in comparison.escalation_note
        assert comparison.is_demonstration_scenario is True

    def test_non_demonstration_scenario_does_not_add_demo_label(self):
        case_a = _make_case(900001)
        case_b = _make_case(900002)
        result_a = run_workflow(case_a, include_llm_explanation=False)
        result_b = run_workflow(case_b, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test", is_demonstration_scenario=False,
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert "DEMONSTRATION SCENARIO" not in comparison.escalation_note

    def test_never_assigns_a_triage_category_itself(self):
        """
        The comparison result must not contain any field that looks like
        an independently-assigned category -- it only reports the
        classification_status strings already produced by the unmodified
        orchestrator for each stay.
        """
        case_a = _make_case(900001)
        case_b = _make_case(900002)
        result_a = run_workflow(case_a, include_llm_explanation=False)
        result_b = run_workflow(case_b, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test",
        )
        comparison = compare_follow_up(link, result_a, result_b)
        dumped = comparison.model_dump()
        assert "category" not in dumped
        assert "priority" not in dumped
        assert comparison.previous_classification_status == result_a.decision.classification_status
        assert comparison.new_classification_status == result_b.decision.classification_status

    def test_requires_clinician_review_always_true(self):
        case_a = _make_case(900001)
        case_b = _make_case(900002)
        result_a = run_workflow(case_a, include_llm_explanation=False)
        result_b = run_workflow(case_b, include_llm_explanation=False)
        link = FollowUpLinkRequest(
            previous_stay_id=900001, new_stay_id=900002, linked_by="test",
            link_reason="unit test",
        )
        comparison = compare_follow_up(link, result_a, result_b)
        assert comparison.requires_clinician_review is True


# (TestDatasetSpecificFollowUp removed: it relied on demo/KTAS ML acuity, which no
# longer exists. The follow-up comparison logic itself is exercised by the
# vital-comparison/band/workflow-action tests above and by the backend
# /cases/{case_uid}/followups tests in test_no_raw_id_comprehensive.py.)
