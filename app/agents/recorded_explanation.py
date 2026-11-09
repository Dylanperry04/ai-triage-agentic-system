"""Render decision facts from the immutable evidence of a logged assessment."""

import re


def _priority(value):
    if type(value) is int and 1 <= value <= 5:
        return value
    return None


def _finding_label(rule_id):
    if isinstance(rule_id, str) and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,99}", rule_id):
        return rule_id.replace("_", " ").lower()
    return None


def explain_recorded_decision(evidence):
    """Return display text and paths relative to the stored explanation evidence."""
    final = evidence.get("final_acuity_assessment")
    if not isinstance(final, dict) or "final_acuity" not in final:
        raise ValueError("The saved assessment has no final decision field")
    advisory = _priority(final["final_acuity"])
    if final["final_acuity"] is not None and advisory is None:
        raise ValueError("The saved assessment has an invalid advisory")
    model = evidence.get("ml_research_estimate") or {}
    available = model.get("available")
    ml = _priority(model.get("predicted_mimic_acuity")) if available is True else None
    if available is True and ml is None:
        raise ValueError("The recorded available model has no valid estimate")
    mts = evidence.get("mts_assessment") or {}
    vital = evidence.get("discriminator_evidence") or {}
    selection = vital.get("model_policy") or {}
    if selection.get("ml_acuity") is not None and (
        _priority(selection["ml_acuity"]) is None or selection["ml_acuity"] != ml
    ):
        raise ValueError("The recorded selection policy disagrees with the model estimate")
    pathway = vital.get("pathway") or {}
    statements = [
        f"The recorded advisory was Acuity {advisory}." if advisory is not None
        else "This assessment recorded no advisory category.",
    ]
    links = [{"claim": "advisory", "path": "/final_acuity_assessment/final_acuity"}]
    if ml is not None:
        statements.append(f"The model estimated Acuity {ml}.")
        links.append({"claim": "model_estimate", "path": "/ml_research_estimate"})
    elif available is False:
        statements.append("This assessment recorded no available model estimate.")
        links.append({"claim": "model_estimate", "path": "/ml_research_estimate"})
    else:
        statements.append("The saved evidence has no model availability status.")
    before_mts = ml
    if vital.get("override_applied") is True:
        floor = _priority(vital.get("safety_floor_acuity"))
        if floor is None or not any(
            item.get("status") == "matched" and _priority(item.get("priority")) == floor
            for item in (vital.get("decisive") or [])
        ):
            raise ValueError("The recorded vital safeguard has no valid urgency")
        if ml is not None and floor >= ml:
            raise ValueError("The recorded vital safeguard did not increase urgency")
        before_mts = floor
        statements.append(f"The vital safeguard established Acuity {floor}.")
        links.append({"claim": "vital_safeguard", "path": "/discriminator_evidence/decisive"})
    if mts.get("escalation_applied") is True:
        floor = _priority(mts.get("candidate_priority"))
        if floor is None or floor != advisory or (before_mts is not None and floor >= before_mts):
            raise ValueError("The recorded MTS contribution disagrees with the advisory")
        findings = []
        for index, rule in enumerate(mts.get("executed_rules") or []):
            if rule.get("outcome") == "matched" and _priority(rule.get("priority")) == floor:
                label = _finding_label(rule.get("id"))
                if label:
                    findings.append(label)
                    links.append({"claim": "mts_finding", "path": f"/mts_assessment/executed_rules/{index}"})
        if not findings:
            raise ValueError("The recorded MTS contribution has no matching rule")
        statements.append(f"Assessed MTS findings set an urgency floor of Acuity {floor} ({', '.join(findings)}).")
        links.append({"claim": "mts_contribution", "path": "/mts_assessment/escalation_applied"})
    elif pathway.get("kind") == "structured_mts" and advisory != before_mts:
        raise ValueError("The recorded advisory disagrees with the applied decision stages")
    if pathway.get("kind") == "structured_mts" and (
        pathway.get("advisory_before") != before_mts
        or pathway.get("advisory_after") != advisory
        or pathway.get("affects_displayed_acuity") is not (mts.get("escalation_applied") is True)
    ):
        raise ValueError("The recorded pathway disagrees with the applied decision stages")
    statements.append("Clinician review is required.")
    return " ".join(statements), links
