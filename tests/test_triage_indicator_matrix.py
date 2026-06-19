"""
Triage Indicator Test Matrix.

Per the boss's request: "populate test cases for each of the triage
indicators and check the output logs." This extends
scripts/run_synthetic_walkthrough.py's 8 high-level status scenarios with
one row per INDIVIDUAL triage indicator: each vital-sign threshold
direction separately, and each individual complaint pathway separately.

HOW EVERY EXPECTED VALUE IN THIS FILE WAS DERIVED
====================================================
Every (status, reason_codes) pair asserted below was obtained by directly
calling app.rules.manchester_engine.run_manchester_engine() and reading
its real output -- not copied from documentation, not assumed by
analogy between similar-looking pathways, and not taken from any
secondhand description of the engine's behaviour. Two real subtleties
were found this way that a guess would likely have gotten wrong:

  1. When no approved ruleset is registered, the pathway functions
     (_pathway_chest_pain, _pathway_cardiac_arrest, etc.) are NEVER
     called. run_manchester_engine() builds the gated reason code itself,
     generically, as f"PATHWAY_MATCHED_{fn.__name__.replace('_pathway_',
     '').upper()}" -- so every pathway's gated reason code has the exact
     same shape, derived from the Python function name, not from
     anything inside the pathway function.

  2. An UNRECOGNISED complaint with a concern-level vital produces
     AWAITING_APPROVED_CLINICAL_RULESET (codes: UNRECOGNISED_COMPLAINT +
     the concern flag(s) + MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET).
     A MATCHED pathway with a concern-level vital instead produces
     PHYSIOLOGY_CONCERN_FLAGGED (codes: PATHWAY_MATCHED_X + the concern
     flag(s), with NO disabled-ruleset suffix). These are genuinely
     different branches in run_manchester_engine() with different
     output shapes -- not the same behaviour with different labels.

Every pathway-match row below uses a chief complaint with otherwise
CLEAN vitals, specifically to isolate "pathway detected, no category
assigned because no ruleset is configured" from any vital-sign
interaction, per the explicit instruction that gated-pathway rows must
say exactly that plainly, with nothing else mixed in.

THIS FILE TESTS THE GATED (no-ruleset) BEHAVIOUR ONLY, which is the
correct default state for this research prototype -- there is no
clinician-approved Manchester ruleset registered anywhere in this
project. It does not test the ruleset-registered branch.

Run with:
  python -m pytest tests/test_triage_indicator_matrix.py -v
  python scripts/run_triage_indicator_matrix.py   (prints a readable table)
"""
from __future__ import annotations

from typing import NamedTuple, Optional

import pytest

from app.schemas.internal import TriageTimeInput
from app.rules.manchester_engine import run_manchester_engine


def _make_input(**overrides) -> TriageTimeInput:
    # The MTS engine now applies only to MIMIC-IV-ED Demo cases (KTAS cases are
    # gated out of MTS entirely). These indicator-matrix cases exist to test the
    # engine's vital/pathway logic, so they use the MIMIC dataset. Individual
    # tests can override source_dataset to assert the KTAS gate.
    defaults = dict(
        subject_id=1, stay_id=1, source_dataset="MIMIC-IV-ED-Demo-v2.2",
        chiefcomplaint="unrecognised complaint xyz",
        heartrate=80.0, resprate=16.0, o2sat=98.0, sbp=120.0, dbp=78.0,
        temperature=36.7, temperature_unit="C", pain="2",
    )
    defaults.update(overrides)
    return TriageTimeInput(**defaults)


class IndicatorTestCase(NamedTuple):
    indicator: str
    triage_input_overrides: dict
    expected_status: str
    expected_reason_codes_subset: list[str]
    note: str


# ── Individual vital-sign threshold indicators ────────────────────────────
# Each row isolates ONE vital at ONE threshold direction, all other vitals
# left clean and the chief complaint left unrecognised (so no pathway
# interaction occurs). Values and expected output verified by direct
# execution against the real engine -- see module docstring.

VITAL_INDICATOR_CASES: list[IndicatorTestCase] = [
    IndicatorTestCase(
        "Critical SpO2 low", {"o2sat": 85.0}, "CRITICAL_PHYSIOLOGY_FLAGGED",
        ["CRITICAL_PHYSIOLOGY_DETECTED", "CRITICAL_HYPOXIA_SPO2_BELOW_90"],
        "SpO2 below 90 is always critical regardless of ruleset status.",
    ),
    IndicatorTestCase(
        "Concern SpO2 low", {"o2sat": 92.0}, "AWAITING_APPROVED_CLINICAL_RULESET",
        ["UNRECOGNISED_COMPLAINT", "CONCERN_SPO2_90_TO_94", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Concern-band SpO2 (90-94) with an unrecognised complaint.",
    ),
    IndicatorTestCase(
        "Critical respiratory rate high", {"resprate": 32.0}, "CRITICAL_PHYSIOLOGY_FLAGGED",
        ["CRITICAL_PHYSIOLOGY_DETECTED", "CRITICAL_RESPIRATORY_RATE_ABOVE_29"],
        "Resprate above 29 is critical.",
    ),
    IndicatorTestCase(
        "Critical respiratory rate low", {"resprate": 5.0}, "CRITICAL_PHYSIOLOGY_FLAGGED",
        ["CRITICAL_PHYSIOLOGY_DETECTED", "CRITICAL_RESPIRATORY_RATE_BELOW_8"],
        "Resprate below 8 is critical.",
    ),
    IndicatorTestCase(
        "Concern respiratory rate high", {"resprate": 27.0}, "AWAITING_APPROVED_CLINICAL_RULESET",
        ["UNRECOGNISED_COMPLAINT", "CONCERN_RESPIRATORY_RATE_25_TO_29", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Concern-band resprate (25-29). NOTE: engine has no low-side concern band for resprate, only the critical-low cutoff at 8.",
    ),
    IndicatorTestCase(
        "Critical heart rate high", {"heartrate": 140.0}, "CRITICAL_PHYSIOLOGY_FLAGGED",
        ["CRITICAL_PHYSIOLOGY_DETECTED", "CRITICAL_HEART_RATE_ABOVE_130"],
        "Heart rate above 130 is critical.",
    ),
    IndicatorTestCase(
        "Critical heart rate low", {"heartrate": 35.0}, "CRITICAL_PHYSIOLOGY_FLAGGED",
        ["CRITICAL_PHYSIOLOGY_DETECTED", "CRITICAL_HEART_RATE_BELOW_40"],
        "Heart rate below 40 is critical.",
    ),
    IndicatorTestCase(
        "Concern heart rate high", {"heartrate": 115.0}, "AWAITING_APPROVED_CLINICAL_RULESET",
        ["UNRECOGNISED_COMPLAINT", "CONCERN_HEART_RATE_101_TO_130", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Concern-band heart rate (101-130). NOTE: engine has no low-side concern band for heart rate, only the critical-low cutoff at 40.",
    ),
    IndicatorTestCase(
        "Critical systolic BP low", {"sbp": 80.0}, "CRITICAL_PHYSIOLOGY_FLAGGED",
        ["CRITICAL_PHYSIOLOGY_DETECTED", "CRITICAL_HYPOTENSION_SBP_BELOW_90"],
        "SBP below 90 is critical.",
    ),
    IndicatorTestCase(
        "Critical systolic BP high", {"sbp": 230.0}, "CRITICAL_PHYSIOLOGY_FLAGGED",
        ["CRITICAL_PHYSIOLOGY_DETECTED", "CRITICAL_HYPERTENSION_SBP_ABOVE_220"],
        "SBP above 220 is critical.",
    ),
    IndicatorTestCase(
        "Concern systolic BP low", {"sbp": 95.0}, "AWAITING_APPROVED_CLINICAL_RULESET",
        ["UNRECOGNISED_COMPLAINT", "CONCERN_SBP_90_TO_99", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Concern-band SBP (90-99). NOTE: engine has no high-side concern band for SBP, only the critical-high cutoff at 220.",
    ),
    IndicatorTestCase(
        "Critical temperature high", {"temperature": 41.5}, "CRITICAL_PHYSIOLOGY_FLAGGED",
        ["CRITICAL_PHYSIOLOGY_DETECTED", "CRITICAL_HYPERPYREXIA_TEMP_ABOVE_41C"],
        "Temperature >= 41C is critical.",
    ),
    IndicatorTestCase(
        "Critical temperature low", {"temperature": 34.0}, "CRITICAL_PHYSIOLOGY_FLAGGED",
        ["CRITICAL_PHYSIOLOGY_DETECTED", "CRITICAL_HYPOTHERMIA_TEMP_BELOW_35C"],
        "Temperature < 35C is critical.",
    ),
    IndicatorTestCase(
        "Concern high fever", {"temperature": 40.0}, "AWAITING_APPROVED_CLINICAL_RULESET",
        ["UNRECOGNISED_COMPLAINT", "CONCERN_HIGH_FEVER_39_5_TO_41C", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Concern-band fever (39.5-41C). NOTE: engine has no low-temperature concern band, only the critical-low cutoff at 35C.",
    ),
]

# ── Missing-data indicators ───────────────────────────────────────────────

MISSING_DATA_INDICATOR_CASES: list[IndicatorTestCase] = [
    IndicatorTestCase(
        "Missing chief complaint", {"chiefcomplaint": None}, "REQUIRES_CLINICIAN_REVIEW",
        ["MISSING_CHIEF_COMPLAINT"],
        "No pathway can be matched without a chief complaint.",
    ),
    IndicatorTestCase(
        "Missing oxygen saturation", {"o2sat": None}, "AWAITING_APPROVED_CLINICAL_RULESET",
        ["UNRECOGNISED_COMPLAINT", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "A missing (not low) o2sat does not itself trigger a critical/concern "
        "vital flag in the rules engine -- missingness is the Data Validation "
        "Agent's concern (MISSING_CRITICAL_VITAL:o2sat in safety_review), not "
        "the Manchester engine's. This row's chief complaint is also "
        "unrecognised, so AWAITING_APPROVED_CLINICAL_RULESET is the engine's "
        "output; the missing-vital flag itself is asserted separately below "
        "against the Data Validation Agent / Safety Review Agent output.",
    ),
    IndicatorTestCase(
        "Missing multiple vitals", {"temperature": None, "heartrate": None,
                                     "resprate": None, "o2sat": None, "sbp": None},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["UNRECOGNISED_COMPLAINT", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Same reasoning as above -- the rules engine itself does not flag "
        "missingness; see TestMissingVitalsSurfacedBySafetyReview below for "
        "where multiple missing vitals are genuinely surfaced.",
    ),
]

# ── Individual complaint pathway indicators ───────────────────────────────
# Each row uses a complaint matching exactly one pathway's keyword list,
# with otherwise clean vitals, so the row isolates "this pathway was
# detected" with no vital-sign interaction. All 11 real pathways from
# app.rules.manchester_engine._PATHWAYS are covered (verified to be all
# 11 by reading _PATHWAYS directly, not assumed).

PATHWAY_INDICATOR_CASES: list[IndicatorTestCase] = [
    IndicatorTestCase(
        "Cardiac arrest pathway match", {"chiefcomplaint": "cardiac arrest witnessed"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_CARDIAC_ARREST", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Anaphylaxis pathway match", {"chiefcomplaint": "anaphylaxis, throat closing"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_ANAPHYLAXIS", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Chest pain pathway match", {"chiefcomplaint": "chest pain radiating to jaw"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_CHEST_PAIN", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Shortness of breath (dyspnoea) pathway match", {"chiefcomplaint": "shortness of breath"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_DYSPNOEA", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Stroke/neuro pathway match", {"chiefcomplaint": "facial droop, slurred speech"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_STROKE", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Trauma pathway match", {"chiefcomplaint": "fall from height, laceration"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_TRAUMA", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Fever pathway match", {"chiefcomplaint": "fever and chills"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_FEVER", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Abdominal pain pathway match", {"chiefcomplaint": "abdominal pain, rlq"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_ABDOMINAL", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Altered mental status pathway match", {"chiefcomplaint": "confused and disoriented"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_ALTERED_CONSCIOUSNESS", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Overdose/poisoning pathway match", {"chiefcomplaint": "overdose, intentional ingestion"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_OVERDOSE", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured. "
        "NOTE: the engine's keyword list matches overdose/poisoning terms specifically "
        "('overdose', 'od', 'poisoning', 'ingestion', 'swallowed', 'toxic', "
        "'intoxication') -- it does not have a separate self-harm-specific pathway.",
    ),
    IndicatorTestCase(
        "Generic pain pathway match", {"chiefcomplaint": "generalised pain", "pain": "5"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["PATHWAY_MATCHED_PAIN_GENERIC", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "Pathway detected; no Manchester category assigned because no approved ruleset is configured.",
    ),
    IndicatorTestCase(
        "Unrecognised complaint", {"chiefcomplaint": "vague tiredness, unspecified"},
        "AWAITING_APPROVED_CLINICAL_RULESET",
        ["UNRECOGNISED_COMPLAINT", "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        "No pathway keyword matched. Different reason code shape from a "
        "matched-but-gated pathway (UNRECOGNISED_COMPLAINT, not "
        "PATHWAY_MATCHED_X), but the same AWAITING_APPROVED_CLINICAL_RULESET "
        "status and the same 'no category without a ruleset' guarantee.",
    ),
    IndicatorTestCase(
        "Matched pathway + concern vital interaction", {"chiefcomplaint": "chest pain", "o2sat": 92.0},
        "PHYSIOLOGY_CONCERN_FLAGGED",
        ["PATHWAY_MATCHED_CHEST_PAIN", "CONCERN_SPO2_90_TO_94"],
        "Genuinely distinct dispatch branch from every other pathway-match "
        "row above: when a pathway IS matched and a concern-level vital IS "
        "also present, the engine takes the _safety_alert(severity='CONCERN') "
        "branch instead of _awaiting_ruleset(), producing "
        "PHYSIOLOGY_CONCERN_FLAGGED with NO "
        "MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET suffix at all -- contrast "
        "this with the 'Concern SpO2 low' row above, where the SAME concern "
        "vital with an UNRECOGNISED complaint instead produces "
        "AWAITING_APPROVED_CLINICAL_RULESET WITH that suffix. Same vital "
        "value, two different statuses, depending purely on whether the "
        "complaint matched a pathway keyword. This row makes that "
        "documented-but-previously-unchecked interaction an automatically "
        "verified part of the matrix.",
    ),
]

ALL_INDICATOR_CASES = (
    VITAL_INDICATOR_CASES + MISSING_DATA_INDICATOR_CASES + PATHWAY_INDICATOR_CASES
)


class TestTriageIndicatorMatrix:
    @pytest.mark.parametrize(
        "case", ALL_INDICATOR_CASES, ids=[c.indicator for c in ALL_INDICATOR_CASES]
    )
    def test_indicator_produces_expected_status_and_reason_codes(self, case: IndicatorTestCase):
        triage_input = _make_input(**case.triage_input_overrides)
        decision = run_manchester_engine(triage_input)

        assert decision.classification_status == case.expected_status, (
            f"[{case.indicator}] expected status {case.expected_status!r}, "
            f"got {decision.classification_status!r}. {case.note}"
        )
        for expected_code in case.expected_reason_codes_subset:
            assert expected_code in decision.reason_codes, (
                f"[{case.indicator}] expected reason code {expected_code!r} "
                f"in {decision.reason_codes}. {case.note}"
            )

    @pytest.mark.parametrize(
        "case", PATHWAY_INDICATOR_CASES, ids=[c.indicator for c in PATHWAY_INDICATOR_CASES]
    )
    def test_pathway_match_never_assigns_category_without_ruleset(self, case: IndicatorTestCase):
        """
        Direct, explicit test of the safety property this whole matrix
        exists to verify: a complaint pathway being recognised must NEVER
        by itself cause a Manchester category to be assigned while no
        approved ruleset is registered.
        """
        triage_input = _make_input(**case.triage_input_overrides)
        decision = run_manchester_engine(triage_input)
        assert decision.category is None, (
            f"[{case.indicator}] pathway match assigned category "
            f"{decision.category!r} with no approved ruleset registered. "
            f"This must never happen."
        )
        assert decision.requires_clinician_review is True


class TestMissingVitalsSurfacedBySafetyReview:
    """
    Missing vitals are NOT flagged by the Manchester engine itself (a
    missing o2sat is simply None, not a critical/concern value) -- they
    are surfaced by the Data Validation Agent and Safety Review Agent
    instead, via the full orchestrator. These tests confirm that
    surfacing genuinely happens, completing the "Missing oxygen
    saturation" and "Missing multiple vitals" indicators from a different
    angle than the Manchester-engine-only tests above.
    """

    def _make_case(self, **triage_overrides):
        from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource
        defaults = dict(
            subject_id=1, stay_id=1, chiefcomplaint="unrecognised complaint xyz",
            heartrate=80.0, resprate=16.0, o2sat=98.0, sbp=120.0, dbp=78.0,
            temperature=36.7, temperature_unit="C", pain="2",
        )
        defaults.update(triage_overrides)
        return EDTriageCase(
            stay_id=1, subject_id=1, source_dataset="Kaggle-KTAS",
            edstay=EDStaySource(subject_id=1, stay_id=1, gender="F", arrival_transport="Walk-in"),
            triage=TriageSource(**defaults),
        )

    def test_missing_o2sat_surfaced_by_safety_review(self):
        from app.agents.orchestrator import run_workflow
        case = self._make_case(o2sat=None)
        result = run_workflow(case, include_llm_explanation=False)
        assert "MISSING_CRITICAL_VITAL:o2sat" in result.safety_review.data_quality_flags
        assert "o2sat" in result.data_validation.missing_required_fields

    def test_missing_multiple_vitals_surfaced_by_safety_review(self):
        from app.agents.orchestrator import run_workflow
        case = self._make_case(temperature=None, heartrate=None, resprate=None,
                                o2sat=None, sbp=None)
        result = run_workflow(case, include_llm_explanation=False)
        for field in ["temperature", "heartrate", "resprate", "o2sat", "sbp"]:
            assert f"MISSING_CRITICAL_VITAL:{field}" in result.safety_review.data_quality_flags
        assert result.safety_review.is_safe_to_present is False
        assert result.data_validation.validation_status == "NEEDS_HUMAN_DATA_REVIEW"


def _print_matrix() -> None:
    """Prints a readable Indicator | Test case | Expected | Actual | Pass/Fail table."""
    print(f"{'Indicator':45s} {'Expected Status':32s} {'Actual Status':32s} {'Pass/Fail'}")
    print("-" * 125)
    all_pass = True
    for case in ALL_INDICATOR_CASES:
        triage_input = _make_input(**case.triage_input_overrides)
        decision = run_manchester_engine(triage_input)
        status_ok = decision.classification_status == case.expected_status
        codes_ok = all(c in decision.reason_codes for c in case.expected_reason_codes_subset)
        passed = status_ok and codes_ok
        all_pass = all_pass and passed
        print(
            f"{case.indicator:45s} {case.expected_status:32s} "
            f"{decision.classification_status:32s} {'PASS' if passed else 'FAIL'}"
        )
        if not passed:
            print(f"    expected codes (subset): {case.expected_reason_codes_subset}")
            print(f"    actual codes:             {decision.reason_codes}")
    print("-" * 125)
    print(f"TOTAL: {len(ALL_INDICATOR_CASES)} indicators, ALL PASS: {all_pass}")


if __name__ == "__main__":
    _print_matrix()
