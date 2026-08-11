"""
Rate limiting and input-size limits for the chatbot/agent boundary.

Abuse protection (defence-in-depth alongside prompt-injection screening and the
output filter): multiple hospital roles can use the chatbot, so a single user
should not be able to flood the LLM (cost, denial-of-service, or to brute-force
past the heuristic injection screen). This module provides:

  - a per-user sliding-window request limiter,
  - a max prompt length check,
  - a per-session case-count limiter,
  - repeated-block tracking (so many blocked/denied attempts can be flagged).

Storage is in-process (a module-level dict). This is sufficient for a single
Streamlit/container instance; a multi-instance deployment should back this with a
shared store (e.g. Redis) — documented as a deployment note, not needed for the
demo. Limits are configurable via env vars.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Configurable limits.
def max_prompt_chars() -> int:
    return _int_env("CHATBOT_MAX_PROMPT_CHARS", 2000)


def rate_limit_window_seconds() -> int:
    return _int_env("CHATBOT_RATE_WINDOW_SECONDS", 60)


def rate_limit_max_requests() -> int:
    return _int_env("CHATBOT_RATE_MAX_REQUESTS", 20)


def max_cases_per_session() -> int:
    return _int_env("CHATBOT_MAX_CASES_PER_SESSION", 100)


def repeated_block_threshold() -> int:
    return _int_env("CHATBOT_REPEATED_BLOCK_THRESHOLD", 5)


# In-process state (per user_id).
_request_times: Dict[str, Deque[float]] = defaultdict(deque)
_block_counts: Dict[str, int] = defaultdict(int)


def reset_state() -> None:
    """Clear all rate-limit state (used by tests)."""
    _request_times.clear()
    _block_counts.clear()


@dataclass
class RateDecision:
    allowed: bool
    reason: str = ""
    retry_after_seconds: int = 0


def check_prompt_length(text: str) -> Tuple[bool, str]:
    """Return (ok, reason). False if the prompt exceeds the max length."""
    limit = max_prompt_chars()
    if text and len(text) > limit:
        return False, f"prompt exceeds {limit} characters ({len(text)})"
    return True, ""


def check_rate(user_id: str, now: float | None = None) -> RateDecision:
    """Sliding-window per-user request limiter. Records the request time when
    allowed."""
    uid = user_id or "anonymous"
    now = now if now is not None else time.monotonic()
    window = rate_limit_window_seconds()
    limit = rate_limit_max_requests()

    times = _request_times[uid]
    # Drop timestamps outside the window.
    cutoff = now - window
    while times and times[0] < cutoff:
        times.popleft()

    if len(times) >= limit:
        retry = int(window - (now - times[0])) + 1
        return RateDecision(allowed=False,
                            reason=f"rate limit: {limit} requests / {window}s exceeded",
                            retry_after_seconds=max(1, retry))
    times.append(now)
    return RateDecision(allowed=True)


def record_block(user_id: str) -> int:
    """Increment and return a user's blocked-attempt counter."""
    uid = user_id or "anonymous"
    _block_counts[uid] += 1
    return _block_counts[uid]


def repeated_blocks_exceeded(user_id: str) -> bool:
    """True if the user has crossed the repeated-block alert threshold."""
    uid = user_id or "anonymous"
    return _block_counts[uid] >= repeated_block_threshold()
