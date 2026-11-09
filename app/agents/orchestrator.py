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
    from app.schemas.mts import MTSChecklist
    from app.rules.mts_assessment import evaluate_mts_checklist
    checklist = MTSChecklist.model_validate(case.mts_checklist) if case.mts_checklist else None
    mts_assessment = evaluate_mts_checklist(checklist, triage_input.chiefcomplaint)
    mts_assessment["checklist_revision"] = case.mts_checklist_revision
    mts_assessment["observation_provenance"] = case.mts_observation_provenance
    mts_assessment["recording_metadata"] = case.mts_recording_metadata
    mts_assessment["recommendation_policy"] = "ml_with_vital_and_confirmed_mts_escalation_only"

    # ── Step 1: Data validation ───────────────────────────────────────────────
    data_validation = run_data_validation_agent(triage_input)

    # ── Step 2: Case summary (no LLM) ────────────────────────────────────────
    case_summary = run_case_summary_agent(triage_input, data_validation)

    # The active UHL workflow uses the explicit source-based checklist. Keep
    # the legacy heuristic engine only for historical dataset compatibility.
    from app.constants import DATASET_SOURCE
    structured_pathway = triage_input.source_dataset == DATASET_SOURCE
    if structured_pathway:
        from app.schemas.workflow import ManchesterDecision
        from app.rules.acuity_mts_mapping import map_acuity_to_mts
        completed_priority = mts_assessment.get("priority")
        display = map_acuity_to_mts(completed_priority) if completed_priority else {}
        decision = ManchesterDecision(
            classification_status="STRUCTURED_MTS_RESEARCH_ASSESSMENT" if completed_priority else "REQUIRES_CLINICIAN_REVIEW",
            category=display.get("category"), priority=completed_priority,
            max_wait_minutes=display.get("max_wait_minutes"),
            reason_codes=["MTS:" + finding for finding in mts_assessment["matched_discriminators"]],
            ruleset_id=mts_assessment["catalog_version"], requires_clinician_review=True)
    else:
        decision = run_manchester_engine(triage_input)

    # ── Step 4: Safety review (deterministic) ─────────────────────────────────
    safety_review = run_safety_review(triage_input)

    # ── Step 5: ML risk prediction ────────────────────────────────────────────
    ml_prediction = run_ml_prediction(triage_input)

    # ── Step 5b: Deterministic escalate-only vital override (MIMIC headline) ───
    # For UHL cases, the ML-predicted acuity is the main result; a small set of
    # extreme/critical vital rules can only escalate it upward (never downward).
    # This produces the override-adjusted acuity the large coloured card shows.
    # Documented safety findings also supply a floor when the ML is unavailable.
    from app.schemas.workflow import FinalAcuityAssessment
    final_acuity_assessment = FinalAcuityAssessment()
    vital_override_applied = False
    from app.rules.discriminators import evaluate_vital_discriminators, build_discriminator_evidence
    evaluated_discriminators = evaluate_vital_discriminators(triage_input)
    if triage_input.source_dataset == "UHL_SYNTHETIC_TRIAGE_VITALS_ACUITY_FINAL_20260402":
        from app.rules.acuity_override import apply_acuity_override
        ml_acuity = ml_prediction.predicted_mimic_acuity if ml_prediction.prediction_available else None
        ov = apply_acuity_override(ml_acuity, triage_input)
        vital_override_applied = ov.get("override_applied", False)
        evaluated_discriminators = ov["evaluated_discriminators"]
        mts = ov.get("final_mts") or {}
        final_acuity_assessment = FinalAcuityAssessment(
            applicable=ov.get("final_acuity") is not None,
            ml_predicted_acuity=ml_acuity,
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

    # A positively identified discriminator supplies an urgency floor even
    # when still-higher findings remain unknown. Do not call an incomplete
    # chart a completed MTS classification or lower the existing ML estimate.
    mts_floor = mts_assessment.get("candidate_priority")
    before_mts = final_acuity_assessment.final_acuity
    mts_escalation = (mts_floor is not None and (before_mts is None or mts_floor < before_mts))
    if mts_escalation:
        from app.rules.acuity_mts_mapping import map_acuity_to_mts
        display = map_acuity_to_mts(mts_floor)
        matched_ids = [r["id"] for r in mts_assessment["executed_rules"]
                       if r["outcome"] == "matched" and r["priority"] == mts_floor]
        final_acuity_assessment = final_acuity_assessment.model_copy(update={
            "applicable": True,
            "final_acuity": mts_floor, "priority": mts_floor, "category": display["category"],
            "colour": display["colour"], "max_wait_minutes": display["max_wait_minutes"],
            "override_applied": True, "override_tier": "MTS_FLOWCHART",
            "override_rule_version": mts_assessment["catalog_version"],
            "override_flags": [*final_acuity_assessment.override_flags, *["MTS:" + x for x in matched_ids]],
            "override_note": (f"Assessed MTS findings raised the advisory from Acuity {before_mts} to {mts_floor}."
                              if before_mts is not None else f"Assessed MTS findings establish an urgency floor of Acuity {mts_floor}; ML is unavailable.")
                             + " Clinician review required.",
        })
    mts_assessment.update(escalation_applied=mts_escalation, advisory_before_mts=before_mts,
                          displayed_priority=final_acuity_assessment.final_acuity)
    mts_assessment["recommendation_basis"] = (
        "documented_safety_or_mts_floor_without_ml" if not ml_prediction.prediction_available and final_acuity_assessment.applicable
        else "ml_with_safety_and_mts_escalation" if final_acuity_assessment.applicable else "unavailable")
    from app.rules.decision_policy import system_identity
    mts_assessment["system_identity"] = system_identity()

    # ── Step 6: LLM Explanation (optional, only if requested) ─────────────────
    discriminator_evidence = build_discriminator_evidence(
        evaluated_discriminators,
        override_applied=vital_override_applied,
        model_policy={
            "type": ml_prediction.decision_rule_type,
            "threshold": ml_prediction.decision_rule_threshold,
            "changed_prediction": ml_prediction.decision_rule_changed_prediction,
            "modal_acuity": ml_prediction.argmax_acuity,
            "ml_acuity": ml_prediction.predicted_mimic_acuity,
        },
        pathway={
            "kind": "structured_mts" if structured_pathway else "legacy_heuristic",
            "ruleset_id": decision.ruleset_id,
            "classification_status": decision.classification_status,
            "reason_codes": decision.reason_codes,
            "priority": decision.priority,
            "candidate_priority": mts_floor if structured_pathway else None,
            "advisory_before": before_mts if structured_pathway else None,
            "advisory_after": final_acuity_assessment.final_acuity if structured_pathway else None,
            "affects_displayed_acuity": structured_pathway and mts_escalation,
        },
    )

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
            "discriminator_evidence": discriminator_evidence,
            "mts_assessment": mts_assessment,
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
            "The active UHL workflow retires the legacy heuristic pathway. The nurse-confirmed "
            "MTS assessment records its source chart, observation states, rules and "
            "catalog hash; its implementation requires clinical validation. "
            "Positive assessed MTS findings may raise, never lower, the ML/safety advisory. "
            "Incomplete charts are not completed MTS classifications. ML outputs are research "
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
            "The active ML prediction uses the pinned UHL model and its exact "
            "input contract. Missing or incompatible assets withhold prediction "
            "rather than substituting another model. A "
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

    if mts_floor is not None and mts_floor <= 2:
        workflow_action = "ESCALATION_REQUIRED"
    if triage_input.source_dataset == DATASET_SOURCE and any(
            r["status"] == "matched" and r["priority"] <= 2 for r in evaluated_discriminators):
        workflow_action = "ESCALATION_REQUIRED"
    elif checklist and mts_assessment.get("unresolved_discriminators") and workflow_action == "NO_CRITICAL_PHYSIOLOGY_FLAGGED":
        workflow_action = "CLINICIAN_INTERVENTION_REQUIRED"

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
        discriminator_evidence=discriminator_evidence,
        mts_assessment=mts_assessment,
        explanation=explanation,
        audit=audit,
        workflow_action=workflow_action,
    )
