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
    "sbp", "dbp", "pain", "chiefcomplaint", "age",
)

# Demographic fields surfaced only if present and approved (coarse).
SAFE_DEMOGRAPHIC_FIELDS = ("gender", "arrival_transport")
SAFE_QUEUE_METADATA_FIELDS = (
    "intime",
    "arrival_time",
    "arrival_time_utc",
    "submitted_at",
    "submitted_at_utc",
)
SAFE_NAME_FIELDS = (
    "patient_name",
    "full_name",
    "name",
    "display_name",
)


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


def _clean_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return " ".join(text.split())[:120]


def _first_label(*values: Any) -> str:
    for value in values:
        cleaned = _clean_label(value)
        if cleaned:
            return cleaned
    return ""


def _patient_name(case: Dict[str, Any]) -> str:
    edstay = case.get("edstay") or {}
    patient = case.get("patient") or {}
    candidates = []
    for field in SAFE_NAME_FIELDS:
        candidates.extend([case.get(field), edstay.get(field), patient.get(field)])
    return _first_label(*candidates)


def safe_display_identity(case: Dict[str, Any]) -> Dict[str, Any]:
    """Display-only identity labels for anonymous/local datasets.

    The API still avoids raw identifier keys such as ``stay_id`` or
    ``subject_id``. These strings are for UI labelling only: prefer a patient
    name when a deployment supplies one, otherwise show a patient/stay number
    rather than the old pseudonymous case token.
    """
    edstay = case.get("edstay") or {}
    triage = case.get("triage") or {}
    name = _patient_name(case)
    patient_number = _first_label(
        case.get("patient_number"),
        edstay.get("patient_number"),
        case.get("patient_id"),
        edstay.get("patient_id"),
        case.get("subject_id"),
        edstay.get("subject_id"),
        triage.get("subject_id"),
    )
    stay_number = _first_label(
        case.get("stay_number"),
        edstay.get("stay_number"),
        case.get("stay_id"),
        edstay.get("stay_id"),
        triage.get("stay_id"),
    )
    encounter_label = f"Stay {stay_number}" if stay_number else ""
    patient_label = name or (
        f"Patient {patient_number}" if patient_number else encounter_label
    )
    out = {}
    if patient_label:
        out["patient_display_label"] = patient_label
    if encounter_label:
        out["encounter_display_label"] = encounter_label
    display_identifier = name or patient_label or encounter_label
    if display_identifier:
        out["display_identifier"] = display_identifier
    if name:
        out["patient_display_name"] = name
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


def _probability_for_acuity(probabilities, acuity):
    """P(acuity) from a class-probability map, or None if unavailable.

    Returns None rather than 0.0 when the acuity is absent: a missing
    probability and a genuinely zero probability mean different things to a
    clinician, and the UI hides the bar for the former.
    """
    if not isinstance(probabilities, dict) or acuity is None:
        return None
    try:
        key = str(int(acuity))
    except (TypeError, ValueError):
        return None
    value = probabilities.get(key, probabilities.get(acuity))
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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


def safe_case_summary(
    case_uid: str,
    source_dataset: str,
    workflow_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Minimal non-clinical summary (for users without clinical-content access)."""
    out = {"case_uid": case_uid, "source_dataset": source_dataset}
    if workflow_state:
        out["workflow_state"] = workflow_state
    return strip_identifiers(out)


def _demo_notice(case: Dict[str, Any]) -> Dict[str, Any]:
    if case.get("synthetic_demo"):
        return {
            "is_synthetic_demo": True,
            "demo_data_notice": (
                case.get("demo_data_notice")
                or "Synthetic supervisor-demo case. Not real patient data."
            ),
        }
    if case.get("public_mimic_demo"):
        return {
            "is_public_mimic_demo": True,
            "demo_data_notice": (
                case.get("demo_data_notice")
                or "Public MIMIC-IV-ED demo subset. View-only sample data."
            ),
        }
    return {}


def _safe_queue_metadata(case: Dict[str, Any]) -> Dict[str, Any]:
    edstay = case.get("edstay") or {}
    audit = case.get("audit_metadata") or {}
    meta: Dict[str, Any] = {}
    for key in SAFE_QUEUE_METADATA_FIELDS:
        value = edstay.get(key, audit.get(key))
        if value not in (None, ""):
            meta[key] = value
    return meta


def safe_clinical_case_view(
    case_uid: str,
    source_dataset: str,
    case: Dict[str, Any],
    workflow_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Triage-time clinical view (for users WITH clinical-content access). Only
    allow-listed triage-time fields; no identifiers, no retrospective data."""
    triage = case.get("triage") or {}
    edstay = case.get("edstay") or {}
    triage_safe = _present_fields(triage, SAFE_TRIAGE_FIELDS)
    demo_safe = _present_fields(edstay, SAFE_DEMOGRAPHIC_FIELDS)
    view = {
        "case_uid": case_uid,
        "source_dataset": source_dataset,
        **safe_display_identity(case),
        "triage": triage_safe,
        "demographics": demo_safe,
        "queue_metadata": _safe_queue_metadata(case),
        **_demo_notice(case),
    }
    if workflow_state:
        view["workflow_state"] = workflow_state
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
        "confidence": (
            ml_prediction.get("confidence")
            or ml_prediction.get("top_class_confidence")
        ),
        "top_class_confidence": ml_prediction.get("top_class_confidence"),
        # Decision-rule provenance. top_class_confidence is the probability of
        # the MOST PROBABLE class, which is not necessarily the assigned acuity:
        # a non-argmax artefact decision rule (e.g. high_acuity_threshold) can
        # escalate away from it. Callers must render assigned_acuity_probability
        # beside the assigned acuity and show argmax_acuity separately, never
        # pair the assigned acuity with top_class_confidence.
        "argmax_acuity": ml_prediction.get("argmax_acuity"),
        "argmax_probability": ml_prediction.get("argmax_probability"),
        # Probability of the acuity the UI ACTUALLY shows, which is final_acuity
        # -- the value after BOTH the artefact decision rule and the
        # deterministic vitals override. ml_prediction.assigned_acuity_probability
        # is P(the artefact-rule output) and is therefore the wrong number
        # whenever the vitals override escalates further; using it would again
        # pair one acuity with another acuity's probability, which is the exact
        # defect this field exists to prevent.
        "assigned_acuity_probability": _probability_for_acuity(
            ml_prediction.get("mimic_acuity_probabilities")
            or ml_prediction.get("class_probabilities"),
            final_acuity,
        ),
        "ml_stage_acuity_probability": ml_prediction.get("assigned_acuity_probability"),
        "rules_override_applied": bool(faa.get("override_applied")),
        "rules_override_from_acuity": (
            faa.get("ml_predicted_acuity") if faa.get("override_applied") else None
        ),
        "decision_rule_type": ml_prediction.get("decision_rule_type"),
        "decision_rule_threshold": ml_prediction.get("decision_rule_threshold"),
        "decision_rule_changed_prediction": bool(
            ml_prediction.get("decision_rule_changed_prediction")
        ),
        "high_acuity_research_estimate": ml_prediction.get(
            "high_acuity_research_estimate"
        ),
        "class_probabilities": (
            ml_prediction.get("class_probabilities")
            or ml_prediction.get("mimic_acuity_probabilities")
        ),
        "mimic_acuity_probabilities": ml_prediction.get("mimic_acuity_probabilities"),
        "safety_flags": safety_review.get("data_quality_flags")
                        or (decision.get("classification_status") if decision else None),
        "critical_missing_vitals": safety_review.get("critical_missing_vitals"),
        "high_risk_complaint_detected": safety_review.get("high_risk_complaint_detected"),
        "is_safe_to_present": safety_review.get("is_safe_to_present"),
        "reason_codes": decision.get("reason_codes") or [],
        "classification_status": decision.get("classification_status"),
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
    missing_fields = (
        (data_validation.get("missing_required_fields") or [])
        + (data_validation.get("non_informative_fields") or [])
    )
    safety_flags = safety_review.get("data_quality_flags") or []
    reason_codes = decision.get("reason_codes") or []
    override_flags = faa.get("override_flags") or []
    model_probability_summary = {
        "predicted_mimic_acuity": ml_prediction.get("predicted_mimic_acuity"),
        "mapped_mts_category": ml_prediction.get("mapped_mts_category"),
        "high_acuity_research_estimate": ml_prediction.get(
            "high_acuity_research_estimate"
        ),
        "top_class_confidence": ml_prediction.get("top_class_confidence"),
        "mimic_acuity_probabilities": ml_prediction.get("mimic_acuity_probabilities"),
    }
    primary_drivers: List[str] = []
    if triage_input.get("chiefcomplaint"):
        primary_drivers.append(
            f"Chief complaint recorded as: {triage_input.get('chiefcomplaint')}"
        )
    primary_drivers.extend([f"Rules reason code: {code}" for code in reason_codes])
    primary_drivers.extend([f"Safety flag: {flag}" for flag in safety_flags])
    primary_drivers.extend([f"Override flag: {flag}" for flag in override_flags])
    if ml_prediction.get("prediction_available"):
        primary_drivers.append(
            "UHL ML research estimate predicted acuity "
            f"{ml_prediction.get('predicted_mimic_acuity')}"
        )
        if ml_prediction.get("high_acuity_research_estimate") is not None:
            primary_drivers.append(
                "ML high-acuity probability estimate: "
                f"{ml_prediction.get('high_acuity_research_estimate')}"
            )
        if ml_prediction.get("top_class_confidence") is not None:
            primary_drivers.append(
                "ML top-class confidence: "
                f"{ml_prediction.get('top_class_confidence')}"
            )
    if not reason_codes and not safety_flags and not override_flags:
        primary_drivers.append(
            "No deterministic critical-vital or safety reason code is present in "
            "the exposed evidence."
        )
    category_reasoning = {
        "task": (
            "Explain why the already-computed research estimate / provisional "
            "Manchester-style display category was shown. Do not re-list every "
            "vital sign; mention only decision-driving, abnormal, missing, or "
            "explicitly non-driving normal findings."
        ),
        "target_output": {
            "ml_predicted_acuity": faa.get("ml_predicted_acuity")
            or ml_prediction.get("predicted_mimic_acuity"),
            "final_acuity": faa.get("final_acuity"),
            "display_category": faa.get("category")
            or ml_prediction.get("mapped_mts_category"),
            "override_applied": faa.get("override_applied"),
            "override_tier": faa.get("override_tier"),
            "override_note": faa.get("override_note"),
        },
        "primary_drivers": primary_drivers,
        "missing_or_limited_data": missing_fields,
        "model_probability_summary": model_probability_summary,
        "rules_summary": {
            "classification_status": decision.get("classification_status"),
            "category": decision.get("category"),
            "priority": decision.get("priority"),
            "reason_codes": reason_codes,
        },
        "safety_summary": {
            "safety_flags": safety_flags,
            "is_safe_to_present": safety_review.get("is_safe_to_present"),
            "critical_missing_vitals": safety_review.get("critical_missing_vitals"),
            "high_risk_complaint_detected": safety_review.get(
                "high_risk_complaint_detected"
            ),
        },
    }
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
        "missing_fields": missing_fields,
        "rules_engine_status": decision.get("classification_status"),
        "rules_engine_reason_codes": reason_codes,
        "rules_engine_note": (
            "No clinician-approved Manchester category is assigned. If a "
            "category is present, it was produced by a provisional, unvalidated "
            "research ruleset (not the official Manchester Triage System) and "
            "requires clinician confirmation."
        ),
        "safety_flags": safety_flags,
        "is_safe_to_present": safety_review.get("is_safe_to_present"),
        "ml_research_estimate": {
            "available": ml_prediction.get("prediction_available"),
            "prediction_scale": ml_prediction.get("prediction_scale"),
            "predicted_mimic_acuity": ml_prediction.get("predicted_mimic_acuity"),
            "high_acuity_research_estimate": ml_prediction.get(
                "high_acuity_research_estimate"
            ),
            "top_class_confidence": ml_prediction.get("top_class_confidence"),
            "mimic_acuity_probabilities": ml_prediction.get(
                "mimic_acuity_probabilities"
            ),
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
            "override_flags": override_flags,
            "override_note": faa.get("override_note"),
        },
        "category_reasoning": category_reasoning,
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
        "category_reasoning": new_evidence.get("category_reasoning"),
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

    # Agent turns were previously identifier-redacted but NOT safety-checked and
    # NOT length-capped, while final_explanation got both. The UI renders these
    # verbatim behind "Show how the agents reached this", so an unsafe or
    # enormous turn reached the clinician through the expander even though the
    # same content would have been caught in the summary. Same treatment for
    # both, or the summary's guarantees are only cosmetic.
    from app.agents.autogen_multi_agent_team import condense_explanation
    from app.rules.llm_safety_filter import check_forbidden_phrases

    agent_turns = []
    turn_safety_failures: list[str] = []
    for turn in team_result.get("agent_turns") or []:
        agent = str(turn.get("agent") or "")
        raw = str(turn.get("text") or "")
        hits = check_forbidden_phrases(raw)
        if hits:
            turn_safety_failures.extend(f"{agent}: {h}" for h in hits)
            text = (
                "[Withheld: this agent turn contained directive clinical advice, "
                "which this system must not present. The finding is recorded in "
                "safety_failures.]"
            )
        else:
            text = condense_explanation(redact_text(raw))
        agent_turns.append({"agent": agent, "text": text})
    out = {
        "case_uid": case_uid,
        "source_dataset": source_dataset,
        **_traceability(),
        "multiagent": True,
        # A withheld agent turn previously left status as PASS, and the UI only
        # warns on SAFETY_FAIL — so a real safety finding was visible only to
        # someone who expanded the agent steps. The status must reflect the
        # worst finding in the payload, not just the summary's.
        "status": (
            "SAFETY_FAIL" if turn_safety_failures
            else team_result.get("status")
        ),
        "agent_turns": agent_turns,
        "final_explanation": redact_text(str(team_result.get("final_explanation") or "")),
        "safety_failures": list(team_result.get("safety_failures") or []) + turn_safety_failures,
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
        "explanation_status": explanation_obj.get("explanation_status"),
        "safety_failures": explanation_obj.get("safety_failures") or [],
        "explanation_only": True,
        "clinician_review_required": True,
        "research_only": True,
    }
    return strip_identifiers(out)


def safe_followup_explanation_response(
    case_uid: str,
    source_dataset: str,
    explanation_result: Dict[str, Any],
    followup_evidence: Dict[str, Any],
) -> Dict[str, Any]:
    """Safe response DTO for the single-agent follow-up Q&A route."""
    from app.security.redaction import redact_text

    comparison = followup_evidence.get("followup_comparison") or {}
    final_assessment = followup_evidence.get("final_acuity_assessment") or {}
    out = {
        "case_uid": case_uid,
        "source_dataset": source_dataset,
        **_traceability(),
        "multiagent": False,
        "predicted_acuity": final_assessment.get("final_acuity"),
        "followup_comparison": comparison,
        "explanation": redact_text(str(explanation_result.get("explanation_text") or "")),
        "explanation_status": explanation_result.get("explanation_status"),
        "safety_failures": explanation_result.get("safety_failures") or [],
        "explanation_only": True,
        "clinician_review_required": True,
        "research_only": True,
    }
    return strip_identifiers(out)
