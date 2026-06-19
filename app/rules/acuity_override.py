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
from app.rules.vitals import temperature_c
from app.rules.acuity_mts_mapping import map_acuity_to_mts

OVERRIDE_RULE_VERSION = "acuity_override_v1"

# Priority of the two override floors (lower = more urgent).
_RED_PRIORITY = 1
_ORANGE_PRIORITY = 2


def _extreme_flags(t: TriageTimeInput) -> List[str]:
    flags: List[str] = []
    if t.o2sat is not None and t.o2sat < 85:
        flags.append("EXTREME_HYPOXIA_SPO2_BELOW_85")
    if t.heartrate is not None and t.heartrate > 150:
        flags.append("EXTREME_TACHYCARDIA_HR_ABOVE_150")
    if t.heartrate is not None and t.heartrate < 35:
        flags.append("EXTREME_BRADYCARDIA_HR_BELOW_35")
    if t.resprate is not None and t.resprate > 35:
        flags.append("EXTREME_TACHYPNOEA_RR_ABOVE_35")
    if t.resprate is not None and t.resprate < 6:
        flags.append("EXTREME_BRADYPNOEA_RR_BELOW_6")
    if t.sbp is not None and t.sbp < 80:
        flags.append("EXTREME_HYPOTENSION_SBP_BELOW_80")
    tc = temperature_c(t)
    if tc is not None and tc >= 41.5:
        flags.append("EXTREME_HYPERPYREXIA_TEMP_ABOVE_41_5C")
    if tc is not None and tc < 32.0:
        flags.append("EXTREME_HYPOTHERMIA_TEMP_BELOW_32C")
    return flags


def _critical_flags(t: TriageTimeInput) -> List[str]:
    flags: List[str] = []
    if t.o2sat is not None and t.o2sat < 90:
        flags.append("CRITICAL_HYPOXIA_SPO2_BELOW_90")
    if t.heartrate is not None and t.heartrate > 130:
        flags.append("CRITICAL_HEART_RATE_ABOVE_130")
    if t.heartrate is not None and t.heartrate < 40:
        flags.append("CRITICAL_HEART_RATE_BELOW_40")
    if t.resprate is not None and t.resprate > 29:
        flags.append("CRITICAL_RESPIRATORY_RATE_ABOVE_29")
    if t.resprate is not None and t.resprate < 8:
        flags.append("CRITICAL_RESPIRATORY_RATE_BELOW_8")
    if t.sbp is not None and t.sbp < 90:
        flags.append("CRITICAL_HYPOTENSION_SBP_BELOW_90")
    if t.sbp is not None and t.sbp > 220:
        flags.append("CRITICAL_HYPERTENSION_SBP_ABOVE_220")
    tc = temperature_c(t)
    if tc is not None and tc >= 41.0:
        flags.append("CRITICAL_HYPERPYREXIA_TEMP_ABOVE_41C")
    if tc is not None and tc < 35.0:
        flags.append("CRITICAL_HYPOTHERMIA_TEMP_BELOW_35C")
    return flags


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
    extreme = _extreme_flags(triage_input)
    critical = _critical_flags(triage_input)

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
        "final_acuity": final_acuity,
        "final_mts": map_acuity_to_mts(final_acuity),
        "override_applied": override_applied,
        "override_tier": tier if (override_applied or tier) else None,
        "override_flags": flags,
        "override_note": note,
        "override_rule_version": OVERRIDE_RULE_VERSION,
    }
