from typing import List, Optional
from pydantic import BaseModel, Field


class CaseSummaryResult(BaseModel):
    summary_status: str
    chief_complaint: Optional[str] = None
    arrival_context: Optional[str] = None
    initial_vitals_summary: List[str] = Field(default_factory=list)
    missing_or_limited_data: List[str] = Field(default_factory=list)
    human_review_required: bool = True
    summary_text: str
    safety_note: str = (
        "This is a non-diagnostic summary based only on triage-time fields. "
        "It does not assign a Manchester category or clinical diagnosis."
    )
