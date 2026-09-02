"""Deterministic evidence builder for the ITD system assistant.

The ITD console previously answered every question from a fixed set of
configuration flags: it could not see the audit log at all, so questions like
"who denied access yesterday" or "which roles submitted decisions this week"
returned a canned status blurb. This module assembles the answerable facts from
the SAME already-redacted audit evidence the dashboard uses.

Design constraints this module is built to satisfy:

* NO patient clinical content. The assistant surface is declared "no patient
  content, enforced server-side", so this module never reads triage tables,
  chief complaints, vitals, or acuity for an individual patient. Case counts and
  pseudonymous case_uids only, and identifiers are gated by `include_case_uids`.
* NO invention. Every number returned is counted from a record. When there is no
  evidence for something the field is absent or zero, never estimated.
* Read-only. Nothing here mutates state or triggers a workflow.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_dt(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _within(record: Mapping[str, Any], since: datetime | None) -> bool:
    if since is None:
        return True
    stamp = _parse_dt(record.get("timestamp_utc"))
    return stamp is not None and stamp >= since


def _latest_state_per_case(states: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Reduce workflow-state rows to the most recent row for each case."""
    latest: dict[str, Mapping[str, Any]] = {}
    latest_ts: dict[str, datetime] = {}
    floor = datetime.min.replace(tzinfo=timezone.utc)
    for rec in states:
        case_uid = _text(rec.get("case_uid"))
        if not case_uid:
            continue
        stamp = _parse_dt(rec.get("timestamp_utc")) or floor
        if case_uid not in latest or stamp >= latest_ts.get(case_uid, floor):
            latest[case_uid] = rec
            latest_ts[case_uid] = stamp
    return list(latest.values())


def build_itd_evidence(
    records: Iterable[Mapping[str, Any]],
    *,
    window_days: int | None = 7,
    top_n: int = 8,
    include_case_uids: bool = False,
    calendar_today: bool = False,
) -> dict[str, Any]:
    """Summarise audit evidence for ITD questions.

    `records` are normalised audit-dashboard records. `window_days=None` means
    all retained history.
    """
    rows = list(records)
    if calendar_today:
        now = datetime.now(timezone.utc)
        local = now.astimezone(ZoneInfo("Europe/Dublin"))
        since = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    else:
        since = (
            datetime.now(timezone.utc) - timedelta(days=int(window_days))
            if window_days else None
        )
    scoped = [r for r in rows if _within(r, since)]

    access = [r for r in scoped if r.get("record_kind") == "access_event"]
    reviews = [r for r in scoped if r.get("record_kind") == "human_review"]
    runs = [r for r in scoped if r.get("record_kind") == "workflow_run"]
    states = [r for r in scoped if r.get("record_kind") == "workflow_state"]
    # Current-state questions are not activity-window questions. An escalation
    # opened eight days ago is still open today; derive current state from all
    # retained state rows while keeping action counts scoped to the requested
    # period.
    all_states = [r for r in rows if r.get("record_kind") == "workflow_state"]
    reruns = [r for r in scoped if r.get("record_kind") == "workflow_rerun"]
    escalation_reviews = [
        r for r in reviews
        if _text(r.get("decision_type")).upper()
        in {"ESCALATION_REQUIRED", "OVERRIDE_REQUIRED"}
    ]
    resolved_escalation_reviews = [
        r for r in reviews
        if _text(r.get("decision_type")).upper()
        in {"ESCALATION_RESOLVED", "ESCALATION_CLOSED", "ESCALATION_REJECTED"}
    ]
    information_requests = [
        r for r in reviews
        if _text(r.get("decision_type")).upper() == "REQUEST_MORE_INFORMATION"
    ]
    acceptance_reviews = [
        r for r in reviews
        if _text(r.get("decision_type")).upper() == "ACCEPTED_AS_PRESENTED"
    ]
    discharges = [
        r for r in reviews if _text(r.get("decision_type")).upper() == "DISCHARGED"
    ]
    admissions = [
        r for r in reviews if _text(r.get("decision_type")).upper() == "CASE_CLOSED"
    ]
    doctor_changes = [
        r for r in reviews
        if _text(r.get("decision_type")).upper() == "ESCALATION_RESOLVED"
        and r.get("system_acuity") is not None
        and r.get("clinician_acuity") is not None
        and r.get("system_acuity") != r.get("clinician_acuity")
    ]
    override_reviews = [
        r for r in reviews
        if _text(r.get("decision_type")).upper() == "OVERRIDDEN"
    ] + doctor_changes

    denied = [
        r for r in access
        if "deni" in _text(r.get("decision_type")).lower()
        or "deni" in _text(r.get("summary")).lower()
    ]

    def _top(values: Iterable[str]) -> list[dict[str, Any]]:
        counts = Counter(v for v in values if v)
        return [{"label": k, "count": n} for k, n in counts.most_common(top_n)]

    def _actor(record: Mapping[str, Any]) -> str:
        return _text(record.get("actor_display_name")) or _text(record.get("actor_user_id"))

    def _distinct_cases(records_for_metric: Iterable[Mapping[str, Any]]) -> int:
        return len({_text(r.get("case_uid")) for r in records_for_metric if _text(r.get("case_uid"))})

    def _unique_cases_by_person(
        records_for_metric: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        pairs = {
            (_actor(r), _text(r.get("case_uid")))
            for r in records_for_metric
            if _actor(r) and _text(r.get("case_uid"))
        }
        return _top(person for person, _case in pairs)

    # Count SUBMITTED reviews, not workflow_state rows: one submitted decision
    # writes several state records, so state-based counts inflate a person's
    # figure and cannot reconcile against the role totals.
    actors = Counter()
    actor_names: dict[str, str] = {}
    for r in reviews:
        uid = _text(r.get("actor_user_id"))
        if uid:
            actors[uid] += 1
            actor_names.setdefault(uid, _text(r.get("actor_display_name")) or uid)

    stamps = [s for s in (_parse_dt(r.get("timestamp_utc")) for r in scoped) if s]

    evidence: dict[str, Any] = {
        "window_days": window_days,
        "calendar_today": calendar_today,
        "window_start_utc": since.isoformat() if since else None,
        "records_in_window": len(scoped),
        "records_retained_total": len(rows),
        "earliest_record_utc": min(stamps).isoformat() if stamps else None,
        "latest_record_utc": max(stamps).isoformat() if stamps else None,
        "access_events": len(access),
        "access_denied": len(denied),
        "clinical_decisions_submitted": len(reviews),
        "model_assessments_run": len(runs),
        "cases_with_workflow_state": len(
            {_text(r.get("case_uid")) for r in states if _text(r.get("case_uid"))}
        ),
        "distinct_identities_seen": len(
            {_text(r.get("actor_user_id")) for r in reviews if _text(r.get("actor_user_id"))}
        ),
        "top_actions": _top(_text(r.get("action_type")) for r in scoped),
        "top_roles_all_records": _top(_text(r.get("reviewer_role")) for r in scoped),
        "top_roles_decisions_only": _top(_text(r.get("reviewer_role")) for r in reviews),
        "denied_by_role": _top(
            _text(r.get("reviewer_role")) or "Unattributed" for r in denied
        ),
        # Action-specific evidence. Without these the composer could only answer
        # "how many decisions" when asked "how many overrides", and could only
        # list everyone who submitted anything when asked who overrode — giving a
        # confidently wrong answer to a different question rather than declining.
        "overrides_submitted": len(override_reviews),
        "accepts_submitted": len(acceptance_reviews),
        "accepts_by_person": _top(_actor(r) for r in acceptance_reviews),
        "accepts_by_role": _top(_text(r.get("reviewer_role")) for r in acceptance_reviews),
        "escalations_submitted": len(escalation_reviews),
        "escalated_patients": _distinct_cases(escalation_reviews),
        "escalations_resolved": len(resolved_escalation_reviews),
        "escalations_by_person": _top(
            _actor(r)
            for r in escalation_reviews
        ),
        "escalated_patients_by_person": _unique_cases_by_person(escalation_reviews),
        "escalations_by_role": _top(
            _text(r.get("reviewer_role")) for r in escalation_reviews
        ),
        "escalations_unattributed": sum(
            1 for r in escalation_reviews
            if not (_text(r.get("actor_display_name")) or _text(r.get("actor_user_id")))
        ),
        "access_allowed": len(access) - len(denied),
        "overrides_by_person": _top(_actor(r) for r in override_reviews),
        "overrides_by_role": _top(_text(r.get("reviewer_role")) for r in override_reviews),
        "information_requests_submitted": len(information_requests),
        "information_requests_by_person": _top(_actor(r) for r in information_requests),
        "information_requests_by_role": _top(_text(r.get("reviewer_role")) for r in information_requests),
        "patients_discharged": _distinct_cases(discharges),
        "discharges_submitted": len(discharges),
        "discharges_by_person": _top(_actor(r) for r in discharges),
        "discharges_by_role": _top(_text(r.get("reviewer_role")) for r in discharges),
        "patients_admitted": _distinct_cases(admissions),
        "admissions_submitted": len(admissions),
        "admissions_by_person": _top(_actor(r) for r in admissions),
        "admissions_by_role": _top(_text(r.get("reviewer_role")) for r in admissions),
        "observation_updates": len(reruns),
        "observation_updates_by_person": _top(_actor(r) for r in reruns),
        "observation_updates_by_role": _top(_text(r.get("reviewer_role")) for r in reruns),
        "decisions_by_person": [
            {"label": actor_names[uid], "actor_user_id": uid, "count": n}
            for uid, n in actors.most_common(top_n)
        ],
        "decisions_unattributed": sum(
            1 for r in reviews if not _text(r.get("actor_user_id"))
        ),
        # Latest state PER CASE, not every historical state row. A case that was
        # escalated and later closed writes both rows; counting rows reported it
        # as still open, so the ITD console contradicted the Analytics page —
        # which already resolves to the latest state — about the same case.
        "open_escalations": sum(
            1 for r in _latest_state_per_case(all_states)
            if _text(r.get("escalation_status")).lower() in {"requested", "pending"}
        ),
        "overdue_vitals_alerts_active": sum(
            1 for r in _latest_state_per_case(all_states)
            if r.get("overdue_vitals_alert_active")
        ),
    }

    last = max(scoped, key=lambda r: _parse_dt(r.get("timestamp_utc")) or datetime.min.replace(tzinfo=timezone.utc)) if scoped else None
    if last:
        entry = {
            "timestamp_utc": _text(last.get("timestamp_utc")),
            "record_kind": _text(last.get("record_kind")),
            "action": _text(last.get("action_type")),
            "role": _text(last.get("reviewer_role")),
        }
        if include_case_uids and _text(last.get("case_uid")):
            entry["case_uid"] = _text(last.get("case_uid"))
        evidence["most_recent_audit_entry"] = entry
    return evidence
