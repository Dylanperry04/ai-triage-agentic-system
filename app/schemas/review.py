from typing import Optional, Literal
from pydantic import BaseModel, Field


ReviewStatus = Literal[
    "not_reviewed",
    "approved_for_review",
    "request_missing_data",
    "override_required",
    "clinician_review_complete",
]


class HumanReviewRequest(BaseModel):
    stay_id: int
    reviewer_role: str = Field(..., description="Example: triage_nurse, emergency_physician, researcher")
    review_status: ReviewStatus
    review_comment: Optional[str] = None


class HumanReviewRecord(BaseModel):
    review_id: str
    stay_id: int
    reviewer_role: str
    review_status: ReviewStatus
    review_comment: Optional[str] = None
    created_at_utc: str
    audit_note: str = (
        "Human review record for audit trail. "
        "This does not create an automated clinical diagnosis or Manchester category."
    )