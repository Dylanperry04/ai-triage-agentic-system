from typing import Optional
from pydantic import BaseModel


class HumanReviewRequest(BaseModel):
    """
    Request body for POST /review/submit.
    The API receives this from the Streamlit UI or any client submitting a review.
    """
    stay_id: int
    source_dataset: Optional[str] = None
    reviewer_role: str
    review_status: str
    review_comment: str
    clinician_override: Optional[str] = None
    override_reason: Optional[str] = None


class HumanReviewRecord(BaseModel):
    """
    Stored audit record. Adds server-generated fields (review_id, timestamp)
    to the incoming HumanReviewRequest.

    source_dataset is OPTIONAL and defaults to None, deliberately, so
    that deserializing any review record already on disk from before
    this field existed does not fail -- a record with source_dataset=None
    means "this review predates dataset-identity tracking", not "this
    review is definitely about a dataset called None". Added during a
    later review pass: stay_id alone is not a safe identifier once
    multiple datasets exist, since nothing in the schema prevents two
    different datasets from someday having genuinely overlapping integer
    stay_ids (KTAS and MIMIC demo do not overlap today only by
    coincidence of their current ranges, not by any guarantee). This is
    a deliberately narrow fix -- adding dataset identity to existing
    record types where the value is already cheaply available at every
    real construction site -- NOT the full case_uid-as-primary-key
    redesign (changing route signatures, the case selector's identity
    scheme, etc.) that a more thorough fix would eventually need; that
    remains a separate, intentionally-deferred piece of work.
    """
    review_id: str
    stay_id: int
    source_dataset: Optional[str] = None
    reviewer_role: str
    review_status: str
    review_comment: str
    clinician_override: Optional[str] = None
    override_reason: Optional[str] = None
    created_at_utc: str
