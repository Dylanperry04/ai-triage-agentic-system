"""Idempotent, revision-aware monthly UHL retraining-data export.

The boundary deliberately ends at a clean CSV and an ITD notification. Nothing
in this module trains a model, connects to NVIDIA infrastructure, submits a
Slurm job, or promotes an artefact.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.config import settings
from app.constants import DATASET_SOURCE, MODEL_INPUT_COLUMNS
from app.rules.acuity_mts_mapping import acuity_from_text


DUBLIN = ZoneInfo("Europe/Dublin")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
EXPORT_COLUMNS = [
    "workflow_run_id", "case_uid", "assessment_date",
    *list(MODEL_INPUT_COLUMNS),
    "system_prediction", "final_clinician_acuity", "review_status",
    "override_reason", "review_comment", "reviewer_role", "label_source",
    "model_version", "model_sha256",
]
FINAL_REVIEW_STATUSES = {
    "ACCEPTED_AS_PRESENTED", "OVERRIDDEN", "ESCALATION_RESOLVED",
}


class MonthlyExportError(RuntimeError):
    """Raised when an export cannot be generated safely."""


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value or {})


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _month_bounds(reporting_month: str) -> tuple[datetime, datetime]:
    if not MONTH_RE.fullmatch(str(reporting_month or "")):
        raise ValueError("reporting_month must use YYYY-MM")
    year, month = (int(part) for part in reporting_month.split("-"))
    start = datetime(year, month, 1, tzinfo=DUBLIN)
    end = (
        datetime(year + 1, 1, 1, tzinfo=DUBLIN)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=DUBLIN)
    )
    return start, end


def previous_reporting_month(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(DUBLIN)
    year, month = local.year, local.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def current_reporting_month(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(DUBLIN)
    return f"{local.year:04d}-{local.month:02d}"


def _require_completed_month(reporting_month: str, now: datetime | None = None) -> None:
    """Refuse a partial/current or future month.

    The label population can still change until the Dublin calendar month has
    closed. The separate demo-preview path can show current-month evidence, but
    an official artifact must retain a complete calendar-month cohort.
    """
    _start, end = _month_bounds(reporting_month)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if current.astimezone(DUBLIN) < end:
        raise MonthlyExportError("reporting month has not completed in Europe/Dublin")


def export_directory() -> Path:
    raw = os.environ.get("MONTHLY_RETRAINING_EXPORT_DIR", "").strip()
    return Path(raw).expanduser() if raw else settings.processed_dir / "monthly_retraining"


def _safe_records(records: Iterable[Any]) -> list[dict[str, Any]]:
    from app.security.redaction import assert_no_raw_identifiers, redact_for_log
    out = []
    for value in records:
        safe = redact_for_log(_as_dict(value))
        assert_no_raw_identifiers(safe)
        out.append(safe)
    return out


def _load_source_records(
    reporting_month: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the same append-only evidence used by audit views."""
    if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true":
        from app.api.status_routes import _read_patient_durable_records
        from app.security.audit_sink import _max_audit_read_limit

        configured = max(
            1000,
            min(int(os.environ.get("MONTHLY_EXPORT_READ_LIMIT", "100000")), 100000),
        )
        # The sink applies an independently configured ceiling. Use the effective
        # value so hitting that ceiling is detectable instead of silently
        # finalising a truncated month.
        limit = min(configured, _max_audit_read_limit())
        start_local, _end_local = _month_bounds(reporting_month)
        since_utc = start_local.astimezone(timezone.utc).isoformat()
        runs = _read_patient_durable_records(
            record_kind="workflow_run",
            limit=limit,
            since_utc=since_utc,
        )
        reviews = _read_patient_durable_records(
            record_kind="human_review",
            limit=limit,
            since_utc=since_utc,
        )
        if len(runs) >= limit or len(reviews) >= limit:
            raise MonthlyExportError(
                "monthly source read reached its configured durable-audit cap; "
                "refusing to create a potentially incomplete export"
            )
        return runs, reviews

    from app.security.local_paths import credentialed_artifact_path
    from app.storage.human_review_repository import read_human_reviews
    from app.storage.workflow_run_repository import read_workflow_runs

    run_path = credentialed_artifact_path(
        settings.processed_dir / "workflow_runs.jsonl",
        purpose="monthly retraining workflow-run read",
    )
    review_path = credentialed_artifact_path(
        settings.processed_dir / "human_reviews.jsonl",
        purpose="monthly retraining clinician-review read",
    )
    return (
        _safe_records(read_workflow_runs(run_path)),
        _safe_records(read_human_reviews(review_path)),
    )


def _valid_model_inputs(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    missing = [name for name in MODEL_INPUT_COLUMNS if snapshot.get(name) in (None, "")]
    if missing:
        return None, "missing_model_features"
    row = {name: snapshot.get(name) for name in MODEL_INPUT_COLUMNS}
    if not str(row["presenting_complaint"]).strip():
        return None, "invalid_presenting_complaint"
    numeric = {
        "age", "month", "hour", "temperature", "heartrate", "resprate",
        "o2sat", "sbp", "dbp", "pain",
    }
    try:
        for name in numeric:
            value = float(row[name])
            if not math.isfinite(value):
                raise ValueError(name)
    except (TypeError, ValueError):
        return None, "invalid_model_feature"
    if int(float(row["month"])) not in range(1, 13) or int(float(row["hour"])) not in range(24):
        return None, "invalid_calendar_feature"
    return row, None


def build_export_dataset(
    reporting_month: str,
    workflow_runs: Iterable[Any],
    human_reviews: Iterable[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return clean rows and reconciliation metadata without performing I/O."""
    start_local, end_local = _month_bounds(reporting_month)
    duplicate_run_ids = 0
    runs_by_id: dict[str, dict[str, Any]] = {}
    for raw in workflow_runs:
        run = _as_dict(raw)
        run_id = str(run.get("workflow_run_id") or "").strip()
        stamp = _parse_timestamp(run.get("timestamp_utc"))
        if not run_id or stamp is None:
            continue
        local = stamp.astimezone(DUBLIN)
        if not (start_local <= local < end_local):
            continue
        if run_id in runs_by_id:
            duplicate_run_ids += 1
            # Identical identifiers are one assessment. Keep the latest stored
            # representation; downstream validation still rejects incomplete data.
        runs_by_id[run_id] = run

    reviews_by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in human_reviews:
        review = _as_dict(raw)
        run_id = str(review.get("workflow_run_id") or "").strip()
        if run_id in runs_by_id:
            reviews_by_run[run_id].append(review)
    for rows in reviews_by_run.values():
        rows.sort(key=lambda r: _parse_timestamp(r.get("created_at_utc")) or datetime.min.replace(tzinfo=timezone.utc))

    clean_rows: list[dict[str, Any]] = []
    excluded = Counter()
    accepted = overrides = doctor_final = 0
    for run_id, run in sorted(
        runs_by_id.items(), key=lambda item: str(item[1].get("timestamp_utc") or "")
    ):
        linked = reviews_by_run.get(run_id) or []
        if not linked:
            excluded["no_linked_review"] += 1
            continue
        # Discharge/admission is a later disposition, not a new label. Ignore
        # those terminal administrative rows when locating the current acuity
        # outcome, but do not ignore request-info/escalation rows: those make the
        # assessment unresolved until a later final clinical outcome exists.
        label_reviews = [
            review for review in linked
            if str(review.get("review_status") or "").upper()
            not in {"DISCHARGED", "CASE_CLOSED"}
        ]
        if not label_reviews:
            excluded["no_clinical_label_review"] += 1
            continue
        latest = label_reviews[-1]
        if str(latest.get("case_uid") or "") != str(run.get("case_uid") or ""):
            excluded["case_link_mismatch"] += 1
            continue
        if not str(run.get("case_uid") or "").strip():
            excluded["missing_case_uid"] += 1
            continue
        if str(run.get("source_dataset") or "") != DATASET_SOURCE:
            excluded["not_active_uhl_dataset"] += 1
            continue
        if list(run.get("input_schema") or []) != list(MODEL_INPUT_COLUMNS):
            excluded["unexpected_model_input_schema"] += 1
            continue
        status = str(latest.get("review_status") or "").upper()
        if status not in FINAL_REVIEW_STATUSES:
            excluded["unresolved_or_nonfinal"] += 1
            continue
        inputs, input_error = _valid_model_inputs(run.get("input_snapshot") or {})
        if input_error:
            excluded[input_error] += 1
            continue
        system_acuity = acuity_from_text(
            run.get("final_acuity") if run.get("final_acuity") is not None
            else run.get("predicted_mimic_acuity")
        )
        if system_acuity not in {1, 2, 3, 4, 5}:
            excluded["invalid_system_prediction"] += 1
            continue
        if not str(run.get("model_version") or "").strip() or not str(run.get("model_sha256") or "").strip():
            excluded["missing_model_provenance"] += 1
            continue

        if status == "ACCEPTED_AS_PRESENTED":
            if str(latest.get("reviewer_role") or "") not in {"triage_nurse", "security_admin"}:
                excluded["accepted_by_wrong_role"] += 1
                continue
            final_acuity = system_acuity
            label_source = "accepted_model_recommendation"
            accepted += 1
        else:
            final_acuity = acuity_from_text(latest.get("final_clinician_acuity"))
            if final_acuity not in {1, 2, 3, 4, 5}:
                excluded["missing_final_clinician_acuity"] += 1
                continue
            if status == "OVERRIDDEN":
                if str(latest.get("reviewer_role") or "") not in {"triage_nurse", "security_admin"}:
                    excluded["override_by_wrong_role"] += 1
                    continue
                label_source = "clinician_override"
                overrides += 1
            else:
                reviewer_role = str(latest.get("reviewer_role") or "")
                if reviewer_role not in {"ed_doctor", "security_admin"}:
                    excluded["final_escalation_not_by_ed_doctor"] += 1
                    continue
                doctor_changed_model = final_acuity != system_acuity
                label_source = (
                    "ed_doctor_final_escalation_override"
                    if doctor_changed_model
                    else "ed_doctor_final_escalation_confirmed"
                )
                if doctor_changed_model:
                    overrides += 1
                doctor_final += 1

        clean_rows.append({
            "workflow_run_id": run_id,
            "case_uid": str(run.get("case_uid") or ""),
            "assessment_date": str(run.get("timestamp_utc") or ""),
            **inputs,
            "system_prediction": system_acuity,
            "final_clinician_acuity": final_acuity,
            "review_status": status,
            "override_reason": str(latest.get("override_reason") or ""),
            "review_comment": str(latest.get("review_comment") or ""),
            "reviewer_role": str(latest.get("reviewer_role") or ""),
            "label_source": label_source,
            "model_version": str(run.get("model_version") or ""),
            "model_sha256": str(run.get("model_sha256") or ""),
        })

    # Defensive final de-duplication at the exact CSV identity boundary.
    deduped = {row["workflow_run_id"]: row for row in clean_rows}
    rows = [deduped[key] for key in sorted(deduped, key=lambda key: deduped[key]["assessment_date"])]
    metadata = {
        "reporting_month": reporting_month,
        "assessment_count": len(runs_by_id),
        "eligible_cases": len(rows),
        "accepted_cases": accepted,
        "overrides": overrides,
        "doctor_final_escalations": doctor_final,
        "excluded_unresolved_cases": sum(excluded.values()),
        "exclusion_reasons": dict(sorted(excluded.items())),
        "duplicate_source_run_ids_removed": duplicate_run_ids + (len(clean_rows) - len(rows)),
    }
    return rows, metadata


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _spreadsheet_safe_text(value: Any) -> Any:
    """Neutralise formula execution when the CSV is opened in Excel/Sheets.

    Quoting a CSV cell does not stop spreadsheet formula evaluation. Prefixing
    an apostrophe is the interoperable display-safe form. Numeric values are
    untouched; string model features retain their text apart from malicious
    formula-leading input, which must never execute during ITD review.
    """
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows([
        {key: _spreadsheet_safe_text(value) for key, value in row.items()}
        for row in rows
    ])
    return buffer.getvalue().encode("utf-8-sig")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(delete=False, dir=path.parent, prefix=f".{path.name}.")
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


@contextmanager
def _generation_lock(directory: Path, reporting_month: str):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / f".{reporting_month}.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # A crashed worker must not block exports forever. A live generation is
        # expected to complete in seconds; 30 minutes is a deliberately wide cap.
        if time.time() - lock.stat().st_mtime > 1800:
            lock.unlink(missing_ok=True)
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            raise MonthlyExportError("monthly export generation is already in progress")
    try:
        os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
        os.close(fd)
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        lock.unlink(missing_ok=True)


def _paths(reporting_month: str, directory: Path | None = None) -> tuple[Path, Path]:
    root = directory or export_directory()
    suffix = reporting_month.replace("-", "_")
    return (
        root / f"monthly_retraining_{suffix}.csv",
        root / f"monthly_retraining_{suffix}.json",
    )


def read_manifest(reporting_month: str, directory: Path | None = None) -> dict[str, Any] | None:
    _month_bounds(reporting_month)
    _, manifest_path = _paths(reporting_month, directory)
    if not manifest_path.is_file():
        return None
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def validate_export_artifact(
    reporting_month: str,
    directory: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Validate an artifact immediately before it is offered for download."""
    manifest = read_manifest(reporting_month, directory)
    csv_path, _ = _paths(reporting_month, directory)
    if manifest is None or not csv_path.is_file():
        raise MonthlyExportError("monthly export artifact is missing")
    try:
        digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise MonthlyExportError("monthly export artifact could not be read") from exc
    if not str(manifest.get("sha256") or "") or digest != manifest.get("sha256"):
        raise MonthlyExportError("monthly export checksum verification failed")
    return manifest, csv_path


def list_manifests(directory: Path | None = None) -> list[dict[str, Any]]:
    root = directory or export_directory()
    if not root.exists():
        return []
    output = []
    for path in root.glob("monthly_retraining_????_??.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and MONTH_RE.fullmatch(str(item.get("reporting_month") or "")):
                output.append(item)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(output, key=lambda item: item["reporting_month"], reverse=True)


def _notify_ready(manifest: Mapping[str, Any]) -> str:
    from app.notifications.config import NotificationSettings
    from app.notifications.repository import get_notification_repository
    from app.notifications.service import create_notification_for_event

    config = NotificationSettings.from_env()
    repository = get_notification_repository(config)
    month = str(manifest["reporting_month"])
    body = (
        f"Reporting month {month}, revision {manifest.get('revision', 1)}: "
        f"{manifest['eligible_cases']} eligible training "
        f"record(s), {manifest['excluded_unresolved_cases']} excluded/unresolved. "
        "Open the ITD Console and select Download Monthly Retraining Data. "
        "No training has been started."
    )
    record, _created = create_notification_for_event(
        repository=repository,
        settings=config,
        kind="monthly_retraining",
        case_uid=f"SYSTEM_MONTHLY_RETRAINING_{month.replace('-', '_')}",
        event_key=str(manifest["generated_at_utc"]),
        target_role="security_admin",
        created_at=str(manifest["generated_at_utc"]),
        body_override=body,
        sms_allowed=False,
    )
    return record.notification_id


def generate_monthly_export(
    reporting_month: str,
    *,
    now: datetime | None = None,
    workflow_runs: Iterable[Any] | None = None,
    human_reviews: Iterable[Any] | None = None,
    directory: Path | None = None,
) -> dict[str, Any]:
    """Create or reconcile one completed-month artifact.

    A run unresolved at the first-of-month boundary is correctly excluded at
    that time. If its final linked clinician label arrives later, a subsequent
    reconciliation atomically publishes a new revision instead of excluding the
    assessment forever. Identical source evidence remains byte-for-byte and
    notification-idempotent.
    """
    _require_completed_month(reporting_month, now)
    root = directory or export_directory()
    csv_path, manifest_path = _paths(reporting_month, root)
    with _generation_lock(root, reporting_month):
        existing = read_manifest(reporting_month, root)
        if workflow_runs is None or human_reviews is None:
            loaded_runs, loaded_reviews = _load_source_records(reporting_month)
            workflow_runs = loaded_runs if workflow_runs is None else workflow_runs
            human_reviews = loaded_reviews if human_reviews is None else human_reviews
        rows, counts = build_export_dataset(reporting_month, workflow_runs, human_reviews)
        csv_content = _csv_bytes(rows)
        csv_sha256 = hashlib.sha256(csv_content).hexdigest()
        generated = now or datetime.now(timezone.utc)
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        count_keys = set(counts)
        unchanged = bool(
            existing
            and csv_path.is_file()
            and existing.get("sha256") == csv_sha256
            and all(existing.get(key) == counts.get(key) for key in count_keys)
        )
        if unchanged:
            manifest = dict(existing)
        else:
            revision = int((existing or {}).get("revision") or 0) + 1
            generated_utc = generated.astimezone(timezone.utc).isoformat()
            manifest = {
                **counts,
                "generated_at_utc": generated_utc,
                "initial_generated_at_utc": (
                    (existing or {}).get("initial_generated_at_utc")
                    or (existing or {}).get("generated_at_utc")
                    or generated_utc
                ),
                "revised_at_utc": generated_utc if revision > 1 else None,
                "revision": revision,
                "supersedes_sha256": (
                    (existing or {}).get("sha256") if revision > 1 else None
                ),
                "filename": csv_path.name,
                "download_status": "ready",
                "sha256": csv_sha256,
                "columns": EXPORT_COLUMNS,
                "model_input_columns": list(MODEL_INPUT_COLUMNS),
                "csv_formula_protection": "apostrophe_prefix_for_formula_leading_text",
                "automatic_training_triggered": False,
            }
            _atomic_write(csv_path, csv_content)
            _atomic_write(
                manifest_path,
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            )
        # Keep notification reconciliation and its manifest update inside the
        # same per-month lock. Otherwise two workers can publish successive
        # revisions and the first worker can subsequently overwrite the newer
        # manifest with stale notification state.
        try:
            notification_id = _notify_ready(manifest)
            manifest = {
                **manifest,
                "notification_status": "created",
                "notification_id": notification_id,
            }
            _atomic_write(
                manifest_path,
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            )
        except Exception as exc:
            manifest = {
                **manifest,
                "notification_status": "retry_pending",
                "notification_error": type(exc).__name__,
            }
            _atomic_write(
                manifest_path,
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            )
    return manifest


def ensure_previous_month_export(now: datetime | None = None) -> dict[str, Any]:
    return generate_monthly_export(previous_reporting_month(now), now=now)


def reconcile_completed_month_exports(
    now: datetime | None = None,
    *,
    directory: Path | None = None,
) -> dict[str, Any]:
    """Reconcile the previous month and every retained official manifest.

    A final label can arrive more than one month late. Revisiting only the
    immediately previous month would still leave such a run permanently absent
    unless ITD manually regenerated its month. Existing official cohorts are
    therefore revision-checked on each scheduled pass. The bounded manifest
    count protects startup from an unexpectedly unbounded directory while the
    default covers ten years of monthly demo/research artifacts.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    root = directory or export_directory()
    try:
        maximum = max(
            1,
            min(int(os.environ.get("MONTHLY_RETRAINING_RECONCILE_MAX_MONTHS", "120")), 1200),
        )
    except ValueError:
        maximum = 120
    previous = previous_reporting_month(current)
    months = [previous]
    months.extend(
        str(item.get("reporting_month") or "")
        for item in list_manifests(root)
        if str(item.get("reporting_month") or "") != previous
    )
    all_months = [month for month in dict.fromkeys(months) if MONTH_RE.fullmatch(month)]
    truncated = len(all_months) > maximum
    months = all_months[:maximum]
    manifests: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for month in months:
        try:
            manifests.append(
                generate_monthly_export(month, now=current, directory=root)
            )
        except Exception as exc:
            failures.append({"reporting_month": month, "error": type(exc).__name__})
    previous_manifest = next(
        (item for item in manifests if item.get("reporting_month") == previous),
        None,
    )
    if previous_manifest is None:
        raise MonthlyExportError(
            "previous-month export reconciliation failed: "
            + (failures[0]["error"] if failures else "unknown")
        )
    return {
        "previous_month_manifest": previous_manifest,
        "reconciled_months": [item.get("reporting_month") for item in manifests],
        "reconciled_count": len(manifests),
        "failures": failures,
        "truncated": truncated,
    }


def build_current_month_demo_preview(
    *,
    now: datetime | None = None,
    workflow_runs: Iterable[Any] | None = None,
    human_reviews: Iterable[Any] | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Build a non-persisted current-month preview for the role-switcher demo.

    This deliberately bypasses only the *completed month* check. It writes no
    artifact, sends no notification and never represents itself as the official
    monthly dataset. Production/patient-data routes do not expose it.
    """
    current = now or datetime.now(timezone.utc)
    month = current_reporting_month(current)
    if workflow_runs is None or human_reviews is None:
        loaded_runs, loaded_reviews = _load_source_records(month)
        workflow_runs = loaded_runs if workflow_runs is None else workflow_runs
        human_reviews = loaded_reviews if human_reviews is None else human_reviews
    rows, counts = build_export_dataset(month, workflow_runs, human_reviews)
    content = _csv_bytes(rows)
    stamp = current if current.tzinfo else current.replace(tzinfo=timezone.utc)
    manifest = {
        **counts,
        "generated_at_utc": stamp.astimezone(timezone.utc).isoformat(),
        "filename": f"demo_preview_retraining_{month.replace('-', '_')}.csv",
        "download_status": "demo_preview_ready",
        "sha256": hashlib.sha256(content).hexdigest(),
        "columns": EXPORT_COLUMNS,
        "model_input_columns": list(MODEL_INPUT_COLUMNS),
        "demo_preview": True,
        "official_monthly_artifact": False,
        "persisted": False,
        "notification_sent": False,
        "automatic_training_triggered": False,
    }
    return manifest, content


def csv_path_for(reporting_month: str, directory: Path | None = None) -> Path:
    path, _ = _paths(reporting_month, directory)
    return path
