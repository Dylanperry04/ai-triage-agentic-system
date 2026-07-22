"""Small JSONL helpers for local append-only evidence files.

Local credentialed research mode uses JSONL files as a workstation-local audit
and workflow store. Uvicorn can service multiple requests concurrently, so
independent appenders need a shared per-file lock; otherwise two writes can
interleave and leave partial JSON fragments. Readers are intentionally tolerant
of legacy damaged lines so Audit/Analytics render the valid evidence instead of
raising a 500.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, TypeVar

import orjson

T = TypeVar("T")

_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)


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
    records: List[T] = []
    for row in read_jsonl_dicts(path):
        try:
            records.append(model(**row))
        except Exception:
            continue
    return records
