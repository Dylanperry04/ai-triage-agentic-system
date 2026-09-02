"""Process-local coordination between demo reset and demo-state writers.

The Azure demonstration is intentionally one App Service instance with one
Uvicorn worker. Within that supported profile this lock prevents a sweeper,
backfill, or clinical transition from recreating state while Reset Demo is
archiving it. It is not presented as a distributed lock for scale-out use.
"""
from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Iterator


_demo_state_lock = threading.RLock()


@contextmanager
def demo_state_guard() -> Iterator[None]:
    with _demo_state_lock:
        yield

