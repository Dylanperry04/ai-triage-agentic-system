"""
Central case resolution service.

External callers (API URLs, the Streamlit client, audit records) only ever use
the PSEUDONYMOUS case_uid. The raw stay_id is never in a URL or response. Because
the pseudonym is a one-way HMAC, we resolve case_uid -> case by loading the cases
for each known dataset and computing each case's pseudonymous uid, then matching.

This is the single place that maps the external identifier to the internal
(source_dataset, stay_id, case) tuple, so raw identifiers stay in internal
processing only. Resolution is dataset-scoped (KTAS cases are never returned for a
MIMIC uid and vice-versa) because the dataset prefix is part of the uid.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.security.redaction import pseudonymous_case_uid

logger = logging.getLogger(__name__)


# The only live dataset the resolver serves is full MIMIC-IV-ED (credentialed),
# loaded via the full loader on the approved environment (MIMIC_FULL_ED_DIR). The
# retired demo/KTAS datasets are no longer served. For tests, a synthetic
# MIMIC-shaped frontend_cases_override.jsonl is honored (no credentialed data).
KNOWN_DATASETS = ("mimic_full",)

FULL_MIMIC_LABEL = "MIMIC-IV-ED-Full-v2.2"
SYNTHETIC_SUPERVISOR_DEMO_LABEL = "MIMIC-IV-ED-Synthetic-Supervisor-Demo"


@dataclass
class ResolvedCase:
    case_uid: str
    source_dataset: str
    stay_id: Any
    case: Dict[str, Any]


class CaseUidCollisionError(RuntimeError):
    """Raised when two internal cases map to the same external case_uid."""


def _source_dataset_label(dataset: str) -> str:
    if dataset == "mimic_full":
        return FULL_MIMIC_LABEL
    return dataset


def _dataset_may_have_uid_prefix(dataset: str, prefix: str) -> bool:
    """Return whether a uid prefix can belong to a resolver dataset.

    The live resolver exposes one logical MIMIC route, but that route can serve
    the credentialed full dataset, the guarded public demo subset, or synthetic
    Azure supervisor-demo rows. Each row's actual source_dataset must remain
    truthful, so resolution cannot assume a single static prefix.
    """
    if dataset == "mimic_full":
        return prefix.startswith("MIMIC-IV-ED")
    return _source_dataset_label(dataset) == prefix


def _add_to_uid_index(index: Dict[str, ResolvedCase], rc: ResolvedCase) -> None:
    """Fail closed instead of resolving an ambiguous pseudonymous identifier."""
    if rc.case_uid in index:
        raise CaseUidCollisionError(
            "Pseudonymous case_uid collision detected; refusing ambiguous "
            f"case resolution for {rc.case_uid}."
        )
    index[rc.case_uid] = rc


# ── Serving cache + uid index ────────────────────────────────────────────────
# Loading and uid-resolving cases on every request is O(n) and re-reads the data.
# We cache the loaded cases and a {case_uid: ResolvedCase} index per dataset, so
# listing is a cheap slice and resolution is an O(1) dict lookup. The cache is
# keyed by a cheap signature so it invalidates when the underlying source changes.

_CASE_CACHE: Dict[str, Any] = {}
_PARTIAL_CASE_CACHE: Dict[str, Any] = {}
_COUNT_CACHE: Dict[str, Any] = {}


def _cache_signature(dataset: str) -> Any:
    """A cheap signature that changes when the served data could have changed:
    the synthetic override file's mtime/size (tests), or the credentialed dir's
    mtime (serving). Never reads patient data; only stat()s."""
    sig: list = [dataset]
    try:
        override = settings.processed_dir / "frontend_cases_override.jsonl"
        if override.exists() and _synthetic_override_allowed():
            st = override.stat()
            sig.append(("override", st.st_mtime_ns, st.st_size))
        demo_cases = _supervisor_demo_cases_path()
        if _supervisor_demo_cases_allowed() and demo_cases.exists():
            st = demo_cases.stat()
            sig.append(("azure_supervisor_demo", st.st_mtime_ns, st.st_size))
        from app.data_pipeline.mimic_public_sample_loader import (
            public_sample_view_allowed, configured_public_sample_path,
        )
        public_sample = configured_public_sample_path()
        if public_sample_view_allowed() and public_sample is not None and public_sample.exists():
            st = public_sample.stat()
            sig.append(("mimic_public_sample", st.st_mtime_ns, st.st_size))
    except Exception:
        pass
    try:
        if settings.mimic_full_ed_dir is not None:
            from pathlib import Path
            ed = Path(settings.mimic_full_ed_dir).expanduser()
            from app.data_pipeline.mimic_adapter import candidate_mimic_table_paths
            for t in ("edstays", "triage"):
                for p in candidate_mimic_table_paths(ed, t):
                    if p.is_file():
                        st = p.stat()
                        sig.append((p.name, st.st_mtime_ns, st.st_size))
    except Exception:
        pass
    return tuple(sig)


def _get_resolved_cases(dataset: str) -> List[ResolvedCase]:
    """Return the dataset's ResolvedCases, building and caching them (with a uid
    index) keyed by a cheap signature."""
    sig = _cache_signature(dataset)
    cached = _CASE_CACHE.get(dataset)
    if cached is not None and cached["sig"] == sig:
        return cached["cases"]
    resolved: List[ResolvedCase] = []
    index: Dict[str, ResolvedCase] = {}
    for case in _load_dataset_cases(dataset):
        stay_id = _case_stay_id(case)
        label = case.get("source_dataset") or _source_dataset_label(dataset)
        uid = pseudonymous_case_uid(label, stay_id)
        rc = ResolvedCase(case_uid=uid, source_dataset=label,
                          stay_id=stay_id, case=case)
        resolved.append(rc)
        _add_to_uid_index(index, rc)
    _CASE_CACHE[dataset] = {"sig": sig, "cases": resolved, "index": index}
    return resolved


def _get_resolved_cases_page(dataset: str, *, upto: int) -> List[ResolvedCase]:
    """Build only the first ``upto`` serving cases and cache their uid index.

    Listing a page should not require constructing all 425k full-MIMIC cases.
    The partial index lets the immediately selected case resolve quickly for
    assessment; resolving a case outside the cached page still falls back to the
    complete index path.
    """
    source_sig = _cache_signature(dataset)
    requested_upto = max(0, int(upto))
    cached = _PARTIAL_CASE_CACHE.get(dataset)
    if (
        cached is not None
        and cached.get("source_sig") == source_sig
        and int(cached.get("upto") or 0) >= requested_upto
    ):
        return cached["cases"][:requested_upto]
    resolved: List[ResolvedCase] = []
    index: Dict[str, ResolvedCase] = {}
    for case in _load_dataset_cases(dataset, n=requested_upto):
        stay_id = _case_stay_id(case)
        label = case.get("source_dataset") or _source_dataset_label(dataset)
        uid = pseudonymous_case_uid(label, stay_id)
        rc = ResolvedCase(case_uid=uid, source_dataset=label,
                          stay_id=stay_id, case=case)
        resolved.append(rc)
        _add_to_uid_index(index, rc)
    _PARTIAL_CASE_CACHE[dataset] = {
        "sig": (source_sig, requested_upto),
        "source_sig": source_sig,
        "upto": requested_upto,
        "cases": resolved,
        "index": index,
    }
    return resolved


def _get_uid_index(dataset: str) -> Dict[str, ResolvedCase]:
    _get_resolved_cases(dataset)  # ensure built
    return _CASE_CACHE[dataset]["index"]


def _load_dataset_cases(dataset: str, n: Optional[int] = None) -> List[Dict[str, Any]]:
    """Load raw case dicts for the live dataset (full MIMIC-IV-ED only).

    A synthetic MIMIC-shaped frontend_cases_override.jsonl takes precedence (used
    by tests; never credentialed data). Otherwise, full MIMIC is loaded from the
    credentialed MIMIC_FULL_ED_DIR via the guarded full loader, and only on an
    approved environment. Returns [] if neither source is available (e.g. this
    sandbox, which has no credentialed data) — the API then serves no cases, which
    is the correct fail-closed behaviour."""
    if dataset != "mimic_full":
        return []
    try:
        from app.storage.jsonl_repository import read_jsonl
        override = settings.processed_dir / "frontend_cases_override.jsonl"
        if override.exists() and _synthetic_override_allowed():
            rows = read_jsonl(override)
            scoped = [r for r in rows
                      if str(r.get("source_dataset", "")).startswith("MIMIC-IV-ED")]
            if scoped:
                return scoped[:n] if n is not None else scoped
        if _supervisor_demo_cases_allowed():
            demo_path = _supervisor_demo_cases_path()
            if demo_path.exists():
                rows = read_jsonl(demo_path)
                scoped = [
                    r for r in rows
                    if str(r.get("source_dataset", "")).startswith("MIMIC-IV-ED")
                ]
                if scoped:
                    scoped = [
                        {
                            **r,
                            "source_dataset": SYNTHETIC_SUPERVISOR_DEMO_LABEL,
                        }
                        for r in scoped
                    ]
                    return scoped[:n] if n is not None else scoped
        from app.data_pipeline.mimic_public_sample_loader import load_public_mimic_ed_cases
        public_sample_rows = load_public_mimic_ed_cases(n=n)
        if public_sample_rows:
            return public_sample_rows
        # Real full-MIMIC load (credentialed environment only). The loader fails
        # closed with a SPECIFIC reason; log it (never silently swallow) so the
        # operator can see why no cases loaded. The reason is non-sensitive.
        # SERVING uses the triage-time-only loader (edstays + triage), NOT the
        # full six-table loader, to avoid loading full-stay data per request.
        from app.data_pipeline.mimic_full_loader import (
            is_full_mimic_available, load_mimic_full_cases_triage_time,
            full_mimic_diagnostic,
        )
        if is_full_mimic_available():
            cases = load_mimic_full_cases_triage_time(n=n)
            return [c.model_dump(mode="json") if hasattr(c, "model_dump") else c
                    for c in cases]
        else:
            diag = full_mimic_diagnostic()
            logger.warning("Full-MIMIC cases not loaded: %s (profile=%s)",
                           diag.get("reason"), diag.get("active_profile"))
    except Exception as exc:
        logger.warning("Full-MIMIC case loading failed: %s: %s",
                       type(exc).__name__, exc)
        return []
    return []


def _count_dataset_cases(dataset: str) -> int:
    sig = _cache_signature(dataset)
    cached = _COUNT_CACHE.get(dataset)
    if cached is not None and cached["sig"] == sig:
        return cached["count"]
    count = 0
    try:
        override = settings.processed_dir / "frontend_cases_override.jsonl"
        if override.exists() and _synthetic_override_allowed():
            from app.storage.jsonl_repository import read_jsonl
            count = sum(
                1 for r in read_jsonl(override)
                if str(r.get("source_dataset", "")).startswith("MIMIC-IV-ED")
            )
        elif _supervisor_demo_cases_allowed():
            from app.storage.jsonl_repository import read_jsonl
            demo_path = _supervisor_demo_cases_path()
            count = sum(
                1 for r in read_jsonl(demo_path)
                if str(r.get("source_dataset", "")).startswith("MIMIC-IV-ED")
            ) if demo_path.exists() else 0
        else:
            from app.data_pipeline.mimic_public_sample_loader import count_public_mimic_ed_cases
            count = count_public_mimic_ed_cases()
        if count == 0:
            from app.data_pipeline.mimic_full_loader import is_full_mimic_available
            if is_full_mimic_available() and settings.mimic_full_ed_dir is not None:
                import gzip
                from pathlib import Path
                ed = Path(settings.mimic_full_ed_dir).expanduser()
                from app.data_pipeline.mimic_adapter import candidate_mimic_table_paths
                for p in candidate_mimic_table_paths(ed, "edstays"):
                    if not p.is_file():
                        continue
                    if p.name.endswith(".gz"):
                        with gzip.open(p, "rt", encoding="utf-8") as f:
                            count = max(0, sum(1 for _ in f) - 1)
                    else:
                        with p.open("rt", encoding="utf-8") as f:
                            count = max(0, sum(1 for _ in f) - 1)
                    break
    except Exception as exc:
        logger.warning("Full-MIMIC case count failed: %s: %s", type(exc).__name__, exc)
        count = len(_get_resolved_cases(dataset))
    _COUNT_CACHE[dataset] = {"sig": sig, "count": count}
    return count


def _synthetic_override_allowed() -> bool:
    """Synthetic case overrides are for public demo/testing, not credentialed data.

    A stray ``frontend_cases_override.jsonl`` in ``data/processed`` should never
    shadow real MIMIC cases on a local credentialed research machine unless the
    operator explicitly opts in.
    """
    if os.environ.get("ALLOW_SYNTHETIC_CASE_OVERRIDE", "").lower() == "true":
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    credentialed = (
        os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        or os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    )
    return not credentialed


def _supervisor_demo_cases_path():
    return settings.project_root / "data" / "demo" / "azure_supervisor_demo_cases.jsonl"


def _supervisor_demo_cases_allowed() -> bool:
    """Built-in synthetic cases for the Azure supervisor demo only.

    Real full MIMIC must not be used in this mode unless explicitly approved.
    When that approval flag and MIMIC_FULL_ED_DIR are both set, the resolver lets
    the normal full-MIMIC loader handle cases instead of the built-in demo rows.
    """
    try:
        from app.security.identity import azure_supervisor_demo_mode
        if not azure_supervisor_demo_mode():
            return False
    except Exception:
        return False
    if os.environ.get("ALLOW_FULL_MIMIC_IN_AZURE_DEMO", "").lower() == "true":
        return False
    return True


def _uses_live_credentialed_full_mimic(dataset: str) -> bool:
    """True when resolving against the real full-MIMIC CSV source.

    In this mode a request must never build the complete 425k-case UID index.
    Small synthetic/public/demo sources are allowed to keep the full-index path
    used by tests and non-credentialed demos.
    """
    if dataset != "mimic_full":
        return False
    try:
        override = settings.processed_dir / "frontend_cases_override.jsonl"
        if override.exists() and _synthetic_override_allowed():
            return False
        if _supervisor_demo_cases_allowed() and _supervisor_demo_cases_path().exists():
            return False
        from app.data_pipeline.mimic_public_sample_loader import (
            configured_public_sample_path,
            public_sample_view_allowed,
        )
        public_sample = configured_public_sample_path()
        if (
            public_sample_view_allowed()
            and public_sample is not None
            and public_sample.exists()
        ):
            return False
        from app.data_pipeline.mimic_full_loader import is_full_mimic_available
        return bool(is_full_mimic_available())
    except Exception:
        return False


def _resolve_scan_limit() -> int:
    raw = os.environ.get("MIMIC_CASE_RESOLVE_SCAN_LIMIT", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_SEARCH_SCAN_LIMIT
    except ValueError:
        value = DEFAULT_SEARCH_SCAN_LIMIT
    return max(MAX_PAGE_SIZE, min(value, 100_000))


def _resolve_from_bounded_page(dataset: str, case_uid: str) -> Optional[ResolvedCase]:
    cases = _get_resolved_cases_page(dataset, upto=_resolve_scan_limit())
    cached = _PARTIAL_CASE_CACHE.get(dataset) or {}
    hit = (cached.get("index") or {}).get(case_uid)
    if hit is not None:
        return hit
    for rc in cases:
        if rc.case_uid == case_uid:
            return rc
    return None


def _latest_workflow_state_for_search(case_uid: str) -> Dict[str, Any]:
    try:
        from app.config import settings as _settings
        from app.storage.case_state_repository import latest_case_state
        return latest_case_state(_settings.processed_dir / "case_workflow_state.jsonl", case_uid)
    except Exception:
        return {}


def _matches_search(rc: ResolvedCase, search: Optional[str]) -> bool:
    if not search:
        return True
    q = str(search).strip().lower()
    if not q:
        return True
    triage = rc.case.get("triage") or {}
    edstay = rc.case.get("edstay") or {}
    workflow_state = _latest_workflow_state_for_search(rc.case_uid)
    haystack = " ".join(
        str(v) for v in (
            rc.case_uid,
            rc.source_dataset,
            rc.stay_id,
            rc.case.get("subject_id"),
            edstay.get("subject_id"),
            edstay.get("stay_id"),
            triage.get("chiefcomplaint"),
            triage.get("acuity"),
            edstay.get("arrival_transport"),
            workflow_state.get("case_status"),
            workflow_state.get("review_status"),
            workflow_state.get("escalation_status"),
            workflow_state.get("escalation_state"),
        )
        if v is not None
    ).lower()
    return q in haystack


def _matches_filters(rc: ResolvedCase, filters: Optional[Dict[str, Any]]) -> bool:
    if not filters:
        return True
    triage = rc.case.get("triage") or {}
    edstay = rc.case.get("edstay") or {}
    workflow_state = _latest_workflow_state_for_search(rc.case_uid)

    def _clean(value: Any) -> str:
        return str(value or "").strip().lower()

    subject_id = _clean(filters.get("subject_id"))
    if subject_id and subject_id not in {
        _clean(rc.case.get("subject_id")),
        _clean(edstay.get("subject_id")),
    }:
        return False
    stay_id = _clean(filters.get("stay_id"))
    if stay_id and stay_id not in {_clean(rc.stay_id), _clean(edstay.get("stay_id"))}:
        return False
    acuity = _clean(filters.get("acuity_level"))
    if acuity and acuity != _clean(triage.get("acuity")):
        return False
    workflow_status = _clean(filters.get("workflow_status"))
    if workflow_status and workflow_status not in {
        _clean(workflow_state.get("review_status")),
        _clean(workflow_state.get("last_action")),
        _clean(workflow_state.get("escalation_status")),
        _clean(workflow_state.get("escalation_state")),
    }:
        return False
    case_status = _clean(filters.get("case_status"))
    if case_status and case_status != _clean(workflow_state.get("case_status")):
        return False
    active_state = _clean(filters.get("active_state"))
    if active_state:
        closed = _clean(workflow_state.get("case_status")) in {
            "discharged", "closed", "case_closed"
        } or bool(workflow_state.get("discharged_at"))
        escalated = _clean(workflow_state.get("escalation_status")) in {
            "requested", "pending", "confirmed"
        }
        if active_state in {"active", "open"} and closed:
            return False
        if active_state in {"discharged", "closed"} and not closed:
            return False
        if active_state in {"escalated", "escalation"} and not escalated:
            return False
    return True


def _case_stay_id(case: Dict[str, Any]) -> Any:
    # KTAS uses 'stay_id'; both adapters populate stay_id in the case dict.
    return case.get("stay_id")


MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
DEFAULT_SEARCH_SCAN_LIMIT = 5000


def _search_scan_limit() -> int:
    """Bound local/demo unindexed search scans.

    Patient-data mode disables unindexed search at the route layer. Outside that
    mode we still avoid materialising all full-MIMIC cases for a search box.
    """
    raw = os.environ.get("MIMIC_CASE_SEARCH_SCAN_LIMIT", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_SEARCH_SCAN_LIMIT
    except ValueError:
        value = DEFAULT_SEARCH_SCAN_LIMIT
    return max(DEFAULT_PAGE_SIZE, min(value, 100_000))


def _search_resolved_cases_bounded(
    dataset: str,
    search: Optional[str],
    *,
    filters: Optional[Dict[str, Any]] = None,
) -> tuple[List[ResolvedCase], bool, int]:
    """Search a bounded local/demo window without building the full case index.

    Returns (matches, truncated, scan_limit). ``truncated`` means more cases
    exist beyond the searched window, so the match count is not an exact global
    total. This is acceptable for public demo / local research search UX and
    avoids accidentally doing an unbounded CSV/index build from a Streamlit
    search box.
    """
    scan_limit = _search_scan_limit()
    cases = _get_resolved_cases_page(dataset, upto=scan_limit)
    matches = [
        rc for rc in cases
        if _matches_search(rc, search) and _matches_filters(rc, filters)
    ]
    try:
        truncated = _count_dataset_cases(dataset) > scan_limit
    except Exception:
        truncated = len(cases) >= scan_limit
    return matches, truncated, scan_limit


def list_cases(dataset: Optional[str] = None, *, limit: Optional[int] = None,
               offset: int = 0, search: Optional[str] = None,
               filters: Optional[Dict[str, Any]] = None) -> List[ResolvedCase]:
    """List cases (optionally for one dataset) with their pseudonymous uids,
    PAGINATED and BOUNDED. Uses the serving cache (cheap repeated calls).

    limit defaults to DEFAULT_PAGE_SIZE and is capped at MAX_PAGE_SIZE so a single
    request can never return an unbounded result set. offset selects the page.
    Pass limit=0 explicitly only via list_all_cases() for internal full scans.
    """
    if limit is None:
        limit = DEFAULT_PAGE_SIZE
    limit = max(0, min(int(limit), MAX_PAGE_SIZE))
    offset = max(0, int(offset))
    datasets = [dataset] if dataset else list(KNOWN_DATASETS)
    out: List[ResolvedCase] = []
    for ds in datasets:
        if search or filters:
            matches, _, _ = _search_resolved_cases_bounded(ds, search, filters=filters)
            out.extend(matches)
        else:
            out.extend(_get_resolved_cases_page(ds, upto=offset + limit))
    return out[offset:offset + limit] if limit else out[offset:]


def count_cases(dataset: Optional[str] = None, *, search: Optional[str] = None,
                filters: Optional[Dict[str, Any]] = None) -> int:
    """Total number of cases available (for pagination metadata)."""
    datasets = [dataset] if dataset else list(KNOWN_DATASETS)
    return sum(
        (
            len(_search_resolved_cases_bounded(ds, search, filters=filters)[0])
            if search or filters else _count_dataset_cases(ds)
        )
        for ds in datasets
    )


def search_metadata(dataset: Optional[str] = None, *, search: Optional[str] = None,
                    filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return non-sensitive metadata for a bounded unindexed search."""
    if not search and not filters:
        return {"search_bounded": False, "total_is_exact": True}
    datasets = [dataset] if dataset else list(KNOWN_DATASETS)
    truncated = False
    scan_limits: list[int] = []
    for ds in datasets:
        _, ds_truncated, scan_limit = _search_resolved_cases_bounded(
            ds, search, filters=filters
        )
        truncated = truncated or ds_truncated
        scan_limits.append(scan_limit)
    return {
        "search_bounded": True,
        "search_scan_limit": max(scan_limits) if scan_limits else _search_scan_limit(),
        "total_is_exact": not truncated,
        "search_truncated": truncated,
    }


def list_all_cases(dataset: Optional[str] = None) -> List[ResolvedCase]:
    """Internal full scan (unbounded). Not exposed to external pagination; used
    where the full set is genuinely needed (e.g. building review queues)."""
    datasets = [dataset] if dataset else list(KNOWN_DATASETS)
    out: List[ResolvedCase] = []
    for ds in datasets:
        out.extend(_get_resolved_cases(ds))
    return out


def resolve(case_uid: str) -> Optional[ResolvedCase]:
    """Resolve a pseudonymous case_uid to its case via an O(1) index lookup
    (cached), or None if not found.

    The uid format is '<source_dataset>~<hmac>'. We pick the dataset by the uid
    prefix, then look the uid up directly in that dataset's index rather than
    recomputing every case's uid.
    """
    if "~" not in case_uid:
        return None
    prefix = case_uid.rsplit("~", 1)[0]
    for ds in KNOWN_DATASETS:
        # Only search a dataset whose label matches the uid prefix.
        if not _dataset_may_have_uid_prefix(ds, prefix):
            continue
        partial = _PARTIAL_CASE_CACHE.get(ds)
        if partial is not None and partial.get("index", {}).get(case_uid) is not None:
            return partial["index"][case_uid]
        if _uses_live_credentialed_full_mimic(ds):
            return _resolve_from_bounded_page(ds, case_uid)
        hit = _get_uid_index(ds).get(case_uid)
        if hit is not None:
            return hit
    return None
