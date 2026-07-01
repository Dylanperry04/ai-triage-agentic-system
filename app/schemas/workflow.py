"""
Workflow output schemas.

One schema per agent output. Pydantic validates on construction.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.internal import TriageTimeInput, RetrospectiveLabels
from app.schemas.validation import DataValidationResult
from app.schemas.summary import CaseSummaryResult


class ManchesterDecision(BaseModel):
    """
    Output of the clinical safety rules engine.

    classification_status values:
      CRITICAL_PHYSIOLOGY_FLAGGED              — dangerous vital signs detected; no MTS category assigned
      PHYSIOLOGY_CONCERN_FLAGGED               — concerning vital signs; no MTS category assigned
      MTS_CATEGORY_ASSIGNED_PENDING_CLINICIAN_REVIEW — clinician-APPROVED ruleset active; clinician confirms
      PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW — a PROVISIONAL, unvalidated research ruleset is
                                                 active (not the official MTS, not clinician-approved);
                                                 category is provisional and clinician must confirm
      AWAITING_APPROVED_CLINICAL_RULESET       — pathway matched but no ruleset registered
      REQUIRES_CLINICIAN_REVIEW                — missing data; engine cannot classify
      NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED — engine disabled

    requires_clinician_review is ALWAYS True. The clinician confirms every result.
    ruleset_id is None when no approved ruleset is active.
    """
    classification_status: str = "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
    category: Optional[str] = None
    priority: Optional[int] = None
    max_wait_minutes: Optional[int] = None
    reason_codes: List[str] = Field(default_factory=list)
    requires_clinician_review: bool = True
    ruleset_id: Optional[str] = None


class SafetyReviewResult(BaseModel):
    """
    Output of the Safety Review Agent.
    Flags data quality issues and confirms leakage guard passed.
    Does NOT assign or modify triage categories.
    """
    data_quality_flags: List[str] = Field(default_factory=list)
    leakage_guard_passed: bool = True
    is_safe_to_present: bool = True           # False if critical flags present
    critical_missing_vitals: List[str] = Field(default_factory=list)
    high_risk_complaint_detected: bool = False
    notes: List[str] = Field(default_factory=list)


class MLPredictionResult(BaseModel):
    """
    Output of the ML research prediction layer.

    These are full-MIMIC/ESI acuity research estimates when a compatible model is
    configured. They are not Manchester triage categories, not clinical risk
    scores, and not approved for patient care.
    """
    model_config = {"protected_namespaces": ()}

    model_version: str = "not_loaded"
    model_name: str = "not_loaded"
    prediction_available: bool = False

    predicted_ktas_class: Optional[int] = None
    ktas_class_probabilities: Dict[str, float] = Field(default_factory=dict)
    emergency_research_estimate: Optional[float] = None
    non_emergency_research_estimate: Optional[float] = None

    # Backward-compatible display fields
    high_acuity_research_estimate: Optional[float] = None
    admission_risk_estimate: Optional[float] = None
    top_class_confidence: Optional[float] = None

    # Which prediction scale this result is on. None when no model ran.
    #   "MIMIC_ACUITY_1_5"             -> MIMIC/ESI acuity model output
    prediction_scale: Optional[str] = None

    # MIMIC acuity model fields (populated only for MIMIC cases).
    predicted_mimic_acuity: Optional[int] = None
    mimic_acuity_probabilities: Dict[str, float] = Field(default_factory=dict)
    mapped_mts_category: Optional[str] = None
    mapped_mts_priority: Optional[int] = None
    mapped_mts_max_wait_minutes: Optional[int] = None
    mapped_mts_colour: Optional[str] = None
    mapping_rule_version: Optional[str] = None

    research_label_only: bool = True
    requires_clinical_validation: bool = True
    model_note: str = (
        "Full MIMIC-IV-ED research model not loaded. Not for clinical use; human "
        "clinical review is required."
    )


class ExplanationResult(BaseModel):
    """
    Output of the LLM Explanation Agent.
    The LLM explains verified evidence only — it never assigns triage categories.
    """
    explanation_status: str = "NOT_RUN"
    explanation_text: str = ""
    safety_failures: List[str] = Field(default_factory=list)
    clinical_use_allowed: bool = False
    automated_manchester_triage_allowed: bool = False
    manchester_category_assigned: bool = False
    human_review_required: bool = True
    model: str = "not_configured"
    deployment: str = "not_configured"


class FinalAcuityAssessment(BaseModel):
    """
    The headline assessment for a MIMIC case: the ML-predicted acuity AFTER the
    deterministic escalate-only vital override, mapped to MTS-style display.

    This is what the large coloured assessment card shows for MIMIC cases. It is
    populated ONLY for full-MIMIC cases where a compatible acuity model runs; it
    stays empty when no ML prediction is available.

    Provisional and not clinically approved; clinician review required.
    """
    model_config = {"protected_namespaces": ()}

    applicable: bool = False               # True only for MIMIC cases with a prediction
    ml_predicted_acuity: Optional[int] = None
    final_acuity: Optional[int] = None     # after escalate-only override
    category: Optional[str] = None         # mapped MTS-style display category
    priority: Optional[int] = None
    max_wait_minutes: Optional[int] = None
    colour: Optional[str] = None
    override_applied: bool = False
    override_tier: Optional[str] = None    # "EXTREME" | "CRITICAL" | None
    override_flags: List[str] = Field(default_factory=list)
    override_note: str = ""
    mapping_rule_version: Optional[str] = None
    override_rule_version: Optional[str] = None
    requires_clinician_review: bool = True


class WorkflowResult(BaseModel):
    """
    Full output of one triage workflow run.
    Everything flows through this schema for auditability.
    """
    stay_id: int
    triage_input: TriageTimeInput
    data_validation: DataValidationResult
    case_summary: CaseSummaryResult
    retrospective_labels: RetrospectiveLabels
    decision: ManchesterDecision
    safety_review: SafetyReviewResult
    ml_prediction: MLPredictionResult = Field(default_factory=MLPredictionResult)
    final_acuity_assessment: FinalAcuityAssessment = Field(default_factory=FinalAcuityAssessment)
    explanation: ExplanationResult = Field(default_factory=ExplanationResult)
    audit: Dict[str, Any] = Field(default_factory=dict)

    workflow_action: str = Field(
        default="NO_CRITICAL_PHYSIOLOGY_FLAGGED",
        description=(
            "One of: ESCALATION_REQUIRED, CLINICIAN_INTERVENTION_REQUIRED, "
            "NO_CRITICAL_PHYSIOLOGY_FLAGGED. Derived purely from decision."
            "classification_status and safety_review.is_safe_to_present. "
            "This is a label for the assessment card, not an independent "
            "decision, and never overrides requires_clinician_review. "
            "RENAMED from the original NO_ESCALATION_DETECTED (a green-badge "
            "label found, during a later review pass, to read like a "
            "positive clinical safety verdict when the underlying fact is "
            "often an absence of capability -- no approved Manchester "
            "ruleset exists -- not a finding; see app/agents/orchestrator.py "
            "for the full reasoning). This is intentionally a DIFFERENT "
            "name from FollowUpComparisonResult.workflow_action's "
            "NO_ESCALATION_DETECTED value, which was deliberately left "
            "unchanged: that field answers a narrower, well-defined "
            "question (did this specific two-visit comparison detect an "
            "escalation), where 'no escalation detected' is an accurate "
            "description, not an overclaim about the patient's overall "
            "status."
        ),
    )
