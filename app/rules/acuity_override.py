"""
Deterministic vital-sign override for the ML-predicted acuity.

DESIGN (Dylan's decision):
  The ML acuity model is the MAIN predictor. This module is a small, hard,
  deterministic SAFETY FLOOR layered on top of it for extreme/critical vitals.
  It is ESCALATE-ONLY: it can pull a case to a MORE urgent category, never to a
  less urgent one. The ML prediction stands unless a vital forces a higher floor.

Two tiers:
  EXTREME   -> floor = Immediate (Red), priority 1   (peri-arrest / immediately
              life-threatening ranges)
  CRITICAL  -> floor = Very Urgent (Orange), priority 2  (the engine's existing
              critical thresholds)

Final category = the MORE urgent of (ML prediction, floor). Lower priority
number = more urgent.

THRESHOLD PROVENANCE: the CRITICAL tier mirrors the engine's existing critical
vital thresholds. The EXTREME tier uses tighter standard physiological
peri-arrest ranges. Both are PROVISIONAL_STANDARD_PHYSIOLOGY (standard
physiological values, not from the licensed official MTS). See
RULESET_PROVENANCE.md. Not clinically approved; clinician review required.
"""
from __future__ import annotations

from typing import Optional, List, Tuple, Dict, Any

from app.schemas.internal import TriageTimeInput
from app.rules.discriminators import evaluate_vital_discriminators
from app.rules.acuity_mts_mapping import map_acuity_to_mts

OVERRIDE_RULE_VERSION = "acuity_override_v1"

# Priority of the two override floors (lower = more urgent).
_RED_PRIORITY = 1
_ORANGE_PRIORITY = 2


def _extreme_flags(t: TriageTimeInput) -> List[str]:
    return [item["code"] for item in evaluate_vital_discriminators(t)
            if item["priority"] == 1 and item["status"] == "matched"]


def _critical_flags(t: TriageTimeInput) -> List[str]:
    return [item["code"] for item in evaluate_vital_discriminators(t)
            if item["priority"] == 2 and item["status"] == "matched"]


def apply_acuity_override(
    predicted_acuity: Optional[int],
    triage_input: TriageTimeInput,
) -> Dict[str, Any]:
    """
    Given the ML-predicted acuity and the triage vitals, return the final
    displayed acuity/category after applying the escalate-only deterministic
    floor.

    Returns a dict:
      final_acuity, final_mts (mapping dict or None),
      override_applied (bool), override_tier ("EXTREME"|"CRITICAL"|None),
      override_flags (list), override_note (str), override_rule_version
    """
    evaluated = evaluate_vital_discriminators(triage_input)
    extreme = [item["code"] for item in evaluated
               if item["priority"] == 1 and item["status"] == "matched"]
    critical = [item["code"] for item in evaluated
                if item["priority"] == 2 and item["status"] == "matched"]

    # Floor acuity from vitals (1 = Red, 2 = Orange). None if no floor.
    floor_acuity: Optional[int] = None
    tier: Optional[str] = None
    flags: List[str] = []
    if extreme:
        floor_acuity, tier, flags = 1, "EXTREME", extreme
    elif critical:
        floor_acuity, tier, flags = 2, "CRITICAL", critical

    # Escalate-only: final = the MORE urgent (smaller acuity number) of ML and floor.
    candidates = [a for a in (predicted_acuity, floor_acuity) if a is not None]
    final_acuity = min(candidates) if candidates else None

    override_applied = (
        floor_acuity is not None
        and predicted_acuity is not None
        and floor_acuity < predicted_acuity
    )
    # Also counts as "applied" if ML gave nothing but a floor exists.
    if floor_acuity is not None and predicted_acuity is None:
        override_applied = True

    if override_applied:
        note = (
            f"Deterministic {tier} vital override: {', '.join(flags)} "
            f"-> escalated to at least "
            f"{'Immediate (Red)' if floor_acuity == 1 else 'Very Urgent (Orange)'}. "
            "Escalate-only safety floor; clinician review required."
        )
    elif tier is not None:
        note = (
            f"{tier} vital flag(s) present ({', '.join(flags)}) but the ML "
            "prediction was already at least as urgent; no escalation needed."
        )
    else:
        note = "No extreme/critical vital override triggered."

    return {
        "evaluated_discriminators": evaluated,
        "final_acuity": final_acuity,
        "final_mts": map_acuity_to_mts(final_acuity),
        "override_applied": override_applied,
        "override_tier": tier if (override_applied or tier) else None,
        "override_flags": flags,
        "override_note": note,
        "override_rule_version": OVERRIDE_RULE_VERSION,
    }
