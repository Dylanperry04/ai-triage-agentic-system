from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.internal import TriageTimeInput, RetrospectiveLabels
from app.schemas.validation import DataValidationResult
from app.schemas.summary import CaseSummaryResult


class ManchesterDecision(BaseModel):
    classification_status: str = "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
    category: Optional[str] = None
    priority: Optional[int] = None
    max_wait_minutes: Optional[int] = None
    reason_codes: List[str] = Field(default_factory=list)
    requires_clinician_review: bool = True


class SafetyReviewResult(BaseModel):
    data_quality_flags: List[str] = Field(default_factory=list)
    leakage_guard_passed: bool = True
    notes: List[str] = Field(default_factory=list)


class WorkflowResult(BaseModel):
    stay_id: int
    triage_input: TriageTimeInput
    data_validation: DataValidationResult
    case_summary: CaseSummaryResult
    retrospective_labels: RetrospectiveLabels
    decision: ManchesterDecision
    safety_review: SafetyReviewResult
    audit: Dict[str, Any] = Field(default_factory=dict)