"""Complete, redacted patient-journey CSV export for the Audit Log UI.

The dashboard intentionally returns a compact presentation shape.  This module
builds a separate evidence export: one row per recorded audit event, ordered by
pseudonymous case and time, with the current observation state carried forward,
explicit previous/new/delta vital columns, every normalised field, and every
field from the already-redacted source record.
"""
from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from app.constants import MODEL_INPUT_COLUMNS
from app.security.redaction import assert_no_raw_identifiers


VITAL_FIELDS = (
    "temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain",
)

CORE_COLUMNS = [
    "export_row_number", "case_uid", "display_identifier",
    "patient_display_name", "patient_display_label", "encounter_display_label",
    "journey_sequence", "journey_event_count", "timestamp_utc", "date",
    "record_kind", "action_type", "decision_type", "summary",
    "actor_user_id", "actor_display_name", "actor_identity_verified",
    "actor_type", "reviewer_role", "reviewer_roles", "source_dataset",
    "workflow_run_id", "review_id", "rerun_id", "action_id",
    "case_status", "escalation_status", "override_status",
    "triage_level", "acuity", "system_acuity", "clinician_acuity",
    "previous_acuity", "new_acuity", "final_clinician_acuity",
    "system_prediction", "clinician_decision", "review_status",
    "review_comment", "override_reason", "reason",
    *list(MODEL_INPUT_COLUMNS),
    *[
        column
        for field in VITAL_FIELDS
        for column in (
            f"{field}_previous", f"{field}_new", f"{field}_delta",
            f"{field}_changed",
        )
    ],
    "changed_fields", "changed_vitals", "model_version", "model_sha256",
    "app_version", "package_checkpoint", "normalised_record_json",
    "source_record_json",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_EVENT_PRIORITY = {
    # When an observation update and automatic reassessment share a timestamp,
    # show the nursing observation event before the resulting model run.
    "workflow_rerun": 10,
    "workflow_run": 20,
    "human_review": 30,
    "workflow_state": 40,
    "access_event": 50,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        value = _json(value)
    if isinstance(value, str) and value.lstrip().startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _flatten(prefix: str, value: Any, output: dict[str, Any]) -> None:
    """Flatten dictionaries while keeping lists as lossless JSON cells."""
    if isinstance(value, Mapping):
        if not value:
            output[prefix] = "{}"
            return
        for key in sorted(value, key=lambda item: str(item)):
            child = f"{prefix}.{key}" if prefix else str(key)
            _flatten(child, value[key], output)
        return
    output[prefix] = _json(value) if isinstance(value, (list, tuple)) else value


def _source(record: Mapping[str, Any]) -> dict[str, Any]:
    value = record.get("source_record")
    return dict(value) if isinstance(value, Mapping) else {}


def _event_changes(record: Mapping[str, Any]) -> dict[str, tuple[Any, Any]]:
    source = _source(record)
    raw = source.get("changed_vitals") or record.get("changed_vitals") or []
    changes: dict[str, tuple[Any, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        field = _text(item.get("field"))
        if field in VITAL_FIELDS:
            changes[field] = (item.get("previous"), item.get("new"))
    return changes


def _numeric_delta(previous: Any, new: Any) -> float | None:
    if previous in (None, "") or new in (None, ""):
        return None
    try:
        return round(float(new) - float(previous), 6)
    except (TypeError, ValueError):
        return None


def build_audit_journey_rows(
    records: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return chronologically ordered, complete safe audit rows and columns."""
    safe_records = [dict(record) for record in records]
    for record in safe_records:
        assert_no_raw_identifiers(record)

    case_counts = Counter(
        _text(record.get("case_uid"))
        for record in safe_records
        if _text(record.get("case_uid"))
    )
    indexed = list(enumerate(safe_records))
    indexed.sort(key=lambda item: (
        1 if not _text(item[1].get("case_uid")) else 0,
        _text(item[1].get("case_uid")),
        float(item[1].get("timestamp_epoch") or 0),
        _EVENT_PRIORITY.get(_text(item[1].get("record_kind")), 99),
        item[0],
    ))

    current_inputs: dict[str, dict[str, Any]] = defaultdict(dict)
    journey_sequence: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    dynamic_columns: set[str] = set()

    for _original_index, record in indexed:
        source = _source(record)
        case_uid = _text(record.get("case_uid"))
        state = current_inputs[case_uid] if case_uid else {}
        snapshot = source.get("input_snapshot")
        if isinstance(snapshot, Mapping):
            for field in MODEL_INPUT_COLUMNS:
                if field in snapshot and snapshot[field] not in (None, ""):
                    state[field] = snapshot[field]

        # A rerun is the observation event. Apply each explicit previous/new
        # pair so non-changing vitals can still be carried forward on later rows.
        changes = _event_changes(record)
        for field, (_previous, new) in changes.items():
            if new not in (None, ""):
                state[field] = new

        new_complaint = source.get("new_chiefcomplaint")
        if new_complaint not in (None, ""):
            state["presenting_complaint"] = new_complaint

        if case_uid:
            journey_sequence[case_uid] += 1
        normalised = {
            key: value for key, value in record.items()
            if key not in {"source_record", "timestamp_epoch"}
        }
        row: dict[str, Any] = {
            "case_uid": case_uid,
            "display_identifier": record.get("display_identifier"),
            "patient_display_name": record.get("patient_display_name"),
            "patient_display_label": record.get("patient_display_label"),
            "encounter_display_label": record.get("encounter_display_label"),
            "journey_sequence": journey_sequence[case_uid] if case_uid else "",
            "journey_event_count": case_counts.get(case_uid, "") if case_uid else "",
            "timestamp_utc": record.get("timestamp_utc"),
            "date": record.get("date"),
            "record_kind": record.get("record_kind"),
            "action_type": record.get("action_type"),
            "decision_type": record.get("decision_type"),
            "summary": record.get("summary"),
            "actor_user_id": record.get("actor_user_id"),
            "actor_display_name": record.get("actor_display_name"),
            "actor_identity_verified": record.get("actor_identity_verified"),
            "actor_type": record.get("actor_type"),
            "reviewer_role": record.get("reviewer_role"),
            "reviewer_roles": record.get("reviewer_roles"),
            "source_dataset": record.get("source_dataset"),
            "workflow_run_id": record.get("workflow_run_id") or source.get("workflow_run_id"),
            "review_id": source.get("review_id"),
            "rerun_id": source.get("rerun_id"),
            "action_id": source.get("action_id"),
            "case_status": record.get("case_status") or source.get("case_status"),
            "escalation_status": record.get("escalation_status"),
            "override_status": record.get("override_status"),
            "triage_level": record.get("triage_level"),
            "acuity": record.get("acuity"),
            "system_acuity": record.get("system_acuity"),
            "clinician_acuity": record.get("clinician_acuity"),
            "previous_acuity": record.get("previous_acuity"),
            "new_acuity": record.get("new_acuity"),
            "final_clinician_acuity": (
                record.get("final_clinician_acuity")
                or source.get("final_clinician_acuity")
            ),
            "system_prediction": source.get("system_prediction") or source.get("final_acuity"),
            "clinician_decision": source.get("clinician_decision"),
            "review_status": source.get("review_status"),
            "review_comment": record.get("review_comment") or source.get("review_comment"),
            "override_reason": record.get("override_reason") or source.get("override_reason"),
            "reason": source.get("reason") or source.get("escalation_reason"),
            "changed_fields": record.get("changed_fields") or source.get("changed_fields"),
            "changed_vitals": record.get("changed_vitals") or source.get("changed_vitals"),
            "model_version": source.get("model_version") or source.get("latest_model_version"),
            "model_sha256": source.get("model_sha256") or source.get("latest_model_sha256"),
            "app_version": source.get("app_version"),
            "package_checkpoint": source.get("package_checkpoint"),
            "normalised_record_json": _json(normalised),
            "source_record_json": _json(source),
        }
        for field in MODEL_INPUT_COLUMNS:
            row[field] = state.get(field, "")
        for field in VITAL_FIELDS:
            previous, new = changes.get(field, (None, None))
            row[f"{field}_previous"] = previous
            row[f"{field}_new"] = new
            row[f"{field}_delta"] = _numeric_delta(previous, new)
            row[f"{field}_changed"] = field in changes

        flattened: dict[str, Any] = {}
        _flatten("normalised", normalised, flattened)
        _flatten("source", source, flattened)
        row.update(flattened)
        dynamic_columns.update(flattened)
        rows.append(row)

    for index, row in enumerate(rows, start=1):
        row["export_row_number"] = index
    columns = [*CORE_COLUMNS, *sorted(dynamic_columns - set(CORE_COLUMNS))]
    return rows, columns


def audit_journey_csv_bytes(records: Iterable[Mapping[str, Any]]) -> bytes:
    rows, columns = build_audit_journey_rows(records)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(value) for key, value in row.items()})
    return buffer.getvalue().encode("utf-8-sig")
