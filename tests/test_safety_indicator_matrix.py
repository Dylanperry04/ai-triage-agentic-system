"""
Safety-review high-risk complaint indicator matrix.

This is a SEPARATE mechanism from the Manchester engine's pathway
matcher (see tests/test_triage_indicator_matrix.py and
app/rules/manchester_engine.py's _PATHWAYS), even though both happen to
check overlapping keywords (cardiac arrest, anaphylaxis, altered mental
status). The Manchester engine produces PATHWAY_MATCHED_{NAME} reason
codes on ManchesterDecision; this file covers
app/agents/safety_review_agent.py's HIGH_RISK_COMPLAINT_PATTERNS, which
produces HIGH_RISK_COMPLAINT_PATTERN:{CODE} flags on a completely
different result object, SafetyReviewResult. A test asserting on one
tells you nothing about the other -- confirmed directly while
investigating this gap: the existing 30-row Manchester pathway matrix
already covered "cardiac arrest", "anaphylaxis", and "altered mental
status" as Manchester pathways, which gave a false impression of
coverage for the safety-review side of those same three keywords, which
in fact had none.

Before this file existed, only 2 of the 10 keyword-pattern entries
(chest pain, overdose) had ANY test coverage anywhere in this project,
confirmed by reading tests/test_safety_review_agent.py in full. The
other 7 distinct reason codes had zero coverage.

A SEPARATE, SEPARATELY-VERIFIED REAL BUG WAS FOUND AND FIXED while
building this coverage (not just a coverage gap): a chief complaint of
"cardiac arrest witnessed" with otherwise normal-looking recorded vitals
previously returned is_safe_to_present=True from run_safety_review(),
since the high-risk-pattern match was only ever recorded as a string in
data_quality_flags with nothing downstream consulting it. This meant
app/agents/orchestrator.py's workflow_action (the most prominent label
in the Streamlit assessment card) would render as
NO_CRITICAL_PHYSIOLOGY_FLAGGED -- the calmest possible label -- for one
of the most dangerous complaint categories the system recognises. Fixed
by adding an explicit, typed high_risk_complaint_detected field to
SafetyReviewResult and folding it into is_safe_to_present's formula, so
a high-risk complaint pattern alone is now sufficient to flag a case as
unsafe-to-present-as-routine, regardless of what the recorded vitals
show. TestHighRiskComplaintMakesUnsafeToPresent below is the direct
regression guard for that fix; TestEveryHighRiskPatternIsDetected is the
coverage matrix itself.

HOW EVERY EXPECTED VALUE BELOW WAS DERIVED: every entry was confirmed by
directly calling run_safety_review() with the listed complaint phrase
and reading its real output, not assumed from reading
HIGH_RISK_COMPLAINT_PATTERNS' source alone.
"""
from __future__ import annotations

from typing import NamedTuple

import pytest

from app.agents.safety_review_agent import run_safety_review
from app.schemas.internal import TriageTimeInput


class HighRiskIndicatorCase(NamedTuple):
    indicator: str
    chiefcomplaint: str
    expected_code: str


# One row per distinct reason code in HIGH_RISK_COMPLAINT_PATTERNS
# (app/agents/safety_review_agent.py). SELF_HARM_RISK and
# ALTERED_CONSCIOUSNESS_HIGH_RISK are each shared by multiple keyword
# phrasings in the real source list (suicide/suicidal/self harm;
# altered mental/mental change/unconscious/unresponsive) -- this matrix
# tests one representative phrasing per distinct CODE, not one row per
# keyword, since the underlying behaviour being verified is "does this
# code get produced and does it make the case unsafe to present",
# which does not differ between synonyms of the same code.
HIGH_RISK_INDICATOR_CASES: list[HighRiskIndicatorCase] = [
    HighRiskIndicatorCase(
        "Cardiac arrest complaint", "cardiac arrest witnessed", "CARDIAC_ARREST_COMPLAINT"
    ),
    HighRiskIndicatorCase(
        "Chest pain complaint", "chest pain", "CHEST_PAIN_HIGH_RISK"
    ),
    HighRiskIndicatorCase(
        "Dyspnoea complaint (shortness of breath phrasing)",
        "shortness of breath", "DYSPNOEA_HIGH_RISK",
    ),
    HighRiskIndicatorCase(
        "Dyspnoea complaint (dyspnea phrasing)", "dyspnea on exertion", "DYSPNOEA_HIGH_RISK"
    ),
    HighRiskIndicatorCase(
        "Stroke complaint", "stroke symptoms, facial droop", "STROKE_COMPLAINT_HIGH_RISK"
    ),
    HighRiskIndicatorCase(
        "Anaphylaxis complaint", "anaphylaxis, throat closing", "ANAPHYLAXIS_COMPLAINT_HIGH_RISK"
    ),
    HighRiskIndicatorCase(
        "Overdose complaint", "overdose paracetamol", "OVERDOSE_COMPLAINT_HIGH_RISK"
    ),
    HighRiskIndicatorCase(
        "Self-harm complaint (suicide phrasing)", "suicide attempt", "SELF_HARM_RISK"
    ),
    HighRiskIndicatorCase(
        "Self-harm complaint (suicidal phrasing)", "suicidal ideation", "SELF_HARM_RISK"
    ),
    HighRiskIndicatorCase(
        "Self-harm complaint (self harm phrasing)", "self harm, cutting", "SELF_HARM_RISK"
    ),
    HighRiskIndicatorCase(
        "Altered consciousness (altered mental phrasing)",
        "altered mental status", "ALTERED_CONSCIOUSNESS_HIGH_RISK",
    ),
    HighRiskIndicatorCase(
        "Altered consciousness (mental change phrasing)",
        "mental change since this morning", "ALTERED_CONSCIOUSNESS_HIGH_RISK",
    ),
    HighRiskIndicatorCase(
        "Altered consciousness (unconscious phrasing)",
        "found unconscious at home", "ALTERED_CONSCIOUSNESS_HIGH_RISK",
    ),
    HighRiskIndicatorCase(
        "Altered consciousness (unresponsive phrasing)",
        "unresponsive on arrival", "ALTERED_CONSCIOUSNESS_HIGH_RISK",
    ),
    HighRiskIndicatorCase(
        "Sepsis complaint", "sepsis suspected, fever and rigors", "SEPSIS_HIGH_RISK"
    ),
    HighRiskIndicatorCase(
        "Intubated arrival complaint", "intubated on arrival by EMS", "INTUBATED_ARRIVAL_HIGH_RISK"
    ),
]


def _make_normal_vitals_input(chiefcomplaint: str) -> TriageTimeInput:
    """
    Deliberately NORMAL/clean vitals throughout, so each row isolates
    "this complaint pattern alone, with otherwise unremarkable vitals" --
    exactly the scenario the real bug above was found in. If vitals were
    abnormal too, a passing test would not distinguish "the complaint
    pattern correctly triggered this" from "the abnormal vitals would
    have triggered it anyway via has_critical_physiology".
    """
    return TriageTimeInput(
        subject_id=1, stay_id=1, chiefcomplaint=chiefcomplaint,
        temperature=36.7, temperature_unit="C", heartrate=80.0,
        resprate=16.0, o2sat=98.0, sbp=120.0, dbp=80.0, pain="3",
    )


class TestEveryHighRiskPatternIsDetected:
    """
    The coverage matrix itself: one test per distinct reason code,
    confirming the pattern is genuinely matched and produces the
    expected flag.
    """

    @pytest.mark.parametrize("case", HIGH_RISK_INDICATOR_CASES, ids=lambda c: c.indicator)
    def test_pattern_produces_expected_flag(self, case: HighRiskIndicatorCase):
        t = _make_normal_vitals_input(case.chiefcomplaint)
        result = run_safety_review(t)
        expected_flag = f"HIGH_RISK_COMPLAINT_PATTERN:{case.expected_code}"
        assert expected_flag in result.data_quality_flags, (
            f"[{case.indicator}] complaint {case.chiefcomplaint!r} did not "
            f"produce the expected flag {expected_flag!r}. "
            f"Got flags: {result.data_quality_flags}"
        )

    def test_every_distinct_code_in_the_real_source_list_has_a_row_above(self):
        """
        Structural guard: confirms HIGH_RISK_INDICATOR_CASES covers every
        DISTINCT code in the real HIGH_RISK_COMPLAINT_PATTERNS list
        (app/agents/safety_review_agent.py), not just a fixed count that
        could silently drift if a new pattern is added there later
        without a matching row here.
        """
        from app.agents.safety_review_agent import HIGH_RISK_COMPLAINT_PATTERNS

        real_distinct_codes = {code for _pattern, code in HIGH_RISK_COMPLAINT_PATTERNS}
        covered_codes = {case.expected_code for case in HIGH_RISK_INDICATOR_CASES}
        missing = real_distinct_codes - covered_codes
        assert not missing, (
            f"HIGH_RISK_COMPLAINT_PATTERNS has codes with no row in this matrix: "
            f"{missing}. Add a HighRiskIndicatorCase for each."
        )


class TestHighRiskComplaintMakesUnsafeToPresent:
    """
    Direct regression guard for the real behavioral bug found and fixed
    alongside this coverage matrix: a high-risk complaint pattern, by
    itself, with otherwise normal vitals, must make is_safe_to_present
    False -- not just append a string to data_quality_flags that nothing
    downstream consults.
    """

    @pytest.mark.parametrize(
        "case", HIGH_RISK_INDICATOR_CASES, ids=lambda c: c.indicator
    )
    def test_high_risk_pattern_alone_with_normal_vitals_is_unsafe_to_present(
        self, case: HighRiskIndicatorCase
    ):
        t = _make_normal_vitals_input(case.chiefcomplaint)
        result = run_safety_review(t)
        assert result.is_safe_to_present is False, (
            f"[{case.indicator}] complaint {case.chiefcomplaint!r} with normal "
            f"vitals returned is_safe_to_present=True -- a high-risk complaint "
            f"must be flagged as unsafe-to-present-as-routine regardless of "
            f"what the recorded vitals show."
        )
        assert result.high_risk_complaint_detected is True

    def test_a_genuinely_low_risk_complaint_with_normal_vitals_is_safe_to_present(self):
        """
        Confirms the fix is targeted, not an accidental tightening that
        makes everything unsafe -- a clearly low-risk complaint with
        normal vitals must still be safe to present.
        """
        t = _make_normal_vitals_input("twisted ankle")
        result = run_safety_review(t)
        assert result.is_safe_to_present is True
        assert result.high_risk_complaint_detected is False

    def test_downstream_workflow_action_is_escalation_required_for_cardiac_arrest(self):
        """
        End-to-end confirmation through the real orchestrator, not just
        the safety review agent in isolation: a cardiac-arrest complaint
        with normal-looking vitals must produce ESCALATION_REQUIRED as
        the workflow_action, the most prominent label in the assessment
        card -- this is the literal user-facing consequence of the bug
        that was found and fixed.
        """
        from app.agents.orchestrator import run_workflow
        from app.schemas.internal import EDStaySource, EDTriageCase, TriageSource

        case = EDTriageCase(
            source_dataset="Kaggle-KTAS", subject_id=1, stay_id=1,
            edstay=EDStaySource(subject_id=1, stay_id=1, gender="F"),
            triage=TriageSource(
                subject_id=1, stay_id=1, chiefcomplaint="cardiac arrest witnessed",
                temperature=36.7, temperature_unit="C", heartrate=80.0,
                resprate=16.0, o2sat=98.0, sbp=120.0, dbp=80.0, pain="5",
            ),
        )
        result = run_workflow(case, include_llm_explanation=False)
        assert result.safety_review.is_safe_to_present is False
        assert result.workflow_action == "ESCALATION_REQUIRED", (
            f"Expected ESCALATION_REQUIRED for a cardiac-arrest complaint, got "
            f"{result.workflow_action!r} -- this is the exact bug this test "
            f"exists to catch: the assessment card's most prominent label "
            f"rendering as calm for one of the most dangerous complaint "
            f"categories the system recognises."
        )
