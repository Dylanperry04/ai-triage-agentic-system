"""
Full-MIMIC review-queue construction.

The live review queue is computed from canonical full-MIMIC cases. A case is
queued for clinician attention when any of these hold:
  - missing triage-time fields (e.g. a missing vital),
  - a deterministic safety flag (missing critical vital / critical physiology),
  - the escalate-only vital override fired,
  - it is flagged unsafe-to-present,
  - and it has no saved clinician review yet.

Every queued row carries its source_dataset and case_uid so downstream review
saving and lookup are dataset-safe. Research only; not clinical triage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from app.schemas.workflow_run import make_case_uid


def build_mimic_review_queue(
    mimic_cases: List[Any],
    reviewed_case_uids: set[str],
    run_workflow_fn,
    limit: int = 60,
) -> List[Dict[str, Any]]:
    """Return queueable MIMIC cases with a reason and model summary.

    `run_workflow_fn` is injected (the orchestrator's run_workflow) so this is
    unit-testable without importing the whole app. `reviewed_case_uids` are the
    case_uids that already have a saved review (excluded from "pending")."""
    queue: List[Dict[str, Any]] = []
    for case in mimic_cases[:limit]:
        wf = run_workflow_fn(case)
        ti = wf.triage_input
        dv = wf.data_validation
        sr = wf.safety_review
        fa = getattr(wf, "final_acuity_assessment", None)

        missing = list(getattr(dv, "missing_required_fields", []) or [])
        flags = list(getattr(sr, "data_quality_flags", []) or [])
        override_applied = bool(getattr(fa, "override_applied", False))
        unsafe = not getattr(sr, "is_safe_to_present", True)

        reasons = []
        if missing:
            reasons.append(f"missing fields: {', '.join(missing)}")
        if any(f.startswith("CRITICAL_PHYSIOLOGY") for f in flags):
            reasons.append("critical physiology flagged")
        if any(f.startswith("MISSING_CRITICAL_VITAL") for f in flags):
            reasons.append("missing critical vital")
        if override_applied:
            reasons.append(f"deterministic override applied ({fa.override_tier})")
        if unsafe:
            reasons.append("flagged unsafe to present")

        if not reasons:
            continue  # nothing to review for this case

        case_uid = make_case_uid(ti.source_dataset, wf.stay_id)
        if case_uid in reviewed_case_uids:
            continue  # already reviewed

        queue.append({
            "source_dataset": ti.source_dataset,
            "case_uid": case_uid,
            "chiefcomplaint": ti.chiefcomplaint or "?",
            "missing_fields": missing,
            "model_summary": (
                f"acuity {fa.ml_predicted_acuity} → {fa.category}"
                if fa and fa.applicable else "no ML estimate"
            ),
            "final_category": getattr(fa, "category", None),
            "final_priority": getattr(fa, "priority", None),
            "review_status": "PENDING",
            "reason_queued": "; ".join(reasons),
        })
    return queue
