"""
Follow-up triage comparison schemas.

PURPOSE -- READ BEFORE EDITING OR REUSING ELSEWHERE
=====================================================
This module supports an explicit, user-declared workflow capability:
"this new triage run is a follow-up to a previous stay_id for the same
patient." It is NOT an automatic repeat-patient detector and must never
become one.

WHY THIS DISTINCTION MATTERS
==============================
The KTAS dataset (Choi et al., 1267 rows) and the MIMIC-IV-ED dataset used
elsewhere in this project have no real, verified patient identifier that
links separate encounters for the same person. The `subject_id` field
produced by both adapters is either a synthetic row index (KTAS) or a
MIMIC research identifier that is not validated as a real repeat-visit
key for this purpose. There is no ground truth anywhere in this project
for "these two rows are the same patient."

Building automatic matching (e.g. guessing from age + sex + similar chief
complaint) would mean inventing a result with no way to check whether it
is correct. That is the exact failure mode this whole project's leakage
guard, governance charter, and clinician-review requirement exist to
prevent applied to a new kind of data instead of a vital sign.

So this capability works the other way round: a human (the clinician, or
Dylan demonstrating the system) explicitly states "stay B is a follow-up
to stay A." The system never infers this on its own. Once stated, the
comparison logic below is fully deterministic and auditable -- it diffs
two already-computed WorkflowResult objects and reports what changed.

WHAT THIS MODULE DOES
========================
Given two stay_ids the user has linked, compares:
  - Vital signs (each field, with the unit-aware delta)
  - The deterministic safety-engine classification_status and reason codes
  - The ML research estimate (if available)
and produces a plain-language escalation note when the comparison crosses
a clinically meaningful threshold, or a "no significant change" note
otherwise. It NEVER assigns a Manchester or KTAS category itself --
it reports on the same two deterministic decisions already produced by
the existing, unmodified orchestrator for each stay individually.

WHAT THIS MODULE DOES NOT DO
===============================
- It does not search for or guess at a "matching" prior stay_id.
- It does not modify, override, or re-run the Manchester engine or the
  KTAS rules; it only compares two outputs that engine already produced.
- It does not claim the two stays are clinically the same patient in any
  validated sense -- that claim belongs entirely to whoever declared the
  link, and is recorded as their assertion, not as a system finding.
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field


class FollowUpLinkRequest(BaseModel):
    """
    User-declared assertion that `new_stay_id` is a follow-up encounter
    for the same patient as `previous_stay_id`.

    linked_by and link_reason are required so the audit trail always shows
    who asserted the link and why, since the system itself never infers it.
    """
    previous_stay_id: int
    new_stay_id: int
    linked_by: str
    link_reason: str = Field(
        default="",
        description=(
            "Free-text reason for the link, e.g. 'same patient returned to ED, "
            "MRN matched on paper chart' or 'demonstration scenario for walkthrough'."
        ),
    )
    is_demonstration_scenario: bool = Field(
        default=False,
        description=(
            "Set True for constructed/synthetic walkthrough examples that are not "
            "a real clinical assertion. Forces the UI and audit text to clearly "
            "label the comparison as a demonstration, not a real patient linkage."
        ),
    )


class VitalDelta(BaseModel):
    """One vital sign's value at both timepoints and the computed change."""
    field_name: str
    previous_value: Optional[float] = None
    new_value: Optional[float] = None
    delta: Optional[float] = None
    unit: Optional[str] = None
    direction: str = "UNCHANGED"  # WORSENED | IMPROVED | UNCHANGED | NEWLY_MISSING | NEWLY_AVAILABLE
    clinically_notable: bool = False


class FollowUpComparisonResult(BaseModel):
    """
    Full output of comparing two already-computed WorkflowResults for a
    user-declared follow-up pair. This is itself fully deterministic --
    no LLM is involved in producing this comparison.
    """
    previous_stay_id: int
    new_stay_id: int
    linked_by: str
    link_reason: str
    is_demonstration_scenario: bool

    dataset_consistency_warning: Optional[str] = Field(
        default=None,
        description=(
            "Set if the two stays come from different datasets (e.g. one KTAS, "
            "one MIMIC) or different temperature units, since a vital-sign "
            "comparison across incompatible measurement systems would be "
            "misleading without explicit conversion, which this module does not "
            "silently perform."
        ),
    )

    vital_deltas: List[VitalDelta] = Field(default_factory=list)

    previous_classification_status: str
    new_classification_status: str
    previous_reason_codes: List[str] = Field(default_factory=list)
    new_reason_codes: List[str] = Field(default_factory=list)

    previous_ml_estimate_available: bool = False
    new_ml_estimate_available: bool = False

    # Dataset-specific comparison (#10). MIMIC: final acuity / mapped priority
    # movement (smaller number = more urgent = escalation). KTAS: KTAS class.
    comparison_dataset: Optional[str] = None
    # MIMIC fields
    previous_final_acuity: Optional[int] = None
    new_final_acuity: Optional[int] = None
    previous_mapped_category: Optional[str] = None
    new_mapped_category: Optional[str] = None
    previous_mapped_priority: Optional[int] = None
    new_mapped_priority: Optional[int] = None
    previous_override_applied: Optional[bool] = None
    new_override_applied: Optional[bool] = None
    # KTAS fields
    previous_ktas_class: Optional[int] = None
    new_ktas_class: Optional[int] = None
    # priority/class movement direction: "ESCALATION" | "DE_ESCALATION" | "NO_CHANGE" | None
    category_movement: Optional[str] = None

    escalation_detected: bool = False
    escalation_note: str = ""

    workflow_action: str = Field(
        default="NO_ESCALATION_DETECTED",
        description=(
            "One of: ESCALATION_REQUIRED, CLINICIAN_INTERVENTION_REQUIRED, "
            "NO_ESCALATION_DETECTED. This is purely a label derived from "
            "escalation_detected and the two classification_status values "
            "already computed above -- it is not an independent decision, "
            "does not assign or change any triage category, and never "
            "overrides requires_clinician_review (which stays True "
            "regardless of this value). Added so a 'reassign triage "
            "assignments' style request can be satisfied with an explicit "
            "next-step label, without the system actually making an "
            "autonomous clinical triage decision."
        ),
    )

    requires_clinician_review: bool = True
    research_note: str = (
        "This comparison is a workflow demonstration capability. The link "
        "between the two stay_ids is a user-declared assertion, not a "
        "system-verified patient match. No automatic repeat-patient "
        "detection is performed anywhere in this project."
    )
