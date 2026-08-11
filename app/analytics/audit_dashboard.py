"""Audit dashboard normalization, filtering, and aggregation.

The app has several append-only evidence streams: access decisions, workflow
runs, human reviews, workflow reassessments, and workflow-state updates. This
module converts those already-redacted records into one dashboard shape. It does
not expose raw MIMIC identifiers; case filtering is by case_uid unless a legacy
record already contains a safely redacted field.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _contains(value: Any, needle: str) -> bool:
    if not needle:
        return True
    if isinstance(value, list):
        return any(_contains(item, needle) for item in value)
    return needle.lower() in str(value or "").lower()


def _normalise_roles(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v or "").strip()]
    if value:
        return [str(value)]
    return []


from app.rules.acuity_mts_mapping import acuity_from_text as canonical_acuity  # noqa: E402

# canonical_acuity is re-exported here under its analytics-facing name. The
# implementation lives in app/rules/acuity_mts_mapping.py, which owns the
# acuity<->category table; a local copy would be free to drift from it.


def _record_base(
    *,
    record_kind: str,
    source_record: Mapping[str, Any],
    timestamp: Any,
    case_uid: Any = None,
    source_dataset: Any = None,
    reviewer_role: Any = None,
    reviewer_roles: Any = None,
    action_type: Any = None,
    decision_type: Any = None,
    triage_level: Any = None,
    predicted_category: Any = None,
    escalation_status: Any = None,
    override_status: Any = None,
) -> dict[str, Any]:
    roles = _normalise_roles(reviewer_roles)
    if reviewer_role and reviewer_role not in roles:
        roles.insert(0, str(reviewer_role))
    timestamp_text = _text(timestamp)
    parsed = _parse_dt(timestamp_text)
    return {
        "record_kind": record_kind,
        "timestamp_utc": timestamp_text,
        "timestamp_epoch": parsed.timestamp() if parsed else None,
        "date": parsed.date().isoformat() if parsed else "",
        "case_uid": _text(case_uid),
        "source_dataset": _text(source_dataset),
        "reviewer_role": _text(reviewer_role or (roles[0] if roles else "")),
        "reviewer_roles": roles,
        "action_type": _text(action_type or record_kind),
        "decision_type": _text(decision_type),
        "triage_level": "" if triage_level is None else str(triage_level),
        # Single canonical acuity for grouping/filtering. triage_level keeps the
        # original text for display and for the existing exact-match filter.
        "acuity": canonical_acuity(triage_level),
        "predicted_category": _text(predicted_category),
        "escalation_status": _text(escalation_status),
        "override_status": "yes" if _boolish(override_status) else "no",
        "summary": "",
        "source_record": dict(source_record),
    }


def normalise_audit_records(
    *,
    access_events: Iterable[Mapping[str, Any]] = (),
    workflow_runs: Iterable[Mapping[str, Any]] = (),
    human_reviews: Iterable[Mapping[str, Any]] = (),
    workflow_reruns: Iterable[Mapping[str, Any]] = (),
    workflow_states: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for event in access_events:
        rec = _record_base(
            record_kind="access_event",
            source_record=event,
            timestamp=event.get("timestamp_utc"),
            case_uid=event.get("case_uid"),
            reviewer_roles=event.get("roles"),
            action_type=event.get("action"),
            decision_type=event.get("decision"),
        )
        rec["summary"] = f"{rec['action_type']} {rec['decision_type']}".strip()
        records.append(rec)

    for run in workflow_runs:
        rec = _record_base(
            record_kind="workflow_run",
            source_record=run,
            timestamp=run.get("timestamp_utc"),
            case_uid=run.get("case_uid"),
            source_dataset=run.get("source_dataset"),
            action_type=run.get("workflow_action") or "workflow_run",
            decision_type=run.get("human_review_status") or "assessment_run",
            triage_level=_first(run.get("final_acuity"), run.get("predicted_mimic_acuity")),
            predicted_category=_first(run.get("final_category"), run.get("mapped_mts_category")),
            escalation_status="pending" if run.get("workflow_action") == "ESCALATE" else "",
            override_status=run.get("override_applied"),
        )
        rec["summary"] = f"Workflow run: acuity {rec['triage_level'] or 'unknown'}"
        records.append(rec)

    for review in human_reviews:
        status = _text(review.get("review_status")).upper()
        override = bool(
            review.get("clinician_override")
            or review.get("override_reason")
            or "OVERRIDE" in status
        )
        if status in {"ESCALATION_CONFIRMED"}:
            escalation = "confirmed"
        elif status in {"ESCALATION_REJECTED"}:
            escalation = "rejected"
        elif status in {"ESCALATION_CLOSED", "ESCALATION_RESOLVED"}:
            escalation = "closed"
        elif "ESCALATION" in status:
            escalation = "requested"
        else:
            escalation = ""
        rec = _record_base(
            record_kind="human_review",
            source_record=review,
            timestamp=review.get("created_at_utc"),
            case_uid=review.get("case_uid"),
            source_dataset=review.get("source_dataset"),
            reviewer_role=review.get("reviewer_role"),
            reviewer_roles=review.get("reviewer_roles"),
            action_type=status.lower() or "human_review",
            decision_type=status,
            triage_level=review.get("system_prediction"),
            predicted_category=review.get("clinician_decision"),
            escalation_status=escalation,
            override_status=override,
        )
        rec["summary"] = f"Review: {status or 'UNKNOWN'}"
        rec["actor_user_id"] = _text(review.get("reviewer_user_id"))
        rec["actor_display_name"] = (
            _text(review.get("reviewer_display_name"))
            or _text(review.get("reviewer_user_id"))
        )
        rec["actor_identity_verified"] = bool(review.get("reviewer_identity_verified"))
        # Explicit, direction-labelled override pair. The generic
        # triage_level/predicted_category fields carry OPPOSITE meanings on
        # different record kinds (on a workflow run predicted_category is the
        # model's category; on a human review it is the CLINICIAN's decision),
        # which is how the override panel came to render clinician -> model
        # under a "model -> clinician" heading. These two names cannot be read
        # backwards.
        rec["system_acuity"] = canonical_acuity(review.get("system_prediction"))
        rec["clinician_acuity"] = canonical_acuity(
            review.get("clinician_override") or review.get("clinician_decision")
        )
        # The acuity the case actually SETTLED on. For an override that is the
        # clinician's value, not the system's -- grouping "decided cases by
        # acuity" on the system prediction counted an overridden case under the
        # acuity the clinician rejected.
        # `override` is the local BOOL computed above, not the "yes"/"no" string
        # that _record_base writes into override_status. Comparing it to "yes"
        # made this branch permanently false, so an overridden case still counted
        # under the acuity the clinician had rejected.
        rec["decided_acuity"] = (
            rec["clinician_acuity"]
            if override and rec["clinician_acuity"] is not None
            else rec["system_acuity"]
        )
        records.append(rec)

    for rerun in workflow_reruns:
        rec = _record_base(
            record_kind="workflow_rerun",
            source_record=rerun,
            timestamp=rerun.get("timestamp_utc"),
            case_uid=rerun.get("case_uid"),
            source_dataset=rerun.get("source_dataset"),
            action_type="followup_reassessment",
            decision_type=rerun.get("movement") or "NO_CHANGE",
            triage_level=rerun.get("new_final_acuity"),
            predicted_category=rerun.get("new_category"),
            escalation_status=(
                "pending" if str(rerun.get("movement") or "").upper() == "ESCALATION" else ""
            ),
            override_status=rerun.get("override_applied_new"),
        )
        changed = rerun.get("changed_vitals") or []
        fields = [str((item or {}).get("field")) for item in changed if isinstance(item, dict)]
        rec["changed_fields"] = [field for field in fields if field]
        rec["summary"] = f"Reassessment: {rec['decision_type']}"
        records.append(rec)

    for state in workflow_states:
        rec = _record_base(
            record_kind="workflow_state",
            source_record=state,
            timestamp=_first(state.get("updated_at_utc"), state.get("review_state_updated_at")),
            case_uid=state.get("case_uid"),
            source_dataset=state.get("source_dataset"),
            reviewer_role=state.get("reviewer_role"),
            action_type=state.get("last_action") or state.get("review_status"),
            decision_type=state.get("review_status"),
            triage_level=_first(
                (state.get("escalation_evidence") or {}).get("new_acuity"),
                (state.get("escalation_evidence") or {}).get("previous_acuity"),
            ),
            escalation_status=state.get("escalation_status"),
            override_status="override" in _text(state.get("last_action")).lower(),
        )
        rec["summary"] = f"Workflow state: {rec['decision_type'] or rec['action_type']}"
        # Per-person attribution. Absent on historical rows written before the
        # actor was recorded — those stay unattributed rather than being
        # back-filled from a role, which would invent an actor.
        rec["actor_user_id"] = _text(state.get("actor_user_id"))
        rec["actor_display_name"] = _text(state.get("actor_display_name"))
        rec["actor_identity_verified"] = bool(state.get("actor_identity_verified"))
        rec["assigned_acuity"] = canonical_acuity(state.get("assigned_acuity"))
        rec["case_status"] = _text(state.get("case_status"))
        rec["escalation_requested_by_role"] = _text(state.get("escalation_requested_by_role"))
        rec["escalation_confirmed_by_role"] = _text(state.get("escalation_confirmed_by_role"))
        rec["escalation_target_role"] = _text(state.get("escalation_target_role"))
        rec["assigned_staff_role"] = _text(state.get("assigned_staff_role"))
        rec["escalation_requested_at"] = _text(state.get("escalation_requested_at"))
        rec["escalation_confirmed_at"] = _text(state.get("escalation_confirmed_at"))
        rec["escalation_closed_at"] = _text(state.get("escalation_closed_at"))
        rec["escalation_reason"] = _text(state.get("escalation_reason"))
        rec["escalation_evidence"] = state.get("escalation_evidence") or {}
        rec["notification_target_role"] = _text(state.get("notification_target_role"))
        rec["overdue_vitals_responsible_role"] = _text(
            state.get("overdue_vitals_responsible_role")
        )
        rec["overdue_vitals_alert_active"] = _boolish(
            state.get("overdue_vitals_alert_active")
        )
        records.append(rec)

    records.sort(key=lambda row: row.get("timestamp_epoch") or 0, reverse=True)
    return records


@dataclass
class AuditFilters:
    start_utc: str | None = None
    end_utc: str | None = None
    patient_or_case: str | None = None
    triage_level: str | None = None
    # Filter on the CANONICAL acuity. triage_level is an exact match against the
    # raw per-record text, so filtering "Acuity 2" that way silently returned
    # only the records whose raw label happened to be "2" and dropped every
    # human review carrying "Very Urgent (Orange)" — the same shape mismatch
    # that split the acuity chart into duplicate slices.
    acuity: int | None = None
    reviewer_role: str | None = None
    decision_type: str | None = None
    action_type: str | None = None
    escalation_status: str | None = None
    override_status: str | None = None
    source_dataset: str | None = None


def filter_audit_records(
    records: Iterable[dict[str, Any]],
    filters: AuditFilters,
) -> list[dict[str, Any]]:
    start_dt = _parse_dt(filters.start_utc)
    end_dt = _parse_dt(filters.end_utc)
    patient = _text(filters.patient_or_case)
    triage = _text(filters.triage_level)
    acuity_filter = canonical_acuity(filters.acuity)
    role = _text(filters.reviewer_role)
    decision = _text(filters.decision_type)
    action = _text(filters.action_type)
    escalation = _text(filters.escalation_status)
    override = _text(filters.override_status).lower()
    dataset = _text(filters.source_dataset)

    out: list[dict[str, Any]] = []
    for rec in records:
        rec_dt = _parse_dt(rec.get("timestamp_utc"))
        if start_dt and (rec_dt is None or rec_dt < start_dt):
            continue
        if end_dt and (rec_dt is None or rec_dt > end_dt):
            continue
        if patient and not (
            _contains(rec.get("case_uid"), patient)
            or _contains(rec.get("patient_display_name"), patient)
            or _contains(rec.get("patient_display_label"), patient)
            or _contains(rec.get("encounter_display_label"), patient)
            or _contains(rec.get("display_identifier"), patient)
            or _contains((rec.get("source_record") or {}).get("subject_id"), patient)
        ):
            continue
        if triage and triage.lower() != _text(rec.get("triage_level")).lower():
            continue
        if acuity_filter is not None and rec.get("acuity") != acuity_filter:
            continue
        # Match the PRIMARY acting role only. The role bar groups on
        # reviewer_role, but this filter searched reviewer_roles (every role the
        # user holds), so drilling into "ed_doctor" could return records the
        # ed_doctor bar never counted -- a drill-down showing more than the bar
        # it came from. An action is taken under one role; that is the one that
        # belongs in both.
        if role and not _contains(rec.get("reviewer_role"), role):
            continue
        if decision and not _contains(rec.get("decision_type"), decision):
            continue
        if action and not _contains(rec.get("action_type"), action):
            continue
        if escalation and escalation.lower() != _text(rec.get("escalation_status")).lower():
            continue
        if override and override not in {"any", "all"}:
            wants_yes = override in {"yes", "true", "1", "override", "overridden"}
            if (rec.get("override_status") == "yes") != wants_yes:
                continue
        if dataset and not _contains(rec.get("source_dataset"), dataset):
            continue
        out.append(rec)
    return out


# Any human-submitted review action. Used for "who did work" counts.
_DECISION_STATUSES = {
    "ACCEPTED_AS_PRESENTED", "OVERRIDDEN", "UNCERTAIN", "REJECTED",
    "REQUEST_MORE_INFORMATION", "ESCALATION_REQUIRED", "ESCALATION_CONFIRMED",
    "ESCALATION_REJECTED", "ESCALATION_RESOLVED", "ESCALATION_CLOSED",
    "DISCHARGED", "CASE_CLOSED",
}

# The strict subset that actually SETTLES an acuity. Requesting more
# information, escalating, or discharging is workflow activity, not an acuity
# decision -- counting those in "decided cases by acuity" attributed an acuity
# to cases where none had been agreed.
_ACUITY_SETTLING_STATUSES = {"ACCEPTED_AS_PRESENTED", "OVERRIDDEN"}


def _decision_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records that represent a human SUBMITTING a decision.

    Excludes access_event (one row per authorised API request), workflow_run
    (a model assessment, which no human submitted) and workflow_state (the
    derived current state of a case, which would double-count the human_review
    that produced it).
    """
    return [
        rec for rec in records
        if rec.get("record_kind") == "human_review"
        and _text(rec.get("decision_type")).upper() in _DECISION_STATUSES
    ]


def _actor_breakdown(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-person decision counts, plus an explicit unattributed bucket."""
    # Count SUBMITTED reviews, not workflow_state rows. One submitted decision
    # writes several state records (current snapshot, history row, later
    # reassessment/notification/escalation updates), so counting state rows gave
    # a person several "decisions" for one review and could never reconcile
    # against the role totals, which are computed from reviews.
    review_rows = _decision_records(records)
    rows = [rec for rec in review_rows if _text(rec.get("actor_user_id"))]
    counts: Counter = Counter()
    names: dict[str, str] = {}
    roles: dict[str, str] = {}
    verified: dict[str, bool] = {}
    for rec in rows:
        uid = _text(rec.get("actor_user_id"))
        counts[uid] += 1
        names.setdefault(uid, _text(rec.get("actor_display_name")) or uid)
        roles.setdefault(uid, _text(rec.get("reviewer_role")))
        verified[uid] = verified.get(uid, True) and bool(rec.get("actor_identity_verified"))
    unattributed = sum(1 for rec in review_rows if not _text(rec.get("actor_user_id")))
    out = [
        {
            "actor_user_id": uid,
            "label": names[uid],
            "reviewer_role": roles.get(uid, ""),
            "identity_verified": verified.get(uid, False),
            "count": count,
        }
        for uid, count in counts.most_common()
    ]
    if unattributed:
        out.append({
            "actor_user_id": "",
            "label": "Unattributed (recorded before actor capture)",
            "reviewer_role": "",
            "identity_verified": False,
            "count": unattributed,
        })
    return out


def _acuity_settling_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        rec for rec in records
        if rec.get("record_kind") == "human_review"
        and _text(rec.get("decision_type")).upper() in _ACUITY_SETTLING_STATUSES
    ]


def _acuity_breakdown(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(
        rec.get("decided_acuity") for rec in records
        if rec.get("decided_acuity") in (1, 2, 3, 4, 5)
    )
    return [
        {"acuity": acuity, "label": f"Acuity {acuity}", "count": counts[acuity]}
        for acuity in sorted(counts)
    ]


def _breakdown(records: Iterable[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    counts = Counter(_text(rec.get(field)) or "Unknown" for rec in records)
    return [{"label": key, "count": count} for key, count in counts.most_common()]


def _latest_workflow_state_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    latest_ts: dict[str, float] = {}
    for rec in records:
        if rec.get("record_kind") != "workflow_state":
            continue
        case_uid = _text(rec.get("case_uid"))
        if not case_uid:
            continue
        ts = rec.get("timestamp_epoch")
        ts_num = float(ts) if isinstance(ts, (int, float)) else -1.0
        if case_uid not in latest or ts_num >= latest_ts.get(case_uid, -1.0):
            latest[case_uid] = rec
            latest_ts[case_uid] = ts_num
    return list(latest.values())


def aggregate_audit_dashboard(records: list[dict[str, Any]]) -> dict[str, Any]:
    latest_states = _latest_workflow_state_records(records)
    total_reviews = sum(1 for rec in records if rec.get("record_kind") == "human_review")
    escalations = sum(1 for rec in records if _text(rec.get("escalation_status")))
    clinical_records = [rec for rec in records if rec.get("record_kind") != "access_event"]

    def _unique_case_count(source: Iterable[dict[str, Any]]) -> int:
        case_uids = {_text(rec.get("case_uid")) for rec in source if _text(rec.get("case_uid"))}
        without_case_uid = sum(1 for rec in source if not _text(rec.get("case_uid")))
        return len(case_uids) + without_case_uid

    # An override is a PERSON rejecting the system's acuity and choosing another.
    # Counting override_status=="yes" across every record kind counted two things
    # that are not that:
    #   * a workflow run carrying override_applied=True, which is the
    #     deterministic vitals rule adjusting the model -- nobody overrode anything;
    #   * an UNCERTAIN review, which requires an override_reason but selects no
    #     clinician acuity, so nothing was overridden either.
    # Both inflated the KPI on cases where no clinician override happened.
    overrides = _unique_case_count(
        rec for rec in clinical_records
        if rec.get("record_kind") == "human_review"
        and _text(rec.get("decision_type")).upper() == "OVERRIDDEN"
        and rec.get("clinician_acuity") is not None
    )
    accepted = _unique_case_count(
        rec for rec in [*clinical_records, *latest_states]
        if _text(rec.get("decision_type")).upper() == "ACCEPTED_AS_PRESENTED"
        or _text(rec.get("case_status")).lower() == "accepted"
        or _text((rec.get("source_record") or {}).get("case_status")).lower() == "accepted"
    )
    request_info = _unique_case_count(
        rec for rec in [*clinical_records, *latest_states]
        if "REQUEST_MORE_INFORMATION" in _text(rec.get("decision_type")).upper()
        or "information_requested" in _text(rec.get("decision_type")).lower()
        or _text(rec.get("case_status")).lower() == "request_more_info"
    )
    workflow_source = latest_states or records
    open_escalations = sum(
        1 for rec in workflow_source
        if _text(rec.get("escalation_status")).lower() in {"requested", "pending"}
    )
    confirmed_escalations = sum(
        1 for rec in workflow_source
        if _text(rec.get("escalation_status")).lower() == "confirmed"
    )
    closed_escalations = sum(
        1 for rec in workflow_source
        if _text(rec.get("escalation_status")).lower()
        in {"closed", "rejected", "resolved"}
    )
    discharged = sum(
        1 for rec in workflow_source
        if _text(rec.get("case_status")).lower() in {"discharged", "closed", "case_closed"}
        or _text(rec.get("decision_type")).upper() in {"DISCHARGED", "CASE_CLOSED"}
    )
    overdue_vitals = sum(
        1 for rec in workflow_source if _boolish(rec.get("overdue_vitals_alert_active"))
    )
    active_cases = sum(
        1
        for rec in latest_states
        if _text(rec.get("case_status")).lower() not in {"discharged", "closed", "case_closed"}
    )
    escalation_worklist: list[dict[str, Any]] = []
    request_to_confirm_minutes: list[float] = []
    now = datetime.now(timezone.utc)
    for rec in latest_states:
        status = _text(rec.get("escalation_status")).lower()
        if status not in {"requested", "pending", "confirmed", "closed", "rejected", "resolved"}:
            continue
        requested_at = _parse_dt(rec.get("escalation_requested_at"))
        confirmed_at = _parse_dt(rec.get("escalation_confirmed_at"))
        closed_at = _parse_dt(rec.get("escalation_closed_at"))
        if requested_at and confirmed_at:
            request_to_confirm_minutes.append(
                max(0.0, (confirmed_at - requested_at).total_seconds() / 60.0)
            )
        awaiting_until = confirmed_at or closed_at or now
        awaiting_minutes = (
            max(0.0, (awaiting_until - requested_at).total_seconds() / 60.0)
            if requested_at else None
        )
        evidence = rec.get("escalation_evidence") or {}
        drivers = []
        if isinstance(evidence, dict):
            for key, value in evidence.items():
                if value not in (None, "", [], {}):
                    drivers.append(str(key))
        if rec.get("overdue_vitals_alert_active"):
            drivers.append("overdue_vitals")
        escalation_worklist.append({
            "case_uid": rec.get("case_uid"),
            "triage_level": rec.get("triage_level"),
            "escalation_status": status,
            "case_status": rec.get("case_status"),
            "requested_by_role": rec.get("escalation_requested_by_role"),
            "confirmed_by_role": rec.get("escalation_confirmed_by_role"),
            "target_role": rec.get("escalation_target_role") or rec.get("assigned_staff_role"),
            "requested_at": rec.get("escalation_requested_at"),
            "confirmed_at": rec.get("escalation_confirmed_at"),
            "closed_at": rec.get("escalation_closed_at"),
            "minutes_awaiting_confirmation": (
                round(awaiting_minutes, 1) if awaiting_minutes is not None else None
            ),
            "reason": rec.get("escalation_reason"),
            "drivers": sorted(set(drivers)),
            "final_outcome": rec.get("case_status") or status,
        })
    avg_request_to_confirmation = (
        sum(request_to_confirm_minutes) / len(request_to_confirm_minutes)
        if request_to_confirm_minutes else None
    )
    timeline_counts = Counter(rec.get("date") or "Unknown" for rec in records)
    timeline = [
        {"date": key, "count": timeline_counts[key]}
        for key in sorted(timeline_counts.keys())
    ]
    return {
        "summary": {
            "total_entries": len(records),
            "total_reviews": total_reviews,
            "escalations": escalations,
            "escalation_event_count": escalations,
            "overrides": overrides,
            # The top KPI counts every submitted workflow action (accept,
            # override, request-info, escalate, discharge, close, uncertain),
            # while the acuity donut counts only accept+override. Presenting both
            # as "clinical decisions" described two different populations under
            # one name. This is the donut's population, exposed so the UI can
            # label each number for what it actually is.
            "acuity_decisions": _unique_case_count(_acuity_settling_records(records)),
            "accepted_cases": accepted,
            "request_more_info_actions": request_info,
            "open_escalations": open_escalations,
            "confirmed_escalations": confirmed_escalations,
            "closed_escalations": closed_escalations,
            "discharged_cases": discharged,
            "overdue_vitals_alerts": overdue_vitals,
            "active_cases": active_cases,
            "unique_cases_with_workflow_state": len(latest_states),
            "average_escalation_request_to_confirmation_minutes": (
                round(avg_request_to_confirmation, 1)
                if avg_request_to_confirmation is not None else None
            ),
        },
        "timeline": timeline,
        "by_triage_level": _breakdown(records, "triage_level"),
        # Acuity distribution grouped on the CANONICAL acuity, so one clinical
        # acuity is one slice regardless of which record shape produced it.
        # Restricted to clinical decisions (the number a reviewer would expect
        # to reconcile against "Clinical decisions"), not every workflow run.
        "by_acuity": _acuity_breakdown(_acuity_settling_records(records)),
        # Submitted clinical decisions per role. by_reviewer_role counts EVERY
        # audit record including per-request access events, which is a very
        # different quantity and must not be presented as workload.
        "by_reviewer_role_decisions": _breakdown(
            _decision_records(records), "reviewer_role"
        ),
        # Who actually submitted the work, for the role drill-down. Only rows
        # that carry a recorded actor appear; unattributed rows are counted
        # separately so the total is never quietly wrong.
        "by_actor": _actor_breakdown(records),
        "by_predicted_category": _breakdown(records, "predicted_category"),
        "by_reviewer_role": _breakdown(records, "reviewer_role"),
        "by_action_type": _breakdown(records, "action_type"),
        "by_decision_type": _breakdown(records, "decision_type"),
        "by_case_uid": _breakdown(records, "case_uid")[:25],
        "by_escalation_status": _breakdown(records, "escalation_status"),
        "by_override_status": _breakdown(records, "override_status"),
        "by_source_dataset": _breakdown(records, "source_dataset"),
        "escalation_worklist": escalation_worklist[:250],
    }


def dashboard_payload(
    records: Iterable[dict[str, Any]],
    filters: AuditFilters | None = None,
    *,
    limit: int = 1000,
) -> dict[str, Any]:
    all_records = list(records)
    filtered = filter_audit_records(all_records, filters or AuditFilters())
    # Aggregate over the WHOLE filtered set. `limit` bounds the row detail sent
    # to the client, not the statistics: previously the slice happened first, so
    # every KPI, donut, bar and timeline silently described only the newest
    # `limit` records while the UI presented them as the totals for the window.
    aggregations = aggregate_audit_dashboard(filtered)
    page = filtered[: max(1, min(int(limit), 5000))]
    return {
        "filters": (filters or AuditFilters()).__dict__,
        "count": len(page),
        "matched": len(filtered),
        "aggregations_cover_all_matched": True,
        "total_unfiltered": len(all_records),
        "aggregations": aggregations,
        "entries": [
            {k: v for k, v in rec.items() if k != "source_record"}
            for rec in page
        ],
    }
