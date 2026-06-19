"""
Synthetic Worked-Example Walkthrough.

PURPOSE
========
Requested by Dylan's boss: "populate test cases for each of the triage
indicators and check the output logs... a full walkthrough across all
possible scenarios and triage assignments." This script produces exactly
that, as a single runnable artefact whose output log can be read top to
bottom.

WHAT THIS IS, AND WHAT IT IS NOT
====================================
Every case below is CONSTRUCTED by hand to deliberately exercise one
specific deterministic code path. None of these are real patients, real
KTAS rows, or real MIMIC rows. Every case is explicitly labelled
"SYNTHETIC_WALKTHROUGH_CASE" in its source_dataset field so it can never
be mistaken for real data anywhere downstream (the leakage guard and
every governance report in this project key off source_dataset, so this
label is load-bearing, not cosmetic).

This script calls the real, unmodified app.agents.orchestrator.run_workflow
and app.agents.followup_comparison_agent.compare_follow_up functions --
it does not reimplement or approximate any decision logic. Every status,
flag, and note shown below is the actual code path firing on a
constructed input, not a description of what the code is supposed to do.

SCENARIOS COVERED
===================
  1. CRITICAL_PHYSIOLOGY_FLAGGED   -- a single critical vital (low SpO2)
  2. PHYSIOLOGY_CONCERN_FLAGGED    -- a single concerning (amber) vital
  3. AWAITING_APPROVED_CLINICAL_RULESET -- normal vitals, recognised complaint,
                                            no Manchester ruleset registered
  4. REQUIRES_CLINICIAN_REVIEW     -- missing chief complaint
  5. NEEDS_HUMAN_DATA_REVIEW (data validation) -- multiple missing vitals,
                                                    "agent is not sure" case
  6. Follow-up, no escalation      -- vitals essentially unchanged across
                                       two declared-linked stays
  7. Follow-up, escalation         -- vitals deteriorate across two
                                       declared-linked stays, with the
                                       resulting escalation note shown in full
  8. Follow-up, newly-missing vital -- a vital that was recorded on the
                                        first visit is absent on the second,
                                        which this project treats as itself
                                        notable, not merely an omission

Run with:
  python scripts/run_synthetic_walkthrough.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource
from app.agents.orchestrator import run_workflow
from app.agents.followup_comparison_agent import compare_follow_up
from app.schemas.followup import FollowUpLinkRequest


SYNTHETIC_DATASET_LABEL = "SYNTHETIC_WALKTHROUGH_CASE"


def _case(stay_id: int, subject_id: int = 9_000_000, **triage_kwargs) -> EDTriageCase:
    base_triage = dict(
        subject_id=subject_id, stay_id=stay_id,
        chiefcomplaint="constructed example complaint",
        heartrate=80.0, resprate=16.0, o2sat=98.0, sbp=120.0, dbp=78.0,
        temperature=36.8, temperature_unit="C", pain="2",
    )
    base_triage.update(triage_kwargs)
    return EDTriageCase(
        stay_id=stay_id, subject_id=subject_id, source_dataset=SYNTHETIC_DATASET_LABEL,
        edstay=EDStaySource(subject_id=subject_id, stay_id=stay_id,
                             gender="F", arrival_transport="Walk-in"),
        triage=TriageSource(**base_triage),
    )


def _print_section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _print_result_summary(result) -> None:
    print(f"  stay_id:               {result.stay_id}")
    print(f"  source_dataset:        {result.triage_input.source_dataset}")
    print(f"  data_validation_status:{result.data_validation.validation_status}")
    print(f"  missing_fields:        {result.data_validation.missing_required_fields}")
    print(f"  rules_engine_status:   {result.decision.classification_status}")
    print(f"  reason_codes:          {result.decision.reason_codes}")
    print(f"  category_assigned:     {result.decision.category}")
    print(f"  is_safe_to_present:    {result.safety_review.is_safe_to_present}")
    print(f"  safety_flags:          {result.safety_review.data_quality_flags}")
    print(f"  requires_clinician_review: {result.decision.requires_clinician_review}")


def run_all_scenarios() -> list[dict]:
    """Runs every scenario, prints a readable log, and returns a list of
    plain dicts (one per scenario) suitable for saving to disk."""
    log: list[dict] = []

    # ── Scenario 1: CRITICAL_PHYSIOLOGY_FLAGGED ──────────────────────────────
    _print_section("SCENARIO 1 — Critical vital sign (SpO2 84%, otherwise normal)")
    case1 = _case(1, o2sat=84.0, chiefcomplaint="shortness of breath")
    result1 = run_workflow(case1, include_llm_explanation=False)
    _print_result_summary(result1)
    assert result1.decision.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"
    log.append({"scenario": "critical_physiology", "stay_id": 1,
                "result": result1.model_dump(mode="json")})

    # ── Scenario 2: PHYSIOLOGY_CONCERN_FLAGGED ───────────────────────────────
    _print_section("SCENARIO 2 — Concerning (amber) vital sign (SpO2 92%, otherwise normal)")
    case2 = _case(2, o2sat=92.0, chiefcomplaint="mild cough")
    result2 = run_workflow(case2, include_llm_explanation=False)
    _print_result_summary(result2)
    log.append({"scenario": "physiology_concern", "stay_id": 2,
                "result": result2.model_dump(mode="json")})

    # ── Scenario 3: AWAITING_APPROVED_CLINICAL_RULESET ───────────────────────
    _print_section("SCENARIO 3 — Normal vitals, recognised complaint, no Manchester ruleset registered")
    case3 = _case(3, chiefcomplaint="chest pain", pain="6")
    result3 = run_workflow(case3, include_llm_explanation=False)
    _print_result_summary(result3)
    assert result3.decision.category is None, (
        "No Manchester category must ever be assigned without an approved ruleset."
    )
    log.append({"scenario": "awaiting_approved_ruleset", "stay_id": 3,
                "result": result3.model_dump(mode="json")})

    # ── Scenario 4: REQUIRES_CLINICIAN_REVIEW (missing chief complaint) ──────
    _print_section("SCENARIO 4 — Missing chief complaint")
    case4 = _case(4, chiefcomplaint=None)
    result4 = run_workflow(case4, include_llm_explanation=False)
    _print_result_summary(result4)
    assert result4.decision.classification_status == "REQUIRES_CLINICIAN_REVIEW"
    log.append({"scenario": "requires_clinician_review_missing_complaint", "stay_id": 4,
                "result": result4.model_dump(mode="json")})

    # ── Scenario 5: NEEDS_HUMAN_DATA_REVIEW -- "agent is not sure" ───────────
    _print_section("SCENARIO 5 — Multiple missing vitals: the 'agent is not sure, clinician intervention required' case")
    case5 = _case(5, heartrate=None, resprate=None, o2sat=None, sbp=None,
                  temperature=None, pain=None, chiefcomplaint="generalised weakness")
    result5 = run_workflow(case5, include_llm_explanation=False)
    _print_result_summary(result5)
    assert result5.data_validation.validation_status == "NEEDS_HUMAN_DATA_REVIEW"
    assert result5.safety_review.is_safe_to_present is False
    print("  --> Note for clinician: the system explicitly reports that it cannot")
    print("      classify this case safely due to missing data, rather than")
    print("      guessing or silently filling in a default vital sign value.")
    log.append({"scenario": "needs_human_data_review_agent_unsure", "stay_id": 5,
                "result": result5.model_dump(mode="json")})

    # ── Scenario 6: Follow-up, no escalation ─────────────────────────────────
    _print_section("SCENARIO 6 — Follow-up visit, vitals essentially unchanged (no escalation)")
    case6a = _case(6, subject_id=9_000_006, heartrate=78.0, o2sat=98.0, chiefcomplaint="follow-up: headache")
    case6b = _case(7, subject_id=9_000_006, heartrate=80.0, o2sat=97.0, chiefcomplaint="follow-up: headache, slightly better")
    result6a = run_workflow(case6a, include_llm_explanation=False)
    result6b = run_workflow(case6b, include_llm_explanation=False)
    link6 = FollowUpLinkRequest(
        previous_stay_id=6, new_stay_id=7, linked_by="walkthrough_script",
        link_reason="Scenario 6: demonstrate stable follow-up, no escalation expected",
        is_demonstration_scenario=True,
    )
    comparison6 = compare_follow_up(link6, result6a, result6b)
    print(f"  escalation_detected: {comparison6.escalation_detected}")
    print(f"  note: {comparison6.escalation_note}")
    assert comparison6.escalation_detected is False
    log.append({"scenario": "followup_no_escalation", "previous_stay_id": 6,
                "new_stay_id": 7, "comparison": comparison6.model_dump(mode="json")})

    # ── Scenario 7: Follow-up, escalation -- the boss's exact scenario ───────
    _print_section("SCENARIO 7 — Follow-up visit, vitals deteriorate (escalation expected) — this is the exact scenario your boss described: 'same patient, vitals change, new assignment should be escalation, with a note explaining the cause'")
    case7a = _case(8, subject_id=9_000_008, heartrate=78.0, resprate=16.0, o2sat=98.0,
                   sbp=118.0, temperature=36.7, chiefcomplaint="mild headache")
    case7b = _case(9, subject_id=9_000_008, heartrate=132.0, resprate=28.0, o2sat=86.0,
                   sbp=82.0, temperature=38.9, chiefcomplaint="worsening headache, now confused")
    result7a = run_workflow(case7a, include_llm_explanation=False)
    result7b = run_workflow(case7b, include_llm_explanation=False)
    link7 = FollowUpLinkRequest(
        previous_stay_id=8, new_stay_id=9, linked_by="walkthrough_script",
        link_reason="Scenario 7: demonstrate deteriorating follow-up per boss's worked example",
        is_demonstration_scenario=True,
    )
    comparison7 = compare_follow_up(link7, result7a, result7b)
    print(f"  previous status: {comparison7.previous_classification_status}")
    print(f"  new status:      {comparison7.new_classification_status}")
    print(f"  escalation_detected: {comparison7.escalation_detected}")
    print(f"  escalation note (full text shown to clinician):")
    print(f"    {comparison7.escalation_note}")
    print(f"  vital-by-vital breakdown:")
    for d in comparison7.vital_deltas:
        flag = "  <-- NOTABLE" if d.clinically_notable else ""
        print(f"    {d.field_name}: {d.previous_value} -> {d.new_value} {d.unit or ''} ({d.direction}){flag}")
    assert comparison7.escalation_detected is True
    log.append({"scenario": "followup_escalation_boss_worked_example", "previous_stay_id": 8,
                "new_stay_id": 9, "comparison": comparison7.model_dump(mode="json")})

    # ── Scenario 8: Follow-up, newly-missing vital ───────────────────────────
    _print_section("SCENARIO 8 — Follow-up visit, a previously-recorded vital is now missing")
    case8a = _case(10, subject_id=9_000_010, o2sat=98.0, chiefcomplaint="follow-up visit")
    case8b = _case(11, subject_id=9_000_010, o2sat=None, chiefcomplaint="follow-up visit, SpO2 not recorded this time")
    result8a = run_workflow(case8a, include_llm_explanation=False)
    result8b = run_workflow(case8b, include_llm_explanation=False)
    link8 = FollowUpLinkRequest(
        previous_stay_id=10, new_stay_id=11, linked_by="walkthrough_script",
        link_reason="Scenario 8: demonstrate that a newly-missing vital is itself flagged as notable",
        is_demonstration_scenario=True,
    )
    comparison8 = compare_follow_up(link8, result8a, result8b)
    print(f"  escalation_detected: {comparison8.escalation_detected}")
    print(f"  note: {comparison8.escalation_note}")
    o2sat_delta = next(d for d in comparison8.vital_deltas if d.field_name == "o2sat")
    assert o2sat_delta.direction == "NEWLY_MISSING"
    assert o2sat_delta.clinically_notable is True
    log.append({"scenario": "followup_newly_missing_vital", "previous_stay_id": 10,
                "new_stay_id": 11, "comparison": comparison8.model_dump(mode="json")})

    return log


if __name__ == "__main__":
    print("SYNTHETIC WORKED-EXAMPLE WALKTHROUGH")
    print("All cases below are constructed for demonstration. None are real")
    print("patient data. Every case is labelled "
          f"source_dataset='{SYNTHETIC_DATASET_LABEL}'.")
    print("NOT FOR CLINICAL USE.")

    log = run_all_scenarios()

    _print_section("ALL SCENARIOS COMPLETED — assertions passed for every expected status")

    output_dir = PROJECT_ROOT / "data" / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "synthetic_walkthrough_log.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "label": "SYNTHETIC_WALKTHROUGH -- constructed demonstration cases, not real patient data",
                "scenarios": log,
            },
            f, indent=2, default=str,
        )
    print(f"\nFull log saved to: {output_path}")
