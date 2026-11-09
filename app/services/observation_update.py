"""Shared observation provenance, reminder and requested-information transition.

A partial set records exactly which readings were repeated. The existing
case-level reminder means time since any measured vital update, not proof of a
complete set; per-reading timestamps retain the unmeasured items' age.
"""
from app.security.identity import clinical_attribution_role

CORE_MEASUREMENTS = ("heartrate", "resprate", "o2sat", "sbp", "dbp", "temperature", "pain")


def observation_transition(previous, *, ctx, now, assessed_measurements, answered=True):
    fields = sorted(set(assessed_measurements) & set(CORE_MEASUREMENTS))
    role = clinical_attribution_role(ctx)
    provenance = dict(previous.get("observation_provenance") or {})
    for field in fields:
        provenance[field] = {"origin": "clinician_entered", "observed_at": now,
            "recorded_at": now, "recorded_by": getattr(ctx, "user_id", None),
            "recorded_by_role": role}
    result = {"observation_provenance": provenance}
    if fields:
        result.update(last_vitals_updated_at=now, last_vitals_checked_at=now,
            last_vitals_assessed_fields=fields, last_vitals_update_scope="partial" if len(fields) < 7 else "complete_set",
            overdue_vitals_alert_active=False, overdue_vitals_acknowledged_at=None)
    if answered:
        target = previous.get("requesting_role")
        if target not in {"triage_nurse", "ed_doctor"}:
            target = "ed_doctor" if previous.get("escalation_status") in {"requested", "confirmed", "pending"} else "triage_nurse"
        result.update(information_response_received_at=now,
            information_response_by_role=role,
            information_response_to_request_at=previous.get("request_timestamp"),
            reassessment_target_role=target, reassessment_notification_pending=True)
        if previous.get("case_status") == "request_more_info":
            result["case_status"] = "request_more_info"
    return result
