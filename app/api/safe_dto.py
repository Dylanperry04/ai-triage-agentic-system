"""
Safe response DTOs for the case_uid-keyed API.

The API must NEVER return raw identifiers (subject_id, stay_id, hadm_id) or
retrospective/outcome data (diagnoses, disposition, outtime, medrecon, pyxis) to
the UI. Returning a full EDTriageCase / WorkflowResult model_dump leaks all of
these nested inside the body even when the top-level case_uid is pseudonymous.

This module builds responses by ALLOW-LIST (only triage-time fields are copied
out), and applies a recursive identifier-stripper as defence-in-depth so a raw id
can never slip through even if the source schema changes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Keys that must never appear anywhere in an API response body.
FORBIDDEN_KEYS = {
    "subject_id", "stay_id", "hadm_id", "mrn",
    # retrospective / outcome / post-hoc data not part of triage-time input
    "diagnoses", "diagnosis", "disposition", "outtime", "medrecon", "pyxis",
    "retrospective_metadata", "retrospective_labels", "vitals_timeseries",
    "icd_code", "icd_title",
}

# Triage-time vital/feature fields that ARE safe to surface (clinical content).
SAFE_TRIAGE_FIELDS = (
    "temperature", "temperature_unit", "heartrate", "resprate", "o2sat",
    "sbp", "dbp", "pain", "chiefcomplaint",
)

# Demographic fields surfaced only if present and approved (coarse).
SAFE_DEMOGRAPHIC_FIELDS = ("gender", "arrival_transport")


def _present_fields(source: Dict[str, Any], allowed: tuple[str, ...]) -> Dict[str, Any]:
    """Copy allow-listed fields that have a real value.

    The UI should not receive legacy placeholders such as ``age: null`` or
    retired KTAS/demo fields. Empty values make the case view noisier and can
    mislead users into thinking a model feature was actively used.
    """
    out: Dict[str, Any] = {}
    for key in allowed:
        value = source.get(key)
        if value is None or value == "":
            continue
        out[key] = value
    return out


def strip_identifiers(obj: Any) -> Any:
    """Recursively remove FORBIDDEN_KEYS from any nested dict/list. Defence in
    depth: even allow-listed builders pass through this."""
    if isinstance(obj, dict):
        return {k: strip_identifiers(v) for k, v in obj.items()
                if k not in FORBIDDEN_KEYS}
    if isinstance(obj, list):
        return [strip_identifiers(v) for v in obj]
    return obj


def assert_no_raw_identifiers(obj: Any) -> None:
    """Raise if any forbidden identifier key appears anywhere in the structure.
    Used by tests and as a final guard before returning a response."""
    def _walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in {"subject_id", "stay_id", "hadm_id", "mrn"}:
                    raise ValueError(f"raw identifier '{k}' present in response")
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
    _walk(obj)


def _traceability() -> Dict[str, Any]:
    from app.version import APP_VERSION, PACKAGE_CHECKPOINT
    return {"app_version": APP_VERSION, "package_checkpoint": PACKAGE_CHECKPOINT}


def _mapped_manchester_equivalent(acuity) -> Optional[Dict[str, Any]]:
    from app.rules.acuity_mts_mapping import map_acuity_to_mts

    mapped = map_acuity_to_mts(acuity)
    if not mapped:
        return None
    return {
        "category": mapped.get("category"),
        "priority": mapped.get("priority"),
        "max_wait_minutes": mapped.get("max_wait_minutes"),
        "colour": mapped.get("colour"),
        "mapping_rule_version": mapped.get("mapping_rule_version"),
        "is_official_mts": False,
        "is_clinically_approved": False,
    }


def _acuity_mts_label(acuity, mapped: Optional[Dict[str, Any]] = None) -> str:
    if acuity is None:
        return "Acuity unavailable"
    mapped = mapped if mapped is not None else _mapped_manchester_equivalent(acuity)
    if not mapped:
        return f"Acuity {acuity}"
    return f"Acuity {acuity} / {mapped.get('category')}"


def safe_case_summary(case_uid: str, source_dataset: str) -> Dict[str, Any]:
    """Minimal non-clinical summary (for users without clinical-content access)."""
    return {"case_uid": case_uid, "source_dataset": source_dataset}


def _demo_notice(case: Dict[str, Any]) -> Dict[str, Any]:
    if not case.get("synthetic_demo"):
        return {}
    return {
        "is_synthetic_demo": True,
        "demo_data_notice": (
            case.get("demo_data_notice")
            or "Synthetic supervisor-demo case. Not real patient data."
        ),
    }


def safe_clinical_case_view(case_uid: str, source_dataset: str,
                            case: Dict[str, Any]) -> Dict[str, Any]:
    """Triage-time clinical view (for users WITH clinical-content access). Only
    allow-listed triage-time fields; no identifiers, no retrospective data."""
    triage = case.get("triage") or {}
    edstay = case.get("edstay") or {}
    triage_safe = _present_fields(triage, SAFE_TRIAGE_FIELDS)
    demo_safe = _present_fields(edstay, SAFE_DEMOGRAPHIC_FIELDS)
    view = {
        "case_uid": case_uid,
        "source_dataset": source_dataset,
        "triage": triage_safe,
        "demographics": demo_safe,
        **_demo_notice(case),
    }
    return strip_identifiers(view)


def safe_assessment_response(case_uid: str, source_dataset: str,
                             workflow_result: Dict[str, Any]) -> Dict[str, Any]:
    """Assessment result DTO: prediction + safety, never raw case/identifiers/
    outcomes. Pulls acuity from the nested final_acuity_assessment."""
    faa = workflow_result.get("final_acuity_assessment") or {}
    decision = workflow_result.get("decision") or {}
    data_validation = workflow_result.get("data_validation") or {}
    safety_review = workflow_result.get("safety_review") or {}
    ml_prediction = workflow_result.get("ml_prediction") or {}
    missing = data_validation.get("missing_required_fields") or []
    ml_error = ml_prediction.get("error") or ml_prediction.get("reason")
    ml_acuity = (
        ml_prediction.get("predicted_mimic_acuity")
        or ml_prediction.get("predicted_acuity")
        or ml_prediction.get("acuity")
    )
    final_acuity = faa.get("final_acuity")
    final_category = faa.get("category") or decision.get("category")
    out = {
        "case_uid": case_uid,
        "source_dataset": source_dataset,
        **_traceability(),
        "predicted_acuity": final_acuity,
        "predicted_category": final_category,
        "final_acuity": final_acuity,
        "final_category": final_category,
        "final_manchester_equivalent": _mapped_manchester_equivalent(final_acuity),
        "ml_prediction_available": bool(
            ml_prediction.get("prediction_available") and ml_acuity is not None and not ml_error
        ),
        "ml_predicted_acuity": ml_acuity,
        "ml_prediction_error": ml_error,
        "model_note": ml_prediction.get("model_note"),
        "rules_category": decision.get("category"),
        "override_applied": faa.get("override_applied") or decision.get("override_applied"),
        "override_note": faa.get("override_note") or decision.get("override_note"),
        "prediction_scale": ml_prediction.get("prediction_scale"),
        "confidence": ml_prediction.get("confidence"),
        "class_probabilities": ml_prediction.get("class_probabilities"),
        "safety_flags": safety_review.get("data_quality_flags")
                        or (decision.get("classification_status") if decision else None),
        "missing_fields": missing,
        "clinician_review_required": True,
        "research_only": True,
        "not_for_clinical_use": True,
    }
    return strip_identifiers(out)


def safe_multiagent_evidence(case_uid: str, source_dataset: str,
                             workflow_result: Dict[str, Any]) -> Dict[str, Any]:
    """Minimized, case_uid-only evidence for the live AutoGen explainer route.

    This deliberately mirrors the legacy fixture evidence at a smaller surface:
    only already-computed, triage-time and policy fields are copied. Raw stay,
    subject, hospital-admission, diagnosis, medication, and outcome fields are
    excluded before the evidence can reach any LLM tool.
    """
    triage_input = workflow_result.get("triage_input") or {}
    data_validation = workflow_result.get("data_validation") or {}
    decision = workflow_result.get("decision") or {}
    safety_review = workflow_result.get("safety_review") or {}
    ml_prediction = workflow_result.get("ml_prediction") or {}
    faa = workflow_result.get("final_acuity_assessment") or {}
    evidence = {
        "case_uid": case_uid,
        "source_dataset": source_dataset,
        "chief_complaint": triage_input.get("chiefcomplaint"),
        "age": triage_input.get("age"),
        "gender": triage_input.get("gender"),
        "arrival_transport": triage_input.get("arrival_transport"),
        "triage_vitals": {
            "temperature": triage_input.get("temperature"),
            "temperature_unit": triage_input.get("temperature_unit"),
            "heartrate": triage_input.get("heartrate"),
            "resprate": triage_input.get("resprate"),
            "o2sat": triage_input.get("o2sat"),
            "sbp": triage_input.get("sbp"),
            "dbp": triage_input.get("dbp"),
            "nrs_pain": triage_input.get("nrs_pain"),
            "pain_present": triage_input.get("pain_present"),
        },
        "data_validation_status": data_validation.get("validation_status"),
        "missing_fields": (
            (data_validation.get("missing_required_fields") or [])
            + (data_validation.get("non_informative_fields") or [])
        ),
        "rules_engine_status": decision.get("classification_status"),
        "rules_engine_reason_codes": decision.get("reason_codes"),
        "rules_engine_note": (
            "No clinician-approved Manchester category is assigned. If a "
            "category is present, it was produced by a provisional, unvalidated "
            "research ruleset (not the official Manchester Triage System) and "
            "requires clinician confirmation."
        ),
        "safety_flags": safety_review.get("data_quality_flags"),
        "is_safe_to_present": safety_review.get("is_safe_to_present"),
        "ml_research_estimate": {
            "available": ml_prediction.get("prediction_available"),
            "prediction_scale": ml_prediction.get("prediction_scale"),
            "predicted_mimic_acuity": ml_prediction.get("predicted_mimic_acuity"),
            "mapped_mts_category": ml_prediction.get("mapped_mts_category"),
            "mapped_mts_priority": ml_prediction.get("mapped_mts_priority"),
            "mapped_mts_max_wait_minutes": ml_prediction.get("mapped_mts_max_wait_minutes"),
            "model_note": ml_prediction.get("model_note"),
        },
        "final_acuity_assessment": {
            "applicable": faa.get("applicable"),
            "final_acuity": faa.get("final_acuity"),
            "category": faa.get("category"),
            "override_applied": faa.get("override_applied"),
            "override_tier": faa.get("override_tier"),
            "override_note": faa.get("override_note"),
        },
        "policy": (
            "Research prototype. Not for clinical use. The LLM/AutoGen layer "
            "only explains already-computed evidence and cannot assign, change, "
            "or approve triage. Human clinical review is required before any "
            "action."
        ),
    }
    return strip_identifiers(evidence)


def safe_followup_multiagent_evidence(
    case_uid: str,
    source_dataset: str,
    previous_workflow_result: Dict[str, Any],
    new_workflow_result: Dict[str, Any],
    *,
    changed_fields: Optional[List[str]] = None,
    changed_vitals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Case_uid-only evidence for explaining a follow-up reassessment.

    The agent receives only triage-time fields, already-computed assessment
    summaries, movement direction, and safety/model availability signals. It
    does not receive raw identifiers or retrospective/outcome data.
    """
    previous_evidence = safe_multiagent_evidence(
        case_uid, source_dataset, previous_workflow_result)
    new_evidence = safe_multiagent_evidence(case_uid, source_dataset, new_workflow_result)
    previous_acuity = (
        previous_evidence.get("final_acuity_assessment") or {}
    ).get("final_acuity")
    new_acuity = (new_evidence.get("final_acuity_assessment") or {}).get("final_acuity")
    comparison = safe_followup_response(
        case_uid,
        previous_acuity,
        new_acuity,
        changed_fields=changed_fields,
        changed_vitals=changed_vitals,
    )
    evidence = {
        "case_uid": case_uid,
        "source_dataset": source_dataset,
        "chief_complaint": previous_evidence.get("chief_complaint"),
        "age": previous_evidence.get("age"),
        "gender": previous_evidence.get("gender"),
        "arrival_transport": previous_evidence.get("arrival_transport"),
        "triage_vitals": new_evidence.get("triage_vitals"),
        "data_validation_status": new_evidence.get("data_validation_status"),
        "missing_fields": new_evidence.get("missing_fields"),
        "rules_engine_status": new_evidence.get("rules_engine_status"),
        "rules_engine_reason_codes": new_evidence.get("rules_engine_reason_codes"),
        "rules_engine_note": new_evidence.get("rules_engine_note"),
        "safety_flags": new_evidence.get("safety_flags"),
        "is_safe_to_present": new_evidence.get("is_safe_to_present"),
        "ml_research_estimate": new_evidence.get("ml_research_estimate"),
        "final_acuity_assessment": new_evidence.get("final_acuity_assessment"),
        "followup_comparison": {
            "change_direction": comparison.get("change_direction"),
            "change_summary": comparison.get("change_summary"),
            "changed_fields": changed_fields or [],
            "changed_vitals": changed_vitals or [],
            "previous_acuity": previous_acuity,
            "previous_manchester_equivalent": comparison.get(
                "previous_manchester_equivalent"
            ),
            "new_acuity": new_acuity,
            "new_manchester_equivalent": comparison.get("new_manchester_equivalent"),
            "original_triage_values": previous_evidence.get("triage_vitals"),
            "updated_triage_values": new_evidence.get("triage_vitals"),
            "chief_complaint": previous_evidence.get("chief_complaint"),
            "previous_final_acuity_assessment": previous_evidence.get(
                "final_acuity_assessment"
            ),
            "new_final_acuity_assessment": new_evidence.get(
                "final_acuity_assessment"
            ),
            "previous_safety_flags": previous_evidence.get("safety_flags"),
            "new_safety_flags": new_evidence.get("safety_flags"),
            "previous_missing_fields": previous_evidence.get("missing_fields"),
            "new_missing_fields": new_evidence.get("missing_fields"),
            "previous_model_available": (
                previous_evidence.get("ml_research_estimate") or {}
            ).get("available"),
            "new_model_available": (
                new_evidence.get("ml_research_estimate") or {}
            ).get("available"),
            "rules_only_safety_signal": {
                "previous_rules_engine_status": previous_evidence.get(
                    "rules_engine_status"
                ),
                "previous_rules_engine_reason_codes": previous_evidence.get(
                    "rules_engine_reason_codes"
                ),
                "new_rules_engine_status": new_evidence.get("rules_engine_status"),
                "new_rules_engine_reason_codes": new_evidence.get(
                    "rules_engine_reason_codes"
                ),
            },
            "clinician_review_required": True,
            "research_only": True,
        },
        "policy": (
            "Explain why the follow-up acuity changed or stayed the same after "
            "the edited triage-time vitals. Identify which changed fields "
            "mattered, distinguish ML estimate from deterministic safety logic "
            "and missing-data/rules-only signals, and state that clinician "
            "review is required. Do not assign, change, or approve triage."
        ),
    }
    return strip_identifiers(evidence)


def safe_multiagent_explanation_response(case_uid: str, source_dataset: str,
                                         team_result: Dict[str, Any]) -> Dict[str, Any]:
    """Safe response DTO for the case_uid AutoGen explainer route."""
    from app.security.redaction import redact_text

    agent_turns = []
    for turn in team_result.get("agent_turns") or []:
        agent_turns.append({
            "agent": str(turn.get("agent") or ""),
            "text": redact_text(str(turn.get("text") or "")),
        })
    out = {
        "case_uid": case_uid,
        "source_dataset": source_dataset,
        **_traceability(),
        "multiagent": True,
        "status": team_result.get("status"),
        "agent_turns": agent_turns,
        "final_explanation": redact_text(str(team_result.get("final_explanation") or "")),
        "safety_failures": list(team_result.get("safety_failures") or []),
        "explanation_only": True,
        "clinician_review_required": True,
        "research_only": True,
        "not_for_clinical_use": True,
    }
    return strip_identifiers(out)


def safe_followup_response(
    case_uid: str,
    previous_acuity,
    new_acuity,
    changed_fields: Optional[List[str]] = None,
    changed_vitals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    direction = "unchanged"
    if previous_acuity is not None and new_acuity is not None:
        if new_acuity < previous_acuity:
            direction = "escalation"
        elif new_acuity > previous_acuity:
            direction = "de-escalation"
    previous_mts = _mapped_manchester_equivalent(previous_acuity)
    new_mts = _mapped_manchester_equivalent(new_acuity)
    previous_label = _acuity_mts_label(previous_acuity, previous_mts)
    new_label = _acuity_mts_label(new_acuity, new_mts)
    if direction == "escalation":
        change_summary = f"Escalating from {previous_label} to {new_label}."
    elif direction == "de-escalation":
        change_summary = f"De-escalating from {previous_label} to {new_label}."
    else:
        change_summary = f"Staying at {new_label}."
    return {
        "case_uid": case_uid,
        **_traceability(),
        "previous_acuity": previous_acuity,
        "previous_manchester_equivalent": previous_mts,
        "new_acuity": new_acuity,
        "new_manchester_equivalent": new_mts,
        "change": direction,
        "change_direction": direction,
        "change_summary": change_summary,
        "changed_fields": changed_fields or [],
        "changed_vitals": changed_vitals or [],
        "clinician_review_required": True,
        "research_only": True,
        "not_for_clinical_use": True,
    }


def safe_explanation_response(case_uid: str, source_dataset: str,
                              workflow_result: Dict[str, Any],
                              explanation_text: Optional[str] = None) -> Dict[str, Any]:
    faa = workflow_result.get("final_acuity_assessment") or {}
    explanation_obj = workflow_result.get("explanation") or {}
    resolved_text = (
        explanation_text
        or workflow_result.get("llm_explanation")
        or explanation_obj.get("explanation_text")
    )
    out = {
        "case_uid": case_uid,
        "source_dataset": source_dataset,
        **_traceability(),
        "predicted_acuity": faa.get("final_acuity"),
        "explanation": resolved_text,
        "explanation_only": True,
        "clinician_review_required": True,
        "research_only": True,
    }
    return strip_identifiers(out)
