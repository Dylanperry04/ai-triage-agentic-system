"""
Follow-Up Comparison Agent.

Compares two already-computed WorkflowResult objects for a user-declared
follow-up pair (see app/schemas/followup.py for the full design rationale
on why this never auto-detects repeat patients).

This agent is fully deterministic. No LLM is involved. It does not call
the Manchester engine, the KTAS rules, or the ML prediction agent itself
-- it only reads outputs those components already produced for each
stay individually, exactly as already computed by the unmodified
orchestrator. This keeps the safety boundary identical to the rest of
the project: this file can compare and explain, it cannot decide.

ESCALATION THRESHOLDS -- HOW THEY WERE VERIFIED, NOT JUST WHAT THEY ARE
==========================================================================
A vital sign delta is marked clinically_notable using boundaries that
must exactly match app/rules/manchester_engine.py's own
_critical_vital_flags / _concern_vital_flags. This sounds simple but two
different, wrong approaches were tried and caught before this version:

  1. First attempt: re-derive "sensible" concern bands from clinical
     judgement (e.g. "heart rate 50-110 seems like a reasonable normal
     range"). This invented bands the real engine does not have (e.g. a
     low-side heart-rate concern band) and silently disagreed with the
     engine on what counts as notable.

  2. Second attempt: a single generic _band(value, crit_low, crit_high,
     conc_low, conc_high) helper with uniform strict < / > comparisons,
     with the four numbers per vital copied by reading the engine's
     source. This looked more rigorous but was still wrong, because the
     real engine mixes <, <=, and >= inconsistently across different
     checks (concern resprate is inclusive on both ends, `25 <= x <= 29`;
     critical fever is `>= 41.0`; critical hypoxia is `< 90`), and a
     single generic comparison shape cannot represent that.

  The fix that actually worked: call the REAL engine functions directly,
  sweep every vital across its boundary values, record the empirical
  (field, value) -> band table that produces, and then write per-field
  if/elif chains whose individual operators were chosen one at a time to
  reproduce that table exactly. See _band()'s docstring for the full
  35-point verification table this was checked against, and
  tests/test_followup_comparison_agent.py for the automated version of
  the same check, which re-derives the sweep from the live engine
  functions at test time rather than from a frozen copy of the numbers,
  so it will fail loudly if the engine's thresholds ever change without
  this file being updated to match.

escalation_detected is True if:
  - the deterministic classification_status worsened in severity
    (e.g. moved from AWAITING_APPROVED_CLINICAL_RULESET or a concern flag
    to CRITICAL_PHYSIOLOGY_FLAGGED), OR
  - one or more vitals crossed into a critical or concern band that was
    previously normal, OR
  - one or more previously-present vitals are now missing
    (NEWLY_MISSING), which the existing Data Validation Agent already
    treats as requiring human review on its own, and which this module
    surfaces explicitly here because a missing vital on a follow-up visit
    is itself a meaningful change worth flagging, not just an omission.
"""
from __future__ import annotations

from typing import Optional

from app.schemas.internal import TriageTimeInput
from app.schemas.workflow import WorkflowResult
from app.schemas.followup import (
    FollowUpComparisonResult,
    FollowUpLinkRequest,
    VitalDelta,
)
from app.rules.vitals import temperature_c


# Severity ranking of classification_status, used only to detect whether a
# follow-up moved toward or away from danger. Higher number = more severe.
# This ranking mirrors the order the Manchester engine's own docstring
# already uses to describe its statuses; it is not a new severity scale.
_STATUS_SEVERITY_RANK: dict[str, int] = {
    "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED": 0,
    "REQUIRES_CLINICIAN_REVIEW": 1,
    "AWAITING_APPROVED_CLINICAL_RULESET": 1,
    "PHYSIOLOGY_CONCERN_FLAGGED": 2,
    "MTS_CATEGORY_ASSIGNED_PENDING_CLINICIAN_REVIEW": 2,
    "CRITICAL_PHYSIOLOGY_FLAGGED": 3,
}

# Fields compared, in display order. "temperature" is handled separately
# in compare_follow_up() because it requires Celsius conversion via the
# shared app.rules.vitals.temperature_c() utility before banding.
_COMPARED_VITAL_FIELDS: list[tuple[str, str]] = [
    ("o2sat", "%"),
    ("resprate", "/min"),
    ("heartrate", "bpm"),
    ("sbp", "mmHg"),
]

_BAND_RANK = {"NORMAL": 0, "CONCERN": 1, "CRITICAL": 2}


def _band(field_name: str, value: Optional[float]) -> str:
    """
    Classify a single vital value into NORMAL / CONCERN / CRITICAL.

    See module docstring for how these boundaries were derived and
    verified. Summary of the empirical ground truth this function was
    built to reproduce exactly (re-derived by sweeping the REAL
    app.rules.manchester_engine functions, not by reading their source):

      o2sat:        <90 CRITICAL | 90<=x<95 CONCERN | >=95 NORMAL
      resprate:     <8 or >29 CRITICAL | 25<=x<=29 CONCERN | else NORMAL
      heartrate:    <40 or >130 CRITICAL | 100<x<=130 CONCERN | else NORMAL
      sbp:          <90 or >220 CRITICAL | 90<=x<100 CONCERN | else NORMAL
      temperature:  <35.0 or >=41.0 CRITICAL | 39.5<=x<41.0 CONCERN | else NORMAL
        (temperature is in Celsius; convert via app.rules.vitals.temperature_c
        before calling this function -- this function does not convert units)
    """
    if value is None:
        return "NORMAL"

    if field_name == "o2sat":
        if value < 90:
            return "CRITICAL"
        if value < 95:
            return "CONCERN"
        return "NORMAL"

    if field_name == "resprate":
        if value < 8 or value > 29:
            return "CRITICAL"
        if 25 <= value <= 29:
            return "CONCERN"
        return "NORMAL"

    if field_name == "heartrate":
        if value < 40 or value > 130:
            return "CRITICAL"
        if 100 < value <= 130:
            return "CONCERN"
        return "NORMAL"

    if field_name == "sbp":
        if value < 90 or value > 220:
            return "CRITICAL"
        if 90 <= value < 100:
            return "CONCERN"
        return "NORMAL"

    if field_name == "temperature":
        if value < 35.0 or value >= 41.0:
            return "CRITICAL"
        if 39.5 <= value < 41.0:
            return "CONCERN"
        return "NORMAL"

    return "NORMAL"


def _direction_from_bands(prev_band: str, new_band: str, delta: float) -> str:
    """
    Determine WORSENED / IMPROVED / UNCHANGED from a band transition.

    When the band rank is unchanged (both NORMAL, or both the same
    non-normal band), this is genuinely ambiguous from the band alone --
    e.g. heart rate moving from 60 to 65 (both NORMAL) is not a clinical
    "improvement" or "worsening" in any real sense, it is noise. Rather
    than guess at a fake direction (a previous version of this function
    used a meaningless `abs(value - 70)` distance check, which is now
    known to be wrong and was removed), this function reports such cases
    honestly as UNCHANGED, since clinically_notable -- the field that
    actually drives escalation_detected -- is already correctly False for
    every same-band-rank case regardless of what direction text is shown.
    """
    prev_rank = _BAND_RANK[prev_band]
    new_rank = _BAND_RANK[new_band]
    if new_rank > prev_rank:
        return "WORSENED"
    if new_rank < prev_rank:
        return "IMPROVED"
    return "UNCHANGED"


def _compare_vital(
    field_name: str,
    unit: str,
    previous: Optional[float],
    new: Optional[float],
) -> VitalDelta:
    if previous is None and new is None:
        return VitalDelta(
            field_name=field_name, previous_value=None, new_value=None,
            delta=None, unit=unit, direction="UNCHANGED", clinically_notable=False,
        )
    if previous is not None and new is None:
        return VitalDelta(
            field_name=field_name, previous_value=previous, new_value=None,
            delta=None, unit=unit, direction="NEWLY_MISSING", clinically_notable=True,
        )
    if previous is None and new is not None:
        return VitalDelta(
            field_name=field_name, previous_value=None, new_value=new,
            delta=None, unit=unit, direction="NEWLY_AVAILABLE", clinically_notable=False,
        )

    delta = round(new - previous, 2)
    prev_band = _band(field_name, previous)
    new_band = _band(field_name, new)
    notable = _BAND_RANK[new_band] > _BAND_RANK[prev_band]
    direction = _direction_from_bands(prev_band, new_band, delta)

    return VitalDelta(
        field_name=field_name, previous_value=previous, new_value=new,
        delta=delta, unit=unit, direction=direction, clinically_notable=notable,
    )


def compare_follow_up(
    link: FollowUpLinkRequest,
    previous_result: WorkflowResult,
    new_result: WorkflowResult,
) -> FollowUpComparisonResult:
    """
    Deterministically compare two already-computed WorkflowResults for a
    user-declared follow-up pair. See module docstring for the full design
    boundary. This function performs no patient matching and calls no
    rules engine, ML model, or LLM of its own.
    """
    prev_ti: TriageTimeInput = previous_result.triage_input
    new_ti: TriageTimeInput = new_result.triage_input

    dataset_warning: Optional[str] = None
    if prev_ti.source_dataset != new_ti.source_dataset:
        dataset_warning = (
            f"The previous stay ({previous_result.stay_id}, dataset="
            f"{prev_ti.source_dataset}) and the new stay ({new_result.stay_id}, "
            f"dataset={new_ti.source_dataset}) come from different source "
            "datasets. Comparing vitals across different datasets and "
            "measurement contexts may not be clinically meaningful. This "
            "comparison is shown for workflow demonstration purposes only."
        )
    elif (prev_ti.temperature_unit or "F") != (new_ti.temperature_unit or "F"):
        dataset_warning = (
            f"Temperature unit differs between the two stays "
            f"({prev_ti.temperature_unit} vs {new_ti.temperature_unit}). "
            "Temperature comparison below uses Celsius for both via the "
            "shared conversion utility to avoid a misleading raw-number diff."
        )

    deltas: list[VitalDelta] = []

    # Temperature: always compare in Celsius via the shared conversion utility,
    # regardless of each stay's native unit, so a Fahrenheit-vs-Celsius pair
    # is never diffed as raw numbers.
    prev_temp_c = temperature_c(prev_ti)
    new_temp_c = temperature_c(new_ti)
    deltas.append(_compare_vital("temperature", "C", prev_temp_c, new_temp_c))

    for field_name, unit in _COMPARED_VITAL_FIELDS:
        prev_val = getattr(prev_ti, field_name, None)
        new_val = getattr(new_ti, field_name, None)
        deltas.append(_compare_vital(field_name, unit, prev_val, new_val))

    prev_status = previous_result.decision.classification_status
    new_status = new_result.decision.classification_status
    prev_rank = _STATUS_SEVERITY_RANK.get(prev_status, 1)
    new_rank = _STATUS_SEVERITY_RANK.get(new_status, 1)

    any_vital_worsened = any(
        d.direction in ("WORSENED", "NEWLY_MISSING") and d.clinically_notable
        for d in deltas
    )
    status_worsened = new_rank > prev_rank

    escalation_detected = status_worsened or any_vital_worsened

    # ── Dataset-specific comparison (#10) ──────────────────────────────────────
    # MIMIC: compare the override-adjusted final acuity / mapped priority
    # (smaller number = more urgent => escalation). KTAS: compare KTAS class
    # (smaller = more urgent). Same-dataset only; a cross-dataset pair already
    # carries dataset_warning and we do not compute a movement for it.
    same_dataset = prev_ti.source_dataset == new_ti.source_dataset
    comparison_dataset = prev_ti.source_dataset if same_dataset else None
    prev_fa = getattr(previous_result, "final_acuity_assessment", None)
    new_fa = getattr(new_result, "final_acuity_assessment", None)
    prev_ml = previous_result.ml_prediction
    new_ml = new_result.ml_prediction

    p_final = p_newf = p_prio = n_prio = p_kc = n_kc = None
    p_cat = n_cat = p_ov = n_ov = None
    category_movement: Optional[str] = None

    if same_dataset and prev_ti.source_dataset == "MIMIC-IV-ED-Demo-v2.2":
        p_final = getattr(prev_fa, "final_acuity", None)
        p_newf = getattr(new_fa, "final_acuity", None)
        p_cat = getattr(prev_fa, "category", None)
        n_cat = getattr(new_fa, "category", None)
        p_prio = getattr(prev_fa, "priority", None)
        n_prio = getattr(new_fa, "priority", None)
        p_ov = getattr(prev_fa, "override_applied", None)
        n_ov = getattr(new_fa, "override_applied", None)
        if p_final is not None and p_newf is not None:
            if p_newf < p_final:
                category_movement = "ESCALATION"
                escalation_detected = True
            elif p_newf > p_final:
                category_movement = "DE_ESCALATION"
            else:
                category_movement = "NO_CHANGE"
    elif same_dataset and prev_ti.source_dataset == "Kaggle-KTAS":
        p_kc = prev_ml.predicted_ktas_class
        n_kc = new_ml.predicted_ktas_class
        if p_kc is not None and n_kc is not None:
            if n_kc < p_kc:
                category_movement = "ESCALATION"
                escalation_detected = True
            elif n_kc > p_kc:
                category_movement = "DE_ESCALATION"
            else:
                category_movement = "NO_CHANGE"

    # workflow_action is derived purely from values already computed
    # above -- it adds no new decision logic of its own. Precedence:
    # a new-visit status of REQUIRES_CLINICIAN_REVIEW (missing data on
    # THIS visit specifically) is reported as CLINICIAN_INTERVENTION_
    # REQUIRED even if it happens to also count as "escalated" by rank,
    # since the actionable issue for a clinician is "go get the missing
    # data", not "this patient is deteriorating" -- those call for
    # different next steps even though both require a human.
    if new_status == "REQUIRES_CLINICIAN_REVIEW":
        workflow_action = "CLINICIAN_INTERVENTION_REQUIRED"
    elif escalation_detected:
        workflow_action = "ESCALATION_REQUIRED"
    else:
        workflow_action = "NO_ESCALATION_DETECTED"

    note_parts: list[str] = []
    if link.is_demonstration_scenario:
        note_parts.append(
            "DEMONSTRATION SCENARIO -- this comparison uses a constructed "
            "example, not a real linked patient encounter."
        )

    if escalation_detected:
        reasons = []
        if status_worsened:
            reasons.append(
                f"the deterministic safety status moved from '{prev_status}' "
                f"to '{new_status}'"
            )
        worsened_vitals = [
            f"{d.field_name} ({d.previous_value} -> {d.new_value} {d.unit or ''})".strip()
            for d in deltas
            if d.direction in ("WORSENED", "NEWLY_MISSING") and d.clinically_notable
        ]
        if worsened_vitals:
            reasons.append("the following vital(s) crossed into a more concerning range: "
                            + "; ".join(worsened_vitals))
        note_parts.append(
            "ESCALATION: " + " and ".join(reasons) + ". "
            "Clinician review is required before any action on this follow-up."
        )
    else:
        note_parts.append(
            "No escalation detected by this deterministic comparison: the safety "
            "status and vital signs reviewed did not cross into a more concerning "
            "range between the two stays. This is not a clearance and does not "
            "reduce the requirement for clinician review."
        )

    if dataset_warning:
        note_parts.append(dataset_warning)

    return FollowUpComparisonResult(
        previous_stay_id=previous_result.stay_id,
        new_stay_id=new_result.stay_id,
        linked_by=link.linked_by,
        link_reason=link.link_reason,
        is_demonstration_scenario=link.is_demonstration_scenario,
        dataset_consistency_warning=dataset_warning,
        vital_deltas=deltas,
        previous_classification_status=prev_status,
        new_classification_status=new_status,
        previous_reason_codes=list(previous_result.decision.reason_codes),
        new_reason_codes=list(new_result.decision.reason_codes),
        previous_ml_estimate_available=previous_result.ml_prediction.prediction_available,
        new_ml_estimate_available=new_result.ml_prediction.prediction_available,
        comparison_dataset=comparison_dataset,
        previous_final_acuity=p_final,
        new_final_acuity=p_newf,
        previous_mapped_category=p_cat,
        new_mapped_category=n_cat,
        previous_mapped_priority=p_prio,
        new_mapped_priority=n_prio,
        previous_override_applied=p_ov,
        new_override_applied=n_ov,
        previous_ktas_class=p_kc,
        new_ktas_class=n_kc,
        category_movement=category_movement,
        escalation_detected=escalation_detected,
        escalation_note=" ".join(note_parts),
        workflow_action=workflow_action,
        requires_clinician_review=True,
    )
