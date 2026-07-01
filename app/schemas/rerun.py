"""
Audit record for a live edit-rerun-reassign action: the SAME case is re-run with
edited triage-time vitals, and the previous vs new assignment is recorded along
with which vitals changed and the reason for any escalation/de-escalation.

This models the "same patient ID, changed vitals, new assignment" workflow as a
single explicit, audited action. Research/demo only; not clinical triage.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VitalChange(BaseModel):
    field: str
    previous: Optional[float] = None
    new: Optional[float] = None


class WorkflowRerunRecord(BaseModel):
    """One edit-rerun-reassign event for a single case_uid."""
    model_config = {"protected_namespaces": ()}

    rerun_id: str
    timestamp_utc: str
    case_uid: str
    source_dataset: Optional[str] = None
    stay_id: Optional[int] = None

    # Previous vs new assignment.
    previous_final_acuity: Optional[int] = None
    new_final_acuity: Optional[int] = None
    previous_category: Optional[str] = None
    new_category: Optional[str] = None
    previous_priority: Optional[int] = None
    new_priority: Optional[int] = None

    # What changed and why.
    changed_vitals: List[VitalChange] = Field(default_factory=list)
    movement: Optional[str] = None  # ESCALATION | DE_ESCALATION | NO_CHANGE
    override_applied_new: bool = False
    override_tier_new: Optional[str] = None
    reason: str = ""

    requires_clinician_review: bool = True
    app_version: Optional[str] = None
    package_checkpoint: Optional[str] = None

    def model_post_init(self, __context) -> None:
        if self.app_version is None or self.package_checkpoint is None:
            from app.version import APP_VERSION, PACKAGE_CHECKPOINT
            if self.app_version is None:
                object.__setattr__(self, "app_version", APP_VERSION)
            if self.package_checkpoint is None:
                object.__setattr__(self, "package_checkpoint", PACKAGE_CHECKPOINT)


def compute_movement(prev_acuity: Optional[int], new_acuity: Optional[int]) -> Optional[str]:
    """Smaller acuity number = more urgent => escalation."""
    if prev_acuity is None or new_acuity is None:
        return None
    if new_acuity < prev_acuity:
        return "ESCALATION"
    if new_acuity > prev_acuity:
        return "DE_ESCALATION"
    return "NO_CHANGE"
