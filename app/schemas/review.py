from typing import Optional
from pydantic import BaseModel, field_validator

# Allowed review statuses (bounded enum). Inclusive of the values the UI/tests use.
_ALLOWED_REVIEW_STATUSES = {
    "ACCEPTED_AS_PRESENTED", "OVERRIDDEN", "REVIEWED", "PENDING",
    "NEEDS_REVIEW", "REJECTED", "REQUEST_MORE_INFORMATION",
    "NOT_REVIEWED", "OVERRIDE_REQUIRED", "ESCALATION_REQUIRED",
    "REJECTED_DATA_QUALITY", "UNCERTAIN",
}
_MAX_COMMENT_LEN = 2000
_MAX_ROLE_LEN = 64
_MAX_OVERRIDE_LEN = 500


class HumanReviewRequest(BaseModel):
    """
    Request body for POST /review/submit.
    The API receives this from the Streamlit UI or any client submitting a review.

    Inputs are bounded: review_status must be one of a fixed set; free-text fields
    have length limits and are redacted of obvious identifiers before storage.
    """
    stay_id: int
    source_dataset: Optional[str] = None
    reviewer_role: str
    review_status: str
    review_comment: str
    clinician_override: Optional[str] = None
    override_reason: Optional[str] = None

    @field_validator("review_status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        up = (v or "").strip().upper()
        if up not in _ALLOWED_REVIEW_STATUSES:
            raise ValueError(
                f"Invalid review_status '{v}'. Allowed: {sorted(_ALLOWED_REVIEW_STATUSES)}")
        return up

    @field_validator("reviewer_role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("reviewer_role must not be empty")
        if len(v) > _MAX_ROLE_LEN:
            raise ValueError(f"reviewer_role too long (max {_MAX_ROLE_LEN})")
        return v

    @field_validator("review_comment")
    @classmethod
    def _check_comment(cls, v: str) -> str:
        from app.security.redaction import redact_text
        v = v or ""
        if len(v) > _MAX_COMMENT_LEN:
            raise ValueError(f"review_comment too long (max {_MAX_COMMENT_LEN})")
        return redact_text(v)

    @field_validator("clinician_override", "override_reason")
    @classmethod
    def _check_override(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        from app.security.redaction import redact_text
        if len(v) > _MAX_OVERRIDE_LEN:
            raise ValueError(f"override field too long (max {_MAX_OVERRIDE_LEN})")
        return redact_text(v)


class HumanReviewRecord(BaseModel):
    """
    Stored clinician-review audit record.

    IDENTITY: the persisted external identifier is `case_uid`, a PSEUDONYMOUS,
    stable token (never the raw stay_id), auto-derived at construction from
    source_dataset + stay_id if not supplied. The raw stay_id is accepted at
    construction (internal processing needs it) but is STRIPPED before the record
    is persisted (the guarded writer redacts identifier keys), so on disk a review
    record carries case_uid, not stay_id. Lookups match on the stored case_uid.

    REVIEWER IDENTITY: reviewer_user_id / reviewer_email_or_pseudonym /
    reviewer_roles / auth_source come from the AUTHENTICATED context (not a
    user-chosen dropdown) in the secured path. system_prediction and
    clinician_decision capture what the model said vs what the clinician decided;
    an override/uncertain decision requires override_reason.
    """
    model_config = {"protected_namespaces": ()}

    review_id: str
    # stay_id is accepted for internal use but redacted before persistence.
    stay_id: Optional[int] = None
    source_dataset: Optional[str] = None
    case_uid: Optional[str] = None  # pseudonymous; auto-derived if absent

    # Reviewer identity (from authenticated context in the secured path).
    reviewer_user_id: Optional[str] = None
    reviewer_email_or_pseudonym: Optional[str] = None
    reviewer_roles: Optional[list] = None
    auth_source: Optional[str] = None
    reviewer_role: Optional[str] = None  # legacy single-role (kept for back-compat)

    # Decision.
    review_status: str
    review_comment: str
    system_prediction: Optional[str] = None
    clinician_decision: Optional[str] = None
    clinician_override: Optional[str] = None
    override_reason: Optional[str] = None
    created_at_utc: str
    app_version: Optional[str] = None
    package_checkpoint: Optional[str] = None

    def model_post_init(self, __context) -> None:
        # Auto-derive the pseudonymous case_uid if it was not supplied.
        if not self.case_uid and self.stay_id is not None:
            from app.security.redaction import pseudonymous_case_uid
            object.__setattr__(
                self, "case_uid",
                pseudonymous_case_uid(self.source_dataset, self.stay_id),
            )
        if self.app_version is None or self.package_checkpoint is None:
            from app.version import APP_VERSION, PACKAGE_CHECKPOINT
            if self.app_version is None:
                object.__setattr__(self, "app_version", APP_VERSION)
            if self.package_checkpoint is None:
                object.__setattr__(self, "package_checkpoint", PACKAGE_CHECKPOINT)
