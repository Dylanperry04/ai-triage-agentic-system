"""
Workflow Orchestrator.

Sequences all agents in the correct clinical safety order:

  1. Data Validation Agent   — completeness check (deterministic, no LLM)
  2. Leakage guard           — via Safety Review Agent
  3. Case Summary Agent      — structured evidence summary (deterministic, no LLM)
  4. Manchester Rules Engine — deterministic triage classification (no LLM)
  5. Safety Review Agent     — data quality and high-risk flags (deterministic, no LLM)
  6. ML Prediction Agent     — research-grade risk estimates (model inference)
  7. LLM Explanation Agent   — clinician-facing explanation (Azure OpenAI)

The LLM is called LAST and ONLY after all deterministic checks have passed.
The LLM never modifies the Manchester decision or the ML prediction.
The LLM explains verified evidence — it does not create it.

Every stage is logged in the audit dict.
"""
from datetime import datetime, timezone

from app.schemas.internal import EDTriageCase
from app.schemas.workflow import WorkflowResult, ExplanationResult
from app.agents.data_validation_agent import run_data_validation_agent
from app.agents.case_summary_agent import run_case_summary_agent
from app.agents.safety_review_agent import run_safety_review
from app.agents.ml_prediction_agent import run_ml_prediction
from app.rules.manchester_engine import run_manchester_engine
from app.rules.vitals import temperature_c


def run_workflow(
    case: EDTriageCase,
    include_llm_explanation: bool = False,
    clinician_question: str | None = None,
) -> WorkflowResult:
    """
    Run the full triage workflow for one ED case.

    Parameters
    ----------
    case : EDTriageCase
        Full ED stay container with all source tables.
    include_llm_explanation : bool
        If True, call the LLM Explanation Agent after all deterministic
        checks. Defaults to False so the workflow is fast and works without
        Azure OpenAI configured.
    clinician_question : str | None
        Optional already-screened clinician question to include in the LLM
        explanation prompt. Ignored unless include_llm_explanation=True.

    Returns
    -------
    WorkflowResult
        Complete, auditable workflow output.
    """
    run_start = datetime.now(timezone.utc).isoformat()

    # ── Extract triage-time inputs (enforces leakage boundary) ───────────────
    triage_input = case.to_triage_time_input()
    retrospective_labels = case.to_retrospective_labels()

    # ── Step 1: Data validation ───────────────────────────────────────────────
    data_validation = run_data_validation_agent(triage_input)

    # ── Step 2: Case summary (no LLM) ────────────────────────────────────────
    case_summary = run_case_summary_agent(triage_input, data_validation)

    # ── Step 3: Manchester rules engine (deterministic) ───────────────────────
    decision = run_manchester_engine(triage_input)

    # ── Step 4: Safety review (deterministic) ─────────────────────────────────
    safety_review = run_safety_review(triage_input)

    # ── Step 5: ML risk prediction ────────────────────────────────────────────
    ml_prediction = run_ml_prediction(triage_input)

    # ── Step 5b: Deterministic escalate-only vital override (MIMIC headline) ───
    # For MIMIC cases, the ML-predicted acuity is the main result; a small set of
    # extreme/critical vital rules can only escalate it upward (never downward).
    # This produces the override-adjusted acuity the large coloured card shows.
    # Built when a full MIMIC-IV-ED acuity prediction ran. Not applicable when no
    # acuity prediction is available (e.g. no full-MIMIC model configured).
    from app.schemas.workflow import FinalAcuityAssessment
    final_acuity_assessment = FinalAcuityAssessment()
    if (
        triage_input.source_dataset == "MIMIC-IV-ED-Full-v2.2"
        and ml_prediction.prediction_available
        and ml_prediction.predicted_mimic_acuity is not None
    ):
        from app.rules.acuity_override import apply_acuity_override
        ov = apply_acuity_override(ml_prediction.predicted_mimic_acuity, triage_input)
        mts = ov.get("final_mts") or {}
        final_acuity_assessment = FinalAcuityAssessment(
            applicable=True,
            ml_predicted_acuity=ml_prediction.predicted_mimic_acuity,
            final_acuity=ov.get("final_acuity"),
            category=mts.get("category"),
            priority=mts.get("priority"),
            max_wait_minutes=mts.get("max_wait_minutes"),
            colour=mts.get("colour"),
            override_applied=ov.get("override_applied", False),
            override_tier=ov.get("override_tier"),
            override_flags=ov.get("override_flags", []),
            override_note=ov.get("override_note", ""),
            mapping_rule_version=mts.get("mapping_rule_version"),
            override_rule_version=ov.get("override_rule_version"),
            requires_clinician_review=True,
        )

    # ── Step 6: LLM Explanation (optional, only if requested) ─────────────────
    if include_llm_explanation:
        from app.agents.llm_explanation_agent import run_llm_explanation
        evidence_package = {
            "stay_id": triage_input.stay_id,
            "chief_complaint": triage_input.chiefcomplaint,
            "triage_vitals": {
                "temperature": triage_input.temperature,
                "temperature_unit": triage_input.temperature_unit,
                "temperature_c": temperature_c(triage_input),
                "heartrate_bpm": triage_input.heartrate,
                "resprate_per_min": triage_input.resprate,
                "o2sat_pct": triage_input.o2sat,
                "systolic_bp_mmhg": triage_input.sbp,
                "diastolic_bp_mmhg": triage_input.dbp,
                "pain_score_0_to_10": triage_input.pain,
            },
            "arrival_transport": triage_input.arrival_transport,
            "data_validation": data_validation.model_dump(mode="json"),
            "manchester_decision": decision.model_dump(mode="json"),
            "safety_flags": safety_review.data_quality_flags,
            "missing_vitals": safety_review.critical_missing_vitals,
            "ml_prediction": ml_prediction.model_dump(mode="json"),
            "final_acuity_assessment": final_acuity_assessment.model_dump(mode="json"),
        }
        llm_kwargs = {}
        if clinician_question is not None:
            llm_kwargs["clinician_question"] = clinician_question
        explanation = run_llm_explanation(evidence_package, **llm_kwargs)
    else:
        explanation = ExplanationResult(explanation_status="NOT_REQUESTED")

    run_end = datetime.now(timezone.utc).isoformat()

    # ── Audit record ──────────────────────────────────────────────────────────
    audit = {
        "workflow_version": "1.0.0",
        "run_start_utc": run_start,
        "run_end_utc": run_end,
        "source_dataset": case.source_dataset,
        "clinical_decision_policy": (
            "Any Manchester category present is PROVISIONAL (produced by an "
            "unvalidated research ruleset, not the official MTS) and requires "
            "clinician confirmation. ML outputs, where present, are research "
            "estimates only. A clinician must accept, override, escalate, or "
            "reject every output."
        ),
        "leakage_policy": (
            "Outcome and retrospective fields are excluded from triage_input. "
            "Leakage guard is checked on every case."
        ),
        "llm_policy": (
            "LLM is called ONLY to explain verified evidence. "
            "LLM does not assign triage categories or make clinical decisions."
        ),
        "ml_policy": (
            "ML prediction is full-MIMIC-only. If MIMIC_FULL_MODEL_PATH is not "
            "configured or the artifact is incompatible, prediction fails closed "
            "rather than substituting a retired demo or non-MIMIC model. A "
            "deterministic escalate-only vital override may raise (never lower) "
            "the displayed acuity for extreme/critical vitals. All ML outputs "
            "are research-grade only and do not replace deterministic safety "
            "review or clinician judgement."
        ),
        "safety_guardrail": (
            "requires_clinician_review=True on all rules-engine outputs. "
            "All outputs require clinician confirmation before any action."
        ),
        "governance_status": "RESEARCH_PROTOTYPE_NOT_FOR_CLINICAL_USE",
    }

    # workflow_action is derived purely from values already computed
    # above -- it adds no new decision logic of its own, mirroring the
    # same pattern used in app/agents/followup_comparison_agent.py for
    # FollowUpComparisonResult.workflow_action. Precedence:
    #   - REQUIRES_CLINICIAN_REVIEW (missing data, engine couldn't even
    #     attempt a classification) -> CLINICIAN_INTERVENTION_REQUIRED,
    #     since the actionable issue is "go get the missing data", not
    #     "this patient is physiologically deteriorating".
    #   - CRITICAL_PHYSIOLOGY_FLAGGED, or safety_review.is_safe_to_present
    #     is False, or PHYSIOLOGY_CONCERN_FLAGGED -> ESCALATION_REQUIRED,
    #     since all three represent a genuine physiological flag from the
    #     deterministic vital-sign checks, just at different severities.
    #   - everything else (AWAITING_APPROVED_CLINICAL_RULESET with clean
    #     vitals, MTS_CATEGORY_ASSIGNED_PENDING_CLINICIAN_REVIEW) ->
    #     NO_CRITICAL_PHYSIOLOGY_FLAGGED.
    #
    # RENAMED from the original NO_ESCALATION_DETECTED (found during a
    # later review pass to read, in a green badge, like a positive
    # clinical safety verdict -- "this was checked and is fine" -- when
    # the actual underlying fact for the more common of the two branches
    # mapped here, AWAITING_APPROVED_CLINICAL_RULESET, is an absence of
    # capability ("no clinical category could be assigned because no
    # approved ruleset exists"), not a finding. NO_CRITICAL_PHYSIOLOGY_FLAGGED
    # states only what was actually checked (the deterministic
    # vital-sign review), not a conclusion about the patient's overall
    # status. This value still covers two genuinely different
    # classification_status branches (one with no ruleset applied at
    # all, one with a category assigned but pending clinician
    # confirmation) -- no single short token can honestly distinguish
    # those two without becoming wrong for one of them, so the precise
    # classification_status is deliberately kept visible immediately
    # above this value in the assessment card (see
    # frontend/app.py::render_assessment_card) rather than trying to
    # compress that distinction into this name too.
    if decision.classification_status == "REQUIRES_CLINICIAN_REVIEW":
        workflow_action = "CLINICIAN_INTERVENTION_REQUIRED"
    elif (
        decision.classification_status in ("CRITICAL_PHYSIOLOGY_FLAGGED", "PHYSIOLOGY_CONCERN_FLAGGED")
        or not safety_review.is_safe_to_present
    ):
        workflow_action = "ESCALATION_REQUIRED"
    else:
        workflow_action = "NO_CRITICAL_PHYSIOLOGY_FLAGGED"

    return WorkflowResult(
        stay_id=case.stay_id,
        triage_input=triage_input,
        data_validation=data_validation,
        case_summary=case_summary,
        retrospective_labels=retrospective_labels,
        decision=decision,
        safety_review=safety_review,
        ml_prediction=ml_prediction,
        final_acuity_assessment=final_acuity_assessment,
        explanation=explanation,
        audit=audit,
        workflow_action=workflow_action,
    )
