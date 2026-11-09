"""Reconcile immutable review attempts with evidence of their applied transition.

Writing a review is the first half of a case transition. It is not proof that
the decision took effect. All consumers use this projection; no journal record
is rewritten and a later inherited last_review_id cannot prove an earlier write.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping


def _dict(value: Any) -> dict:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def _revision(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def reconcile_review_outcomes(
    reviews: Iterable[Any], workflow_states: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return copies marked applied, pending, superseded, or unverified.

    An applied event must agree on case, review, run and (for new records) the
    exact revision. Older records without a base revision require a matching
    state event; absent evidence is never promoted to a completed decision.
    """
    by_review: dict[tuple[str, str], list[dict]] = defaultdict(list)
    latest: dict[str, dict] = {}
    for raw in workflow_states:
        state = _dict(raw)
        case = str(state.get("case_uid") or "")
        if not case:
            continue
        previous = latest.get(case, {})
        if (_revision(state.get("state_revision")) or 0) >= (_revision(previous.get("state_revision")) or 0):
            latest[case] = state
        if state.get("last_review_id"):
            by_review[(case, str(state["last_review_id"]))].append(state)

    result = []
    for raw in reviews:
        review = _dict(raw)
        case = str(review.get("case_uid") or "")
        run = str(review.get("workflow_run_id") or "")
        review_id = str(review.get("review_id") or "")
        base = _revision(review.get("base_state_revision"))
        proof = next((state for state in by_review.get((case, review_id), [])
            if run and state.get("workflow_run_id") == run
            and state.get("latest_workflow_run_id", run) == run
            and (base is None or _revision(state.get("state_revision")) == base + 1)), None)
        outcome = "unverified"
        current = latest.get(case, {})
        revision = _revision(current.get("state_revision"))
        if proof is not None:
            outcome = "applied"
        elif base is not None and revision == base and current.get("latest_workflow_run_id") == run:
            outcome = "pending"
        elif base is not None and revision is not None and revision > base:
            outcome = "superseded"
        result.append({**review, "review_outcome": outcome,
            "applied_state_revision": proof.get("state_revision") if proof is not None else None})
    return result
