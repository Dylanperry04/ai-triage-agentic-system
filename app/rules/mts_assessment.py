"""Deterministic execution trace of an explicitly assessed clinical checklist.

This module does not infer symptoms, treat unknown findings as absent, or
replace the trained UHL model. The configured recommendation policy is owned
by the orchestrator, independently of this comparison's execution.
"""
from copy import deepcopy
from app.rules.mts_catalog import CHARTS, SOURCE, VERSION, catalog_hash, propose_flowchart
from app.schemas.mts import MTSChecklist


def _measured_predicate(rule_id, observations):
    """Only actually assessed measurements, with the book's required context.

    Definition pages: Hot 182, Low SpO2/PEFR 183, Very hot/very low 188,
    Warm 189. Skin/clinical findings can also be explicitly recorded by a nurse.
    """
    def value(key):
        observation = observations.get(key) or {}
        return observation.get("value") if observation.get("state") == "assessed" else None
    temperature = value("temperature")
    if rule_id in ("hot", "very_hot", "warm") and temperature is not None:
        threshold = {"hot": 38.5, "very_hot": 41, "warm": 37.5}[rule_id]
        matched = temperature > threshold if rule_id == "warm" else temperature >= threshold
        return matched, {"fields": ["temperature"], "operator": ">" if rule_id == "warm" else ">=", "threshold": threshold, "unit": "C"}
    saturation, oxygen = value("o2sat"), value("on_supplemental_oxygen")
    if rule_id in ("low_spo2", "very_low_spo2") and saturation is not None and oxygen is not None:
        threshold = 95 if oxygen or rule_id == "low_spo2" else 92
        matched = saturation < threshold and (rule_id != "low_spo2" or oxygen is False)
        return matched, {"fields": ["o2sat", "on_supplemental_oxygen"], "operator": "<", "threshold": threshold,
                         "unit": "%", "oxygen_therapy": oxygen, "requires_room_air": rule_id == "low_spo2"}
    measured, expected = value("pefr_measured"), value("pefr_expected")
    if rule_id in ("low_pefr", "very_low_pefr") and measured is not None and expected is not None:
        threshold = .33 if rule_id == "very_low_pefr" else .5
        return measured / expected < threshold, {"fields": ["pefr_measured", "pefr_expected"], "operator": "<",
                                                "ratio_threshold": threshold, "observed_ratio": measured / expected}
    return None, None


def evaluate_mts_checklist(checklist: MTSChecklist | None, complaint: str | None) -> dict:
    proposed = propose_flowchart(complaint)
    # The catalog hash binds the complete publisher/ISBN metadata. The audit
    # snapshot uses non-identifying bibliographic fields, compatible with the
    # existing raw-name/long-number redaction policy.
    source = {k: deepcopy(SOURCE[k]) for k in ("title", "edition", "printed_year", "validation_status", "scope")}
    base = {"catalog_version": VERSION, "catalog_sha256": catalog_hash(), "source": source,
            "flowchart_proposal": proposed, "presenting_complaint": complaint,
            "is_clinically_validated": False, "priority": None,
            "selected_flowchart": None, "matched_discriminators": [], "executed_rules": [],
            "evidence": [], "observations": {}, "unresolved_discriminators": []}
    if checklist is None:
        return {**base, "status": "awaiting_nurse_checklist"}
    chart = CHARTS[checklist.flowchart_id]
    observed = checklist.model_dump(mode="json")["observations"]
    rows = []
    for rule in chart["rules"]:
        observation = observed.get(rule["id"], {"state": "not_assessed", "value": None, "note": ""})
        state = observation["state"]
        outcome = ("matched" if observation["value"] is True else "not_matched") if state == "assessed" else (
            "not_applicable" if state == "not_applicable" else "unknown")
        automatic, criterion = _measured_predicate(rule["id"], observed)
        conflict = automatic is not None and state in ("assessed", "not_applicable") and automatic != (observation["value"] is True)
        # Either documented positive finding remains a positive signal. A
        # contradiction is retained explicitly for clinical review.
        if automatic is True:
            outcome = "matched"
        elif automatic is False and outcome == "unknown":
            outcome = "not_matched"
        if rule.get("applicability") == "paediatric":
            outcome = "not_applicable"
        rows.append({**rule, "outcome": outcome, "observation": observation,
                     "evaluation_method": "assessed_measurement_and_nursing_finding" if automatic is not None else "explicit_nursing_finding",
                     "measurement_criterion": criterion, "measurement_finding_conflict": conflict})
    matched = [r for r in rows if r["outcome"] == "matched"]
    conflicts = [r["id"] for r in rows if r.get("measurement_finding_conflict")]
    # The chart is ordered both across and within urgency bands. A known
    # positive supplies an urgency floor immediately, but an earlier unknown
    # finding can still change which rule determines the completed category.
    first_match = matched[0] if matched else None
    candidate = first_match["priority"] if first_match else None
    preceding = rows[:rows.index(first_match)] if first_match else rows
    unresolved_rows = [r for r in preceding if r["outcome"] == "unknown"]
    unresolved = [r["id"] for r in unresolved_rows]
    if conflicts:
        priority, status = None, "conflicting_observations_require_resolution"
    elif unresolved and (candidate is None or any(r["priority"] < candidate for r in unresolved_rows)):
        priority, status = None, "incomplete_higher_priority_observations"
    elif unresolved:
        priority, status = None, "incomplete_earlier_ordered_observations"
    elif candidate is not None:
        priority, status = candidate, "research_category_requires_clinician_review"
    elif chart["fallback_priority"] is None:
        priority, status = None, "select_another_flowchart"
    else:
        priority, status = chart["fallback_priority"], "research_category_requires_clinician_review"
    earliest_known_match = first_match["id"] if first_match else None
    determining = earliest_known_match if priority is not None else None
    return {**base, "status": status, "priority": priority, "candidate_priority": candidate,
            "selected_flowchart": {"id": checklist.flowchart_id, "label": chart["name"],
                "source_page": chart["source_page"], "selection_method": "nurse_confirmed",
                "selection_reason": checklist.selection_reason},
            "complaint_group": chart["complaint_group"],
            "matched_discriminators": [r["id"] for r in matched], "executed_rules": rows,
            "decisive_discriminators": [determining] if determining else [], "unresolved_discriminators": unresolved,
            "determining_discriminator": determining, "earliest_known_match": earliest_known_match,
            "unresolved_earlier_discriminators": unresolved if first_match else [],
            "assessment_complete": priority is not None,
            "observations": observed, "notes": checklist.notes,
            "conflicting_observations": conflicts,
            "evidence": [{"discriminator_id": r["id"], "observation": r["observation"],
                          "measurement_criterion": r.get("measurement_criterion"),
                          "supporting_measurements": {k: observed[k] for k in (r.get("measurement_criterion") or {}).get("fields", [])},
                          "priority": r["priority"], "source_page": r["source_page"]} for r in matched]}
