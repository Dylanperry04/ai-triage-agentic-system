"""
Provisional Manchester Triage System (MTS) research ruleset.

WHAT THIS IS
============
A single, reviewable definition of the metadata that registers the heuristic
complaint pathways in `app/rules/manchester_engine.py` with the engine, plus a
machine-readable provenance table for the thresholds those pathways and the
vital-flag functions use. Registering it makes the engine assign categories
instead of staying gated at AWAITING_APPROVED_CLINICAL_RULESET.

WHAT THIS IS NOT
================
This is NOT the official Manchester Triage System. The official MTS (52
flowcharts with their full discriminator sets) is published as a licensed,
copyrighted work by the Manchester Triage Group (Wiley/BMJ, "Emergency
Triage") and is not freely reproducible. This module is a RESEARCH
APPROXIMATION assembled from:

  (a) the small number of MTS discriminators that ARE published in the open
      peer-reviewed literature, with their real cut-points, and
  (b) standard adult physiological danger thresholds (the same ones the
      engine's own _critical_vital_flags / _concern_vital_flags already used
      before this ruleset existed), standing in for the licensed discriminator
      detail that is not openly available.

Every threshold below carries an explicit `source` tag so a reviewer can see
which numbers rest on a published MTS source and which are a reasoned
physiological approximation. See RULESET_PROVENANCE.md for the full table.

REVIEW STATUS
=============
provisional_mts_research_v0 has been drafted for Dylan's personal review. It
has NOT been reviewed or approved by a clinician, by Meghana, or by any
clinical governance process. It is registered at application startup so the
research demo shows working categories, but EVERY decision it produces is
stamped provisional and carries requires_clinician_review = True. It must
never be presented as an approved or official Manchester triage result.
"""
from __future__ import annotations

from typing import Dict, Any

from app.rules import manchester_engine


PROVISIONAL_RULESET_ID = "provisional_mts_research_v0"


# Machine-readable provenance for each threshold the engine uses. This dict
# does not itself drive the engine's arithmetic (the pathway functions and the
# vital-flag functions in manchester_engine.py hold the live thresholds); it is
# the record of WHERE each threshold comes from, kept next to the registration
# call. If you change a threshold in manchester_engine.py, change its row here
# too -- tests/test_provisional_mts_ruleset.py checks the two stay consistent
# for the thresholds it can verify directly.
THRESHOLD_PROVENANCE: Dict[str, Dict[str, Any]] = {
    "spo2_very_low": {
        "engine_rule": "o2sat < 90  -> CRITICAL_HYPOXIA_SPO2_BELOW_90",
        "mts_category_intent": "Very Urgent (Orange) or higher",
        "source": "PUBLISHED_MTS_DISCRIMINATOR",
        "citation": "PLOS One 2021 doi:10.1371/journal.pone.0246324 - "
                    "'Very low SaO2' (<90% on air) -> Very urgent",
    },
    "spo2_low": {
        "engine_rule": "90 <= o2sat < 95  -> CONCERN_SPO2_90_TO_94",
        "mts_category_intent": "Urgent (Yellow)",
        "source": "PUBLISHED_MTS_DISCRIMINATOR",
        "citation": "PLOS One 2021 doi:10.1371/journal.pone.0246324 - "
                    "'Low SaO2' (<95% on air) -> Urgent",
    },
    "heartrate_critical_high": {
        "engine_rule": "heartrate > 130 -> CRITICAL_HEART_RATE_ABOVE_130",
        "mts_category_intent": "Very Urgent / Immediate-adjacent",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard adult tachycardia danger range. Related published MTS "
                    "example: HR >=120 used as a Very-urgent discriminator (PLOS One "
                    "2021). Exact >130 cut is approximation.",
    },
    "heartrate_concern_high": {
        "engine_rule": "100 < heartrate <= 130 -> CONCERN_HEART_RATE_101_TO_130",
        "mts_category_intent": "Concern (no category alone)",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard adult tachycardia range; not an official MTS cut.",
    },
    "heartrate_critical_low": {
        "engine_rule": "heartrate < 40 -> CRITICAL_HEART_RATE_BELOW_40",
        "mts_category_intent": "Very Urgent / Immediate-adjacent",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard adult bradycardia danger range; not an official MTS cut.",
    },
    "resprate_critical_high": {
        "engine_rule": "resprate > 29 -> CRITICAL_RESPIRATORY_RATE_ABOVE_29",
        "mts_category_intent": "Very Urgent / Immediate-adjacent",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard adult tachypnoea danger range; not an official MTS cut.",
    },
    "resprate_critical_low": {
        "engine_rule": "resprate < 8 -> CRITICAL_RESPIRATORY_RATE_BELOW_8",
        "mts_category_intent": "Very Urgent / Immediate-adjacent",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard adult bradypnoea danger range; not an official MTS cut.",
    },
    "resprate_concern_high": {
        "engine_rule": "25 <= resprate <= 29 -> CONCERN_RESPIRATORY_RATE_25_TO_29",
        "mts_category_intent": "Concern (no category alone)",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard adult tachypnoea range; not an official MTS cut.",
    },
    "sbp_critical_low": {
        "engine_rule": "sbp < 90 -> CRITICAL_HYPOTENSION_SBP_BELOW_90",
        "mts_category_intent": "Very Urgent / Immediate-adjacent (shock)",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard adult hypotension/shock threshold; not an official MTS cut.",
    },
    "sbp_critical_high": {
        "engine_rule": "sbp > 220 -> CRITICAL_HYPERTENSION_SBP_ABOVE_220",
        "mts_category_intent": "Very Urgent",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard adult severe-hypertension threshold; not an official MTS cut.",
    },
    "sbp_concern_low": {
        "engine_rule": "90 <= sbp < 100 -> CONCERN_SBP_90_TO_99",
        "mts_category_intent": "Concern (no category alone)",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Borderline adult hypotension; not an official MTS cut.",
    },
    "temp_critical_high": {
        "engine_rule": "temp_c >= 41.0 -> CRITICAL_HYPERPYREXIA_TEMP_ABOVE_41C",
        "mts_category_intent": "Very Urgent",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard hyperpyrexia threshold; not an official MTS cut.",
    },
    "temp_critical_low": {
        "engine_rule": "temp_c < 35.0 -> CRITICAL_HYPOTHERMIA_TEMP_BELOW_35C",
        "mts_category_intent": "Very Urgent",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard hypothermia threshold; not an official MTS cut.",
    },
    "temp_concern_high": {
        "engine_rule": "39.5 <= temp_c < 41.0 -> CONCERN_HIGH_FEVER_39_5_TO_41C",
        "mts_category_intent": "Concern / Urgent via fever pathway",
        "source": "PROVISIONAL_STANDARD_PHYSIOLOGY",
        "citation": "Standard high-fever threshold; not an official MTS cut.",
    },
    "pain_severe": {
        "engine_rule": "pain >= 7 -> severe-pain discriminator in pathways",
        "mts_category_intent": "Very Urgent or Urgent depending on pathway",
        "source": "PROVISIONAL_APPROXIMATION_OF_MTS_STRUCTURE",
        "citation": "MTS uses pain-severity general discriminators; the official 0-10 "
                    "cut-points are licensed. 7+/4-6/<4 banding is approximation.",
    },
    "complaint_pathway_routing": {
        "engine_rule": "_PATHWAYS keyword lists -> pathway function selection",
        "mts_category_intent": "Presentation flowchart selection",
        "source": "PROVISIONAL_APPROXIMATION_OF_MTS_STRUCTURE",
        "citation": "Mirrors documented MTS structure (presentation -> discriminators -> "
                    "category) but keyword lists and assignments are this project's own "
                    "heuristic, not licensed flowchart content.",
    },
}


def register_provisional_ruleset() -> None:
    """
    Register the provisional research ruleset with the Manchester engine.

    Activates the heuristic, unvalidated complaint pathways so the engine
    assigns provisional categories instead of returning
    AWAITING_APPROVED_CLINICAL_RULESET. Called at application startup so the
    research demo shows working categories by default.

    Idempotent. Every decision the engine then produces still carries
    requires_clinician_review=True and is labelled provisional throughout.

    To DISABLE provisional mode (revert to fully-gated behaviour), do not call
    this -- or call manchester_engine.clear_approved_ruleset(). One environment
    variable controls this at startup; see app/main.py.
    """
    manchester_engine.register_approved_ruleset(
        ruleset_id=PROVISIONAL_RULESET_ID,
        approved_by="UNAPPROVED - provisional research ruleset, pending Dylan's review",
        approved_date="not_approved",
        source="app/rules/provisional_mts_ruleset.py + RULESET_PROVENANCE.md "
               "(research approximation, NOT the official Manchester Triage System)",
        acknowledge_heuristic_pathways=True,
    )


def provisional_ruleset_summary() -> Dict[str, Any]:
    """Machine-readable summary for the API/UI provenance surfaces."""
    by_source: Dict[str, int] = {}
    for row in THRESHOLD_PROVENANCE.values():
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
    return {
        "ruleset_id": PROVISIONAL_RULESET_ID,
        "is_official_mts": False,
        "is_clinically_approved": False,
        "review_status": "DRAFT - pending Dylan's personal review; not clinician-approved",
        "threshold_count": len(THRESHOLD_PROVENANCE),
        "thresholds_by_source": by_source,
        "warning": (
            "Provisional research ruleset. NOT the official Manchester Triage "
            "System and NOT clinically approved. Every category requires "
            "clinician confirmation."
        ),
    }
