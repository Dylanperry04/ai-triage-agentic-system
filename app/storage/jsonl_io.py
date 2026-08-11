"""Small JSONL helpers for local append-only evidence files.

Local credentialed research mode uses JSONL files as a workstation-local audit
and workflow store. Uvicorn can service multiple requests concurrently, so
independent appenders need a shared per-file lock; otherwise two writes can
interleave and leave partial JSON fragments. Readers are intentionally tolerant
of legacy damaged lines so Audit/Analytics render the valid evidence instead of
raising a 500.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, TypeVar

import orjson

T = TypeVar("T")

_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


_log = logging.getLogger(__name__)


def _lock_for(path: Path) -> threading.Lock:
    return _LOCKS[str(path.expanduser().resolve()).lower()]


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    payload = orjson.dumps(record) + b"\n"
    resolved = path.expanduser()
    lock = _lock_for(resolved)
    with lock:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with resolved.open("ab") as f:
            f.write(payload)
            f.flush()


def read_jsonl_dicts(path: Path, *, limit: int | None = None) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("rb") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                value = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    if limit is not None:
        return rows[-max(0, int(limit)):]
    return rows


def read_jsonl_models(path: Path, model: Callable[..., T]) -> List[T]:
    """Parse a JSONL file into models, skipping rows that fail validation.

    Skipping (rather than raising) is deliberate: one malformed line must not
    take down analytics or the audit dashboard. But the skip was previously
    SILENT, which is its own hazard — if a schema gains a required field, every
    historical record silently disappears from the dashboard with no error, no
    count, and no log line, and the totals just quietly get smaller. The
    behaviour is unchanged; the failure is now visible in the service logs and
    countable via `last_skipped_row_count`.
    """
    records: List[T] = []
    skipped = 0
    first_error: str = ""
    for row in read_jsonl_dicts(path):
        try:
            records.append(model(**row))
        except Exception as exc:
            skipped += 1
            if not first_error:
                first_error = f"{type(exc).__name__}"
            continue
    _SKIPPED_ROWS[str(path)] = skipped
    if skipped:
        _log.warning(
            "read_jsonl_models: skipped %d unparseable row(s) of %d in %s "
            "(first error: %s). These rows are absent from any downstream "
            "count or aggregation.",
            skipped, skipped + len(records), path.name, first_error,
        )
    return records


# path -> rows skipped on the most recent read. Lets a caller report data-quality
# loss instead of silently under-reporting.
_SKIPPED_ROWS: dict = {}


def last_skipped_row_count(path: Path | str | None = None) -> int:
    """Rows dropped by the most recent read_jsonl_models call(s)."""
    if path is None:
        return sum(_SKIPPED_ROWS.values())
    return _SKIPPED_ROWS.get(str(path), 0)
