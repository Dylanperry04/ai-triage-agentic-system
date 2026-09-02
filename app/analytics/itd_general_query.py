"""Validated general-purpose read-only queries over normalised audit evidence.

Azure OpenAI/Foundry is used only to translate natural language into this small
query language.  The model never receives audit rows and never calculates a
figure.  Every field, operator, time range and result limit is validated before
the backend executes the plan against the same redacted records as Analytics.

This is intentionally not SQL and cannot read files, call routes or mutate
state.  It can answer any question expressible from the recorded audit fields
and supported aggregate operations; when evidence is not recorded it says so
instead of substituting a nearby metric.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
import re
from statistics import median
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


DUBLIN = ZoneInfo("Europe/Dublin")
MAX_QUERIES = 4
MAX_FILTERS = 10
MAX_RESULT_ROWS = 20

FIELD_TYPES: dict[str, str] = {
    "record_kind": "text",
    "timestamp_utc": "datetime",
    "date": "text",
    "hour_of_day": "integer",
    "day": "text",
    "week": "text",
    "month": "text",
    "event_category": "text",
    "action_type": "text",
    "decision_type": "text",
    "reviewer_role": "text",
    "actor_display_name": "text",
    "actor_user_id": "text",
    "case_uid": "text",
    "workflow_run_id": "text",
    "acuity": "integer",
    "system_acuity": "integer",
    "clinician_acuity": "integer",
    "decided_acuity": "integer",
    "previous_acuity": "integer",
    "new_acuity": "integer",
    "escalation_status": "text",
    "override_status": "text",
    "case_status": "text",
    "assessment_execution_mode": "text",
    "assessment_triggered_by_role": "text",
    "changed_fields": "list",
    "reason": "text",
    "overdue_vitals_alert_active": "boolean",
}

FILTER_OPERATORS = {
    "eq", "not_eq", "in", "not_in", "contains", "exists", "is_true", "is_false",
}
OPERATIONS = {
    "count", "group_count", "percentage", "list", "time_histogram",
    "average", "duration", "values",
}
SCOPES = {"events", "latest_state"}
EVENT_LABELS = {
    "access_event": "access event",
    "access_denial": "access denial",
    "assessment": "model assessment",
    "reassessment": "reassessment",
    "acceptance": "acceptance",
    "override": "clinician override",
    "escalation": "escalation",
    "escalation_confirmation": "escalation ownership confirmation",
    "escalation_resolution": "escalation resolution",
    "information_request": "request for additional information",
    "discharge": "discharge",
    "admission": "admission/closure",
    "workflow_state": "workflow-state event",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_dt(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _event_category(record: Mapping[str, Any]) -> str:
    kind = _text(record.get("record_kind")).lower()
    decision = _text(record.get("decision_type")).upper()
    if kind == "access_event":
        return "access_denial" if "DENI" in decision or "BLOCK" in decision else "access_event"
    if kind == "workflow_run":
        return "assessment"
    if kind == "workflow_rerun":
        return "reassessment"
    if kind == "workflow_state":
        return "workflow_state"
    if decision == "ACCEPTED_AS_PRESENTED":
        return "acceptance"
    if decision == "OVERRIDDEN":
        return "override"
    if decision == "ESCALATION_RESOLVED":
        # Resolution is the primary workflow event even when the ED Doctor
        # changes the model acuity. ``override_status`` independently captures
        # that change, allowing both "last escalation events" and "all
        # clinician changes" to be answered without misclassifying either.
        return "escalation_resolution"
    if decision in {"ESCALATION_CLOSED", "ESCALATION_REJECTED"}:
        return "escalation_resolution"
    if decision == "ESCALATION_CONFIRMED":
        # Taking ownership is an ED Doctor action on an escalation that already
        # exists. Counting it as a new escalation attributed the initiating
        # Triage Nurse's work to the Doctor and made the marquee ITD answer
        # wrong after the normal request -> ownership -> resolution workflow.
        return "escalation_confirmation"
    if decision in {"ESCALATION_REQUIRED", "OVERRIDE_REQUIRED"}:
        return "escalation"
    if decision == "REQUEST_MORE_INFORMATION":
        return "information_request"
    if decision == "DISCHARGED":
        return "discharge"
    if decision == "CASE_CLOSED":
        return "admission"
    return kind or "audit_event"


def _value(record: Mapping[str, Any], field: str) -> Any:
    stamp = _parse_dt(record.get("timestamp_utc"))
    if field == "event_category":
        return _event_category(record)
    if field == "hour_of_day":
        return stamp.astimezone(DUBLIN).hour if stamp else None
    if field == "day":
        return stamp.astimezone(DUBLIN).date().isoformat() if stamp else ""
    if field == "week":
        if not stamp:
            return ""
        local = stamp.astimezone(DUBLIN)
        year, week, _weekday = local.isocalendar()
        return f"{year:04d}-W{week:02d}"
    if field == "month":
        return stamp.astimezone(DUBLIN).strftime("%Y-%m") if stamp else ""
    if field == "actor_display_name":
        return _text(record.get(field)) or _text(record.get("actor_user_id"))
    if field == "reason":
        value = (
            _text(record.get("override_reason"))
            or _text(record.get("review_comment"))
            or _text(record.get("escalation_reason"))
        )
        # Source records are already redacted before normalisation. Apply the
        # free-text scrubber again at this last projection boundary so a custom
        # durable reader cannot accidentally expose an identifier in an answer.
        from app.security.redaction import redact_text
        return redact_text(value)
    return record.get(field)


def _normalise_scalar(value: Any, field_type: str) -> Any:
    if field_type == "integer":
        try:
            number = float(value)
            return int(number) if math.isfinite(number) and number.is_integer() else None
        except (TypeError, ValueError):
            return None
    if field_type == "boolean":
        return str(value).lower() in {"1", "true", "yes", "on"}
    return _text(value)


def _matches(record: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    field = _text(item.get("field"))
    operator = _text(item.get("op"))
    field_type = FIELD_TYPES[field]
    actual = _value(record, field)
    expected = item.get("value")
    if operator == "exists":
        return actual not in (None, "", [], {})
    if operator == "is_true":
        return bool(actual)
    if operator == "is_false":
        return not bool(actual)
    if field_type == "list":
        values = [str(value).lower() for value in (actual or [])]
        target = str(expected or "").lower()
        if operator == "contains":
            return any(target in value for value in values)
        if operator == "eq":
            return target in values
        if operator == "not_eq":
            return target not in values
        candidates = {str(value).lower() for value in (expected or [])}
        present = any(value in candidates for value in values)
        return present if operator == "in" else not present
    if operator in {"in", "not_in"}:
        candidates = {
            _normalise_scalar(value, field_type) for value in (expected or [])
        }
        value = _normalise_scalar(actual, field_type)
        return (value in candidates) if operator == "in" else (value not in candidates)
    if operator == "contains":
        return _text(expected).lower() in _text(actual).lower()
    left = _normalise_scalar(actual, field_type)
    right = _normalise_scalar(expected, field_type)
    return left == right if operator == "eq" else left != right


def _valid_filter(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    if set(raw) - {"field", "op", "value"}:
        return None
    field = _text(raw.get("field"))
    operator = _text(raw.get("op") or "eq")
    if field not in FIELD_TYPES or operator not in FILTER_OPERATORS:
        return None
    value = raw.get("value")
    if FIELD_TYPES[field] == "boolean" and operator in {"eq", "not_eq", "in", "not_in"}:
        candidates = value if isinstance(value, list) else [value]
        if any(
            not isinstance(item, bool)
            and str(item).strip().lower() not in {"0", "1", "true", "false", "yes", "no", "on", "off"}
            for item in candidates
        ):
            return None
    if operator in {"in", "not_in"}:
        if not isinstance(value, list) or not value or len(value) > 20:
            return None
        if any(isinstance(item, (dict, list)) for item in value):
            return None
        if any(
            _normalise_scalar(item, FIELD_TYPES[field]) is None
            for item in value
        ):
            return None
        value = list(value)
    if isinstance(value, (dict, list)) and operator not in {"in", "not_in"}:
        return None
    if operator in {"eq", "not_eq", "contains"}:
        if value is None or _normalise_scalar(value, FIELD_TYPES[field]) is None:
            return None
    if operator in {"exists", "is_true", "is_false"} and value not in (None, True, False):
        return None
    return {"field": field, "op": operator, "value": value}


def _valid_time_range(raw: Any) -> dict[str, str | None] | None:
    if raw is None:
        return {"start_utc": None, "end_utc": None}
    if not isinstance(raw, Mapping) or set(raw) - {"start_utc", "end_utc"}:
        return None
    raw_start = raw.get("start_utc")
    raw_end = raw.get("end_utc")
    start = _parse_dt(raw.get("start_utc"))
    end = _parse_dt(raw.get("end_utc"))
    if raw_start not in (None, "") and start is None:
        return None
    if raw_end not in (None, "") and end is None:
        return None
    if start and end and end <= start:
        return None
    return {
        "start_utc": start.isoformat() if start else None,
        "end_utc": end.isoformat() if end else None,
    }


def _valid_subquery(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    operation = _text(raw.get("operation"))
    if operation not in OPERATIONS:
        return None
    common_keys = {"operation", "scope", "filters", "time_range"}
    operation_keys = {
        "count": {"distinct_field"},
        "group_count": {"group_by", "distinct_field", "limit", "rank"},
        "percentage": {"distinct_field", "numerator_filters", "denominator_filters"},
        "list": {"limit"},
        "time_histogram": {"distinct_field", "interval", "limit"},
        "average": {"value_field"},
        "duration": {"pair_by", "start_filters", "end_filters"},
        "values": {"value_field", "limit"},
    }
    if set(raw) - (common_keys | operation_keys[operation]):
        return None
    raw_filters = raw.get("filters", [])
    if not isinstance(raw_filters, list) or len(raw_filters) > MAX_FILTERS:
        return None
    filters = []
    for value in raw_filters:
        if not isinstance(value, Mapping):
            return None
        parsed = _valid_filter(value)
        if parsed is None:
            return None
        filters.append(parsed)
    scope = _text(raw.get("scope") or "events")
    if scope not in SCOPES:
        return None
    group_by = _text(raw.get("group_by")) or None
    distinct_field = _text(raw.get("distinct_field")) or None
    value_field = _text(raw.get("value_field")) or None
    pair_by = _text(raw.get("pair_by")) or "case_uid"
    if group_by is not None and group_by not in FIELD_TYPES:
        return None
    if distinct_field is not None and distinct_field not in FIELD_TYPES:
        return None
    if value_field is not None and value_field not in FIELD_TYPES:
        return None
    if pair_by not in {"case_uid", "workflow_run_id"}:
        return None
    try:
        raw_limit = raw.get("limit", 8)
        if isinstance(raw_limit, bool):
            return None
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return None
    if not 1 <= limit <= MAX_RESULT_ROWS:
        return None
    rank = raw.get("rank", "all")
    if rank not in {"most", "least", "all"}:
        return None
    interval = raw.get("interval", "day")
    if interval not in {"hour", "day", "week", "month"}:
        return None
    time_range = _valid_time_range(raw.get("time_range"))
    if time_range is None:
        return None
    output = {
        "operation": operation,
        "scope": scope,
        "filters": filters,
        "time_range": time_range,
        "group_by": group_by,
        "distinct_field": distinct_field,
        "value_field": value_field,
        "pair_by": pair_by,
        "limit": limit,
        "rank": rank,
        "interval": interval,
    }
    if operation == "percentage":
        numerator = []
        denominator = []
        for key, target in (("numerator_filters", numerator), ("denominator_filters", denominator)):
            values = raw.get(key)
            if not isinstance(values, list) or len(values) > MAX_FILTERS:
                return None
            for value in values:
                parsed = _valid_filter(value) if isinstance(value, Mapping) else None
                if parsed is None:
                    return None
                target.append(parsed)
        if not numerator or not denominator:
            return None
        output["numerator_filters"] = numerator
        output["denominator_filters"] = denominator
    if operation == "duration":
        start_filters = []
        end_filters = []
        for key, target in (("start_filters", start_filters), ("end_filters", end_filters)):
            values = raw.get(key)
            if not isinstance(values, list) or len(values) > MAX_FILTERS:
                return None
            for value in values:
                parsed = _valid_filter(value) if isinstance(value, Mapping) else None
                if parsed is None:
                    return None
                target.append(parsed)
        if not start_filters or not end_filters:
            return None
        output["start_filters"] = start_filters
        output["end_filters"] = end_filters
    if operation == "group_count" and group_by is None:
        return None
    if operation in {"average", "values"} and value_field is None:
        return None
    return output


def validate_plan(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not raw or not isinstance(raw, Mapping):
        return None
    if set(raw) - {"queries", "unsupported_reason"}:
        return None
    reason = _text(raw.get("unsupported_reason"))
    if reason:
        if raw.get("queries") not in (None, []) or len(reason) > 300 or "\n" in reason or "\r" in reason:
            return None
        return {"queries": [], "unsupported_reason": reason}
    values = raw.get("queries")
    if not isinstance(values, list) or not values or len(values) > MAX_QUERIES:
        return None
    queries = []
    for value in values:
        parsed = _valid_subquery(value) if isinstance(value, Mapping) else None
        if parsed is None:
            return None
        queries.append(parsed)
    return {"queries": queries, "unsupported_reason": ""}


def _time_range_from_question(question: str, now: datetime) -> dict[str, str | None]:
    q = question.lower()
    local = now.astimezone(DUBLIN)
    if "all time" in q or "ever" in q or "all retained" in q:
        return {"start_utc": None, "end_utc": None}
    between = re.search(r"(?:between|from)\s+(\d{4}-\d{2}-\d{2})\s+(?:and|to)\s+(\d{4}-\d{2}-\d{2})", q)
    if between:
        start = datetime.fromisoformat(between.group(1)).replace(tzinfo=DUBLIN)
        end = datetime.fromisoformat(between.group(2)).replace(tzinfo=DUBLIN) + timedelta(days=1)
        return {"start_utc": start.astimezone(timezone.utc).isoformat(), "end_utc": end.astimezone(timezone.utc).isoformat()}
    single_date = re.search(r"(?:on|during|for)\s+(\d{4}-\d{2}-\d{2})\b", q)
    if single_date:
        start = datetime.fromisoformat(single_date.group(1)).replace(tzinfo=DUBLIN)
        end = start + timedelta(days=1)
        return {"start_utc": start.astimezone(timezone.utc).isoformat(), "end_utc": end.astimezone(timezone.utc).isoformat()}
    if "yesterday" in q:
        end = local.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
    elif "today" in q:
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = None
    elif "this week" in q:
        start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = None
    elif "last month" in q or "previous month" in q:
        this_month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = this_month
        start = (this_month - timedelta(days=1)).replace(day=1)
    else:
        match = re.search(r"(?:last|past|previous)\s+(\d{1,3})\s*(day|week|month)", q)
        if match:
            number = max(1, min(int(match.group(1)), 3660))
            factor = 7 if match.group(2) == "week" else 30 if match.group(2) == "month" else 1
            start = local - timedelta(days=number * factor)
            end = None
        else:
            start = local - timedelta(days=7)
            end = None
    return {
        "start_utc": start.astimezone(timezone.utc).isoformat(),
        "end_utc": end.astimezone(timezone.utc).isoformat() if end else None,
    }


def _question_has_authoritative_time(question: str) -> bool:
    q = question.lower()
    return any(term in q for term in (
        "all time", "ever", "all retained", "today", "yesterday", "this week",
        "last month", "previous month",
    )) or bool(
        re.search(r"(?:last|past|previous)\s+\d{1,3}\s*(?:day|week|month)", q)
        or re.search(r"(?:between|from)\s+\d{4}-\d{2}-\d{2}\s+(?:and|to)\s+\d{4}-\d{2}-\d{2}", q)
        or re.search(r"(?:on|during|for)\s+\d{4}-\d{2}-\d{2}\b", q)
    )


def _query(operation: str, time_range: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    return {"operation": operation, "scope": "events", "filters": [], "time_range": dict(time_range), **kwargs}


def local_plan(question: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Deterministic fallback for common audit wording when Foundry is absent."""
    q = question.lower()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    window = _time_range_from_question(q, current)

    if "assessment" in q and any(word in q for word in ("failed", "failure", "error")):
        return {"unsupported_reason": (
            "The audit schema records completed model-assessment runs but does not record a distinct assessment-failure outcome, so that count cannot be determined reliably."
        )}

    if "how long" in q or "time does" in q or "normally take" in q:
        if any(word in q for word in ("requested vital", "requested information", "supply", "provide")):
            return {"queries": [_query(
                "duration", window, pair_by="case_uid",
                start_filters=[{"field": "event_category", "op": "eq", "value": "information_request"}],
                end_filters=[{"field": "event_category", "op": "eq", "value": "reassessment"}],
            )]}
        if "escalat" in q:
            return {"queries": [_query(
                "duration", window, pair_by="case_uid",
                start_filters=[{"field": "event_category", "op": "eq", "value": "escalation"}],
                end_filters=[{"field": "event_category", "op": "eq", "value": "escalation_resolution"}],
            )]}

    if "assessment" in q and any(word in q for word in ("busiest", "what hour", "which hour")):
        return {"queries": [_query(
            "group_count", window,
            filters=[{"field": "event_category", "op": "eq", "value": "assessment"}],
            group_by="hour_of_day", rank="most", limit=1,
        )]}

    if "reassessment" in q and any(word in q for word in ("which patient", "which case", "most")):
        return {"queries": [_query(
            "group_count", window,
            filters=[{"field": "event_category", "op": "eq", "value": "reassessment"}],
            group_by="case_uid", rank="most", limit=1,
        )]}

    if "escalat" in q and any(word in q for word in ("show", "list", "latest", "last five", "last 5")):
        match = re.search(r"(?:last|latest|show(?: me)?)\s+(\d{1,2})", q)
        limit = int(match.group(1)) if match else 5
        return {"queries": [_query(
            "list", window,
            filters=[{"field": "event_category", "op": "in", "value": [
                "escalation", "escalation_confirmation", "escalation_resolution",
            ]}],
            limit=limit,
        )]}

    if any(word in q for word in ("override", "overrode", "overridden")) and any(word in q for word in ("reason", "reasons", "why")):
        return {"queries": [_query(
            "values", window,
            filters=[
                {"field": "record_kind", "op": "eq", "value": "human_review"},
                {"field": "override_status", "op": "eq", "value": "yes"},
            ],
            value_field="reason", limit=8,
        )]}

    if any(word in q for word in ("override", "overrode", "overridden")) and "acuity" in q and any(word in q for word in ("most", "frequent", "common")):
        return {"queries": [_query(
            "group_count", window,
            filters=[
                {"field": "record_kind", "op": "eq", "value": "human_review"},
                {"field": "override_status", "op": "eq", "value": "yes"},
            ],
            group_by="system_acuity", rank="most", limit=5,
        )]}

    if any(word in q for word in ("increasing", "decreasing", "trend", "week by week", "week-by-week")):
        category = "escalation" if "escalat" in q else "assessment" if "assessment" in q else None
        if category:
            return {"queries": [_query(
                "time_histogram", window,
                filters=[{"field": "event_category", "op": "eq", "value": category}],
                interval="week" if "week" in q else "day", limit=20,
            )]}

    if "percentage" in q or "percent" in q or "proportion" in q or " rate" in q:
        category = (
            "acceptance" if "accept" in q
            else "override" if any(word in q for word in ("override", "overrode", "overridden"))
            else None
        )
        if category:
            return {"queries": [_query(
                "percentage", window,
                numerator_filters=(
                    [
                        {"field": "record_kind", "op": "eq", "value": "human_review"},
                        {"field": "override_status", "op": "eq", "value": "yes"},
                    ]
                    if category == "override"
                    else [{"field": "event_category", "op": "eq", "value": category}]
                ),
                denominator_filters=[{"field": "event_category", "op": "eq", "value": "assessment"}],
                distinct_field="workflow_run_id",
            )]}

    queries: list[dict[str, Any]] = []
    if ("open" in q or "unresolved" in q or "remain" in q) and "escalat" in q:
        queries.append(_query(
            "count", {"start_utc": None, "end_utc": None}, scope="latest_state",
            filters=[
                {"field": "record_kind", "op": "eq", "value": "workflow_state"},
                {"field": "escalation_status", "op": "in", "value": ["requested", "pending"]},
            ], distinct_field="case_uid",
        ))
    if "overdue" in q:
        queries.append(_query(
            "count", {"start_utc": None, "end_utc": None}, scope="latest_state",
            filters=[
                {"field": "record_kind", "op": "eq", "value": "workflow_state"},
                {"field": "overdue_vitals_alert_active", "op": "is_true", "value": True},
            ], distinct_field="case_uid",
        ))
    if queries:
        return {"queries": queries}

    category = None
    if "reassessment" in q or "repeat observation" in q:
        category = "reassessment"
    elif "assessment" in q or "model run" in q or "prediction run" in q:
        category = "assessment"
    elif "access denial" in q or "access deni" in q or "denied access" in q:
        category = "access_denial"
    elif "override" in q or "overrode" in q or "overridden" in q:
        category = "override"
    elif "accept" in q:
        category = "acceptance"
    elif "additional information" in q or "request more information" in q:
        category = "information_request"
    elif "discharg" in q:
        category = "discharge"
    elif "admit" in q or "admission" in q:
        category = "admission"
    elif "escalat" in q:
        category = "escalation_resolution" if any(word in q for word in ("resolved", "closed")) else "escalation"
    elif "audit" in q or "activity" in q or "log" in q:
        category = None
    else:
        return None

    filters = (
        [
            {"field": "record_kind", "op": "eq", "value": "human_review"},
            {"field": "override_status", "op": "eq", "value": "yes"},
        ]
        if category == "override"
        else ([{"field": "event_category", "op": "eq", "value": category}] if category else [])
    )
    group_by = None
    if any(word in q for word in ("who", "staff member", "by staff", "per staff", "each nurse", "by nurse")):
        group_by = "actor_display_name"
    elif any(word in q for word in ("which role", "by role", "per role", "each role")):
        group_by = "reviewer_role"
    elif "by action" in q or "what happened" in q:
        group_by = "event_category"
    elif "by acuity" in q or "which acuity" in q:
        group_by = "decided_acuity"
    operation = "group_count" if group_by else "count"
    # A category word alone is not enough to infer a calculation. Previously
    # "standard deviation of assessment acuity" silently became an assessment
    # count. Local fallback must say it cannot safely plan unfamiliar wording;
    # the Foundry planner can still express any supported aggregate operation.
    supported_intent = any(word in q for word in (
        "how many", "count", "total", "who", "which role", "which staff",
        "staff member", "by staff", "per staff", "each nurse", "by nurse",
        "by role", "per role", "each role", "by action", "what happened",
        "most", "top", "highest", "least", "fewest", "lowest",
        "by acuity", "which acuity",
    ))
    if not supported_intent:
        return None
    distinct = "case_uid" if category == "escalation" and any(word in q for word in ("patient", "patients", "case", "cases")) else None
    kwargs: dict[str, Any] = {"filters": filters}
    if distinct:
        kwargs["distinct_field"] = distinct
    if group_by:
        kwargs.update({
            "group_by": group_by,
            "rank": (
                "most" if any(word in q for word in ("most", "top", "highest"))
                else "least" if any(word in q for word in ("least", "fewest", "lowest"))
                else "all"
            ),
            "limit": 8,
        })
    return {"queries": [_query(operation, window, **kwargs)]}


def _extract_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", _text(text), flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def azure_plan(question: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    """Ask the configured Foundry deployment for a schema-only query plan."""
    try:
        enabled = os.environ.get("ITD_ASSISTANT_LLM_PLANNER_ENABLED")
        if enabled is not None and enabled.lower() != "true":
            return None
        if enabled is None and os.environ.get("AZURE_SUPERVISOR_DEMO_MODE", "").lower() != "true":
            return None
        from app.agents.autogen_team import load_azure_config
        config = load_azure_config()
        if not config:
            return None
        from openai import AzureOpenAI

        current = now or datetime.now(timezone.utc)
        schema = ", ".join(f"{name}:{kind}" for name, kind in FIELD_TYPES.items())
        client = AzureOpenAI(
            azure_endpoint=config["endpoint"], api_key=config["api_key"],
            api_version=config["api_version"],
        )
        completion = client.chat.completions.create(
            model=config["deployment"], temperature=0, max_tokens=900,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate an IT administrator's question into a read-only JSON audit plan. "
                        "Do not answer, calculate, write SQL, invent fields, or replace an unsupported "
                        "measure with a nearby one. Return {queries:[...]}; each query has operation "
                        "count|group_count|percentage|list|time_histogram|average|duration|values, "
                        "scope events|latest_state, filters [{field,op,value}], time_range "
                        "{start_utc,end_utc}, and only relevant fields: group_by, distinct_field, "
                        "value_field, pair_by, limit, rank (most|least|all), interval, numerator_filters, "
                        "denominator_filters, start_filters, end_filters. Filter ops are "
                        "eq|not_eq|in|not_in|contains|exists|is_true|is_false. At most 4 queries, "
                        "10 filters and 20 rows. If the requested fact is not represented by this "
                        "schema, return {unsupported_reason:'...'} rather than another metric. "
                        f"Current UTC time: {current.astimezone(timezone.utc).isoformat()}. Fields: {schema}. "
                        "event_category values include assessment, reassessment, acceptance, override, "
                        "escalation, escalation_resolution, information_request, discharge, admission, "
                        "access_event, access_denial and workflow_state. Use latest_state for current/open status."
                    ),
                },
                {"role": "user", "content": question},
            ],
        )
        return _extract_json(completion.choices[0].message.content or "")
    except Exception:
        return None


def _query_filters(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values: list[Mapping[str, Any]] = []
    for query in plan.get("queries") or []:
        for key in (
            "filters", "numerator_filters", "denominator_filters",
            "start_filters", "end_filters",
        ):
            values.extend(query.get(key) or [])
    return values


def _expected_event_categories(question: str) -> set[str]:
    q = question.lower()
    expected: set[str] = set()
    if "reassessment" in q or "repeat observation" in q:
        expected.add("reassessment")
    if "access" in q and any(word in q for word in ("denial", "denied", "blocked")):
        expected.add("access_denial")
    if "escalat" in q:
        if any(word in q for word in ("resolved", "resolution", "closed")):
            expected.add("escalation_resolution")
        elif any(word in q for word in ("ownership", "confirmed", "confirmation")):
            expected.add("escalation_confirmation")
        else:
            expected.add("escalation")
    if any(word in q for word in ("override", "overrode", "overridden")):
        expected.add("override")
    if "accept" in q:
        expected.add("acceptance")
    if "additional information" in q or "request more information" in q:
        expected.add("information_request")
    if "discharg" in q:
        expected.add("discharge")
    if "admit" in q or "admission" in q:
        expected.add("admission")
    # "reassessment" contains "assessment" but is a distinct recorded event.
    if (
        ("assessment" in q and "reassessment" not in q)
        or "model run" in q
        or "prediction run" in q
    ):
        expected.add("assessment")
    return expected


def _plan_semantically_compatible(question: str, plan: Mapping[str, Any]) -> bool:
    """Reject safe-looking plans that answer a materially different question.

    Structural validation proves only that a plan cannot escape the read-only
    query language. This second check binds explicit event, calculation, group,
    and current-state wording to the plan so Foundry cannot substitute a nearby
    metric and return a confident but wrong figure.
    """
    if plan.get("unsupported_reason"):
        return True
    q = question.lower()
    queries = list(plan.get("queries") or [])
    operations = {str(query.get("operation") or "") for query in queries}
    filters = _query_filters(plan)

    expected_categories = _expected_event_categories(question)
    unsatisfied_categories = set(expected_categories)
    planned_categories: set[str] = set()
    has_override_projection = False
    for item in filters:
        if item.get("field") == "override_status" and item.get("value") == "yes":
            has_override_projection = True
        if item.get("field") != "event_category":
            continue
        value = item.get("value")
        planned_categories.update(
            str(entry) for entry in (value if isinstance(value, list) else [value])
        )
    if "escalation" in unsatisfied_categories and any(
        query.get("scope") == "latest_state" for query in queries
    ) and any(item.get("field") == "escalation_status" for item in filters):
        unsatisfied_categories.discard("escalation")
    if "override" in unsatisfied_categories and has_override_projection:
        unsatisfied_categories.discard("override")
    if expected_categories:
        # The plan must cover every explicitly requested event category and may
        # not add a nearby but unrequested population.  Merely intersecting the
        # requested set allowed {assessment, access_denial} to pass for an
        # assessment-only question and inflate an otherwise plausible answer.
        if not planned_categories.issubset(expected_categories):
            return False
        if not unsatisfied_categories.issubset(planned_categories):
            return False

    expected_operations: set[str] = set()
    if any(word in q for word in ("how long", "time does", "normally take", "duration")):
        expected_operations = {"duration"}
    elif any(word in q for word in ("percentage", "percent", "proportion", " rate")):
        expected_operations = {"percentage"}
    elif any(word in q for word in ("show me", "list ", "latest ", "last five", "last 5")):
        expected_operations = {"list"}
    elif any(word in q for word in ("trend", "increasing", "decreasing", "week by week", "week-by-week")):
        expected_operations = {"time_histogram"}
    elif "average" in q or "mean " in q:
        expected_operations = {"average", "duration"}
    elif any(word in q for word in ("reason", "reasons", "why")) and "override" in q:
        expected_operations = {"values", "list"}
    elif any(word in q for word in ("how many", "count", "total", "who", "which")):
        expected_operations = {"count", "group_count"}
    if expected_operations and operations.isdisjoint(expected_operations):
        return False

    expected_group = None
    if any(word in q for word in ("who", "staff member", "by staff", "per staff", "by nurse", "each nurse")):
        expected_group = "actor_display_name"
    elif any(word in q for word in ("which role", "by role", "per role", "each role")):
        expected_group = "reviewer_role"
    elif any(word in q for word in ("what hour", "which hour", "busiest hour")):
        expected_group = "hour_of_day"
    if expected_group and not any(
        query.get("group_by") == expected_group for query in queries
    ):
        return False

    asks_current = any(word in q for word in ("currently", "current ", "open", "unresolved", "remain", "overdue"))
    if asks_current and not any(query.get("scope") == "latest_state" for query in queries):
        return False
    return True


def plan_question(question: str, *, now: datetime | None = None) -> tuple[dict[str, Any] | None, str]:
    # Deterministic handlers are authoritative for recognised questions. Foundry
    # is a fallback for genuinely new wording, not a higher-priority source that
    # can replace a known assessment question with an unrelated safe metric.
    local = validate_plan(local_plan(question, now=now))
    if local is not None:
        return local, "local_validated"

    raw_azure = azure_plan(question, now=now)
    validated = validate_plan(raw_azure)
    if validated is not None:
        if not _plan_semantically_compatible(question, validated):
            return None, "azure_foundry_semantic_mismatch"
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        deterministic_window = (
            _time_range_from_question(question, current)
            if _question_has_authoritative_time(question)
            else None
        )
        # Date interpretation is server-authoritative. A model-supplied 2020
        # window cannot change "today", and current state must be derived from
        # all retained state before any activity-window filtering.
        for query in validated.get("queries") or []:
            query["time_range"] = (
                {"start_utc": None, "end_utc": None}
                if query.get("scope") == "latest_state"
                else deterministic_window or query["time_range"]
            )
        return validated, "azure_foundry_validated"
    return None, "unsupported"


def _latest_state(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    latest: dict[str, tuple[datetime, Mapping[str, Any]]] = {}
    floor = datetime.min.replace(tzinfo=timezone.utc)
    for record in records:
        if _text(record.get("record_kind")) != "workflow_state":
            continue
        case_uid = _text(record.get("case_uid"))
        if not case_uid:
            continue
        stamp = _parse_dt(record.get("timestamp_utc")) or floor
        if case_uid not in latest or stamp >= latest[case_uid][0]:
            latest[case_uid] = (stamp, record)
    return [value[1] for value in latest.values()]


def _filtered(records: Iterable[Mapping[str, Any]], query: Mapping[str, Any], *, extra_filters: Iterable[Mapping[str, Any]] = ()) -> list[Mapping[str, Any]]:
    source = _latest_state(records) if query.get("scope") == "latest_state" else list(records)
    start = None if query.get("scope") == "latest_state" else _parse_dt((query.get("time_range") or {}).get("start_utc"))
    end = None if query.get("scope") == "latest_state" else _parse_dt((query.get("time_range") or {}).get("end_utc"))
    filters = [*list(query.get("filters") or []), *list(extra_filters)]
    output = []
    for record in source:
        stamp = _parse_dt(record.get("timestamp_utc"))
        if start and (stamp is None or stamp < start):
            continue
        if end and (stamp is None or stamp >= end):
            continue
        if all(_matches(record, item) for item in filters):
            output.append(record)
    return output


def _count_rows(rows: Iterable[Mapping[str, Any]], distinct_field: str | None) -> int:
    values = list(rows)
    if not distinct_field:
        return len(values)
    return len({_text(_value(row, distinct_field)) for row in values if _text(_value(row, distinct_field))})


def _display_group(field: str, value: Any) -> str:
    if value in (None, ""):
        return "Unattributed/unknown"
    if field == "hour_of_day":
        return f"{int(value):02d}:00–{int(value):02d}:59"
    if field in {"system_acuity", "clinician_acuity", "decided_acuity", "acuity"}:
        return f"Acuity {value}"
    if field == "case_uid":
        return f"Case {_text(value)}"
    return _text(value).replace("_", " ")


def _execute_one(records: list[Mapping[str, Any]], query: Mapping[str, Any]) -> dict[str, Any]:
    operation = _text(query.get("operation"))
    rows = _filtered(records, query)
    distinct = query.get("distinct_field")
    result: dict[str, Any] = {"operation": operation, "matched_records": len(rows)}
    if operation == "count":
        result["value"] = _count_rows(rows, distinct)
        result["distinct_field"] = distinct
    elif operation == "group_count":
        field = _text(query.get("group_by"))
        grouped: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            value = _value(row, field)
            key = tuple(value) if isinstance(value, list) else value
            grouped[key].append(row)
        items = [
            {"label": _display_group(field, key), "value": key, "count": _count_rows(values, distinct)}
            for key, values in grouped.items()
        ]
        items.sort(key=lambda item: (-int(item["count"]), str(item["label"])))
        if query.get("rank") == "most" and items:
            highest = items[0]["count"]
            items = [item for item in items if item["count"] == highest]
        elif query.get("rank") == "least" and items:
            lowest = items[-1]["count"]
            items = [item for item in items if item["count"] == lowest]
        result.update({
            "group_by": field,
            "distinct_field": distinct,
            "total": _count_rows(rows, distinct),
            "groups": items[: int(query["limit"])],
        })
    elif operation == "percentage":
        numerator_rows = _filtered(records, query, extra_filters=query.get("numerator_filters") or [])
        denominator_rows = _filtered(records, query, extra_filters=query.get("denominator_filters") or [])
        numerator = _count_rows(numerator_rows, distinct)
        denominator = _count_rows(denominator_rows, distinct)
        result.update({
            "numerator": numerator, "denominator": denominator,
            "percentage": round(numerator * 100.0 / denominator, 1) if denominator else None,
            "distinct_field": distinct,
        })
    elif operation == "list":
        ordered = sorted(rows, key=lambda row: _parse_dt(row.get("timestamp_utc")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        result["items"] = [{
            "timestamp_utc": _text(row.get("timestamp_utc")),
            "action": _text(row.get("decision_type")) or _text(row.get("action_type")),
            "role": _text(row.get("reviewer_role")),
            "staff": _text(_value(row, "actor_display_name")),
            "case_uid": _text(row.get("case_uid")),
            "workflow_run_id": _text(row.get("workflow_run_id")),
        } for row in ordered[: int(query["limit"])]]
    elif operation == "time_histogram":
        interval = _text(query.get("interval") or "day")
        field = {"hour": "hour_of_day", "day": "day", "week": "week", "month": "month"}[interval]
        counts = Counter(_value(row, field) for row in rows if _value(row, field) not in (None, ""))
        groups = [{"label": _display_group(field, key), "value": key, "count": count} for key, count in counts.items()]
        groups.sort(key=lambda item: item["value"])
        result.update({"interval": interval, "groups": groups[-int(query["limit"]):]})
    elif operation == "average":
        field = _text(query.get("value_field"))
        values = []
        for row in rows:
            try:
                value = float(_value(row, field))
                if math.isfinite(value):
                    values.append(value)
            except (TypeError, ValueError):
                continue
        result.update({"value_field": field, "sample_size": len(values), "average": round(sum(values) / len(values), 2) if values else None})
    elif operation == "values":
        field = _text(query.get("value_field"))
        values = [_text(_value(row, field)) for row in rows if _text(_value(row, field))]
        counts = Counter(values)
        result.update({"value_field": field, "values": [{"value": value, "count": count} for value, count in counts.most_common(int(query["limit"]))]})
    elif operation == "duration":
        pair_by = _text(query.get("pair_by"))
        starts = _filtered(records, query, extra_filters=query.get("start_filters") or [])
        ends = _filtered(records, query, extra_filters=query.get("end_filters") or [])
        starts_by_key: dict[str, list[datetime]] = defaultdict(list)
        ends_by_key: dict[str, list[datetime]] = defaultdict(list)
        for row in starts:
            key = _text(_value(row, pair_by)); stamp = _parse_dt(row.get("timestamp_utc"))
            if key and stamp: starts_by_key[key].append(stamp)
        for row in ends:
            key = _text(_value(row, pair_by)); stamp = _parse_dt(row.get("timestamp_utc"))
            if key and stamp: ends_by_key[key].append(stamp)
        durations = []
        for key, start_values in starts_by_key.items():
            available_ends = sorted(ends_by_key.get(key, []))
            for start in sorted(start_values):
                end_index = next(
                    (index for index, candidate in enumerate(available_ends) if candidate >= start),
                    None,
                )
                end = available_ends.pop(end_index) if end_index is not None else None
                if end:
                    durations.append((end - start).total_seconds() / 60.0)
        result.update({
            "pair_by": pair_by, "sample_size": len(durations),
            "average_minutes": round(sum(durations) / len(durations), 1) if durations else None,
            "median_minutes": round(median(durations), 1) if durations else None,
        })
    return result


def _subject(query: Mapping[str, Any]) -> str:
    for item in query.get("filters") or []:
        if item.get("field") == "event_category" and item.get("op") == "eq":
            return EVENT_LABELS.get(_text(item.get("value")), _text(item.get("value")))
        if item.get("field") == "override_status" and item.get("value") == "yes":
            return "clinician override"
    if query.get("scope") == "latest_state":
        if any(item.get("field") == "overdue_vitals_alert_active" for item in query.get("filters") or []):
            return "currently active overdue-observation alert"
        return "current workflow case"
    return "audit event"


def _render_one(query: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    operation = result["operation"]
    subject = _subject(query)
    if operation == "count":
        value = int(result.get("value") or 0)
        if result.get("distinct_field") == "case_uid":
            return f"{value} unique patient case{'s' if value != 1 else ''} matched the recorded {subject} evidence."
        return f"{value} {subject}{'' if value == 1 else 's'} matched the recorded audit evidence."
    if operation == "group_count":
        groups = result.get("groups") or []
        total = int(result.get("total") or 0)
        if not groups:
            if result.get("distinct_field") == "case_uid":
                return (
                    f"0 unique patient cases matched the recorded {subject} evidence. "
                    "No attributed staff or role group was recorded."
                )
            return f"0 recorded {subject}s matched; no attributed group was recorded."
        detail = ", ".join(f"{item['label']} ({item['count']})" for item in groups)
        if query.get("rank") in {"most", "least"}:
            total_label = (
                f"{total} unique patient case{'s' if total != 1 else ''} matched"
                if result.get("distinct_field") == "case_uid"
                else f"{total} recorded {subject}{'' if total == 1 else 's'} matched"
            )
            rank_label = "highest" if query.get("rank") == "most" else "lowest"
            return (
                f"{total_label}. The {rank_label} attributed count was {groups[0]['count']}: "
                f"{', '.join(item['label'] for item in groups)}."
            )
        return f"{total} recorded {subject}{'' if total == 1 else 's'} grouped as: {detail}."
    if operation == "percentage":
        denominator = int(result.get("denominator") or 0)
        if denominator == 0:
            return "The percentage cannot be calculated because the recorded denominator is zero."
        return f"{result['percentage']:.1f}% ({result['numerator']} of {denominator}) matched the numerator conditions in the recorded audit evidence."
    if operation == "list":
        items = result.get("items") or []
        if not items:
            return f"No matching {subject} was recorded."
        rendered = []
        for item in items:
            details = [item.get("timestamp_utc"), item.get("action")]
            if item.get("staff"): details.append(f"staff {item['staff']}")
            elif item.get("role"): details.append(f"role {item['role']}")
            if item.get("case_uid"): details.append(f"case {item['case_uid']}")
            rendered.append(" — ".join(str(value) for value in details if value))
        return f"The latest {len(items)} matching audit event{'s' if len(items) != 1 else ''}: " + "; ".join(rendered) + "."
    if operation == "time_histogram":
        groups = result.get("groups") or []
        if not groups:
            return f"No matching {subject} was recorded for the requested trend."
        detail = ", ".join(f"{item['label']} ({item['count']})" for item in groups)
        return f"Recorded {subject} counts by {result['interval']}: {detail}."
    if operation == "average":
        if result.get("average") is None:
            return "No numeric audit values were recorded for that calculation."
        return f"The recorded average {result['value_field'].replace('_', ' ')} was {result['average']} across {result['sample_size']} event(s)."
    if operation == "values":
        values = result.get("values") or []
        if not values:
            return "No recorded reason/comment was available for the matching events."
        return "The most frequent recorded values were: " + "; ".join(f"{item['value']} ({item['count']})" for item in values) + "."
    if operation == "duration":
        if not result.get("sample_size"):
            return "No complete start/end event pair was recorded for that duration calculation."
        return f"Across {result['sample_size']} complete recorded pair(s), the median was {result['median_minutes']} minutes and the average was {result['average_minutes']} minutes."
    return "The validated audit query completed."


def answer_general_audit_question(
    question: str,
    records: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Plan, validate, execute and render a natural-language audit question."""
    plan, planner = plan_question(question, now=now)
    if plan is None:
        return None
    if plan.get("unsupported_reason"):
        return {
            "answer": plan["unsupported_reason"],
            "planner": planner,
            "plan": {"queries": []},
            "results": [],
            "supported": False,
        }
    rows = list(records)
    results = [_execute_one(rows, query) for query in plan["queries"]]
    answer = " ".join(_render_one(query, result) for query, result in zip(plan["queries"], results))
    return {
        "answer": answer,
        "planner": planner,
        "plan": plan,
        "results": results,
        "supported": True,
    }
