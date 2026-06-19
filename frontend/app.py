"""
AI Triage Agentic Workflow — Streamlit UI (multi-dataset: MIMIC-IV-ED Demo default, KTAS separate).

Seven tabs:
  1. Triage Review     — run the deterministic workflow on a selected case,
                          view the rules engine output, safety flags, and ML
                          research estimate, and submit a clinician review.
  2. Follow-Up Comparison — explicitly declare a new stay as a follow-up to
                          a previous stay_id, and view the deterministic
                          escalation comparison. This is a demonstration
                          workflow capability, not automatic repeat-patient
                          detection: the public KTAS data has no real patient
                          identifier, so this project never tries to guess
                          which rows belong to the same person. See
                          app/schemas/followup.py for the full rationale.
  (The case chat and multi-agent team explanation now live INSIDE the Triage
   Review tab, scoped to the selected case -- the standalone Clinician Chat tab
   was removed. The chat/team backend (app/agents/autogen_team.py,
   app/agents/autogen_multi_agent_team.py) is unchanged and reused there.)
  4. Governance         — five-stage responsible-AI review gate, adapted for
                          KTAS, summarising whether this system is ready for
                          anything beyond a research demo (it is not).
  5. Review Queue       — cases with missing triage-time data that need a
                          clinician's attention before being relied on.
  6. Audit Log          — full history of saved clinician reviews.
  7. Model Performance  — the two trained KTAS research models (5-class and
                          emergency binary) and their cross-validated metrics.

NOT FOR CLINICAL USE. Research prototype only. Every output requires
clinician confirmation. KTAS is not Manchester Triage Scale; no mapping
between them exists anywhere in this codebase.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.rules.provisional_mts_ruleset import register_provisional_ruleset
from app.storage.jsonl_repository import read_jsonl
from app.schemas.internal import EDTriageCase
from app.schemas.review import HumanReviewRecord
from app.agents.orchestrator import run_workflow
from app.storage.human_review_repository import (
    append_human_review,
    get_reviews_for_stay,
    read_human_reviews,
)
from app.agents.autogen_team import load_azure_config, run_single_question
from app.agents.autogen_multi_agent_team import run_team_explanation
from app.agents.followup_comparison_agent import compare_follow_up
from app.schemas.followup import FollowUpLinkRequest
from app.storage.followup_repository import (
    append_followup_comparison,
    get_followup_history_for_stay,
    read_followup_comparisons,
)
from app.data_pipeline.ktas_adapter import load_ktas_cases
from app.data_pipeline.mimic_adapter import load_mimic_demo_cases
from app.data_pipeline.export import write_jsonl


st.set_page_config(
    page_title="AI Triage Agentic Workflow",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Register the provisional MTS research ruleset at startup (default-on; set
# PROVISIONAL_MTS_MODE=off to disable). Makes the engine assign provisional,
# clinician-review-required Manchester categories. NOT the official MTS and NOT
# clinically approved -- see app/rules/provisional_mts_ruleset.py.
if settings.provisional_mts_mode:
    register_provisional_ruleset()


# ── Helper functions ──────────────────────────────────────────────────────────

@st.cache_data(show_spinner="Loading KTAS and MIMIC demo cases...")
def _load_cases_cached(
    override_path: Path,
    override_mtime: float | None,
    ktas_path: Path,
    ktas_mtime: float | None,
    demo_dir: Path,
    demo_mtime: float | None,
) -> list[dict]:
    """
    The actual cacheable computation, with no side effects (no
    st.session_state writes, no file writes) and no hidden dependence on
    module-level settings -- every input that should invalidate the
    cache is an explicit parameter.

    BUG FIX (found while testing the MIMIC-merge work this function is
    part of): an earlier version of this caching had a zero-argument
    @st.cache_data function reading settings.processed_dir from a
    closed-over global instead of as a parameter. Confirmed directly
    that Streamlit caches a zero-argument function's result after its
    very FIRST real call in a process and never re-evaluates it again,
    even when the underlying settings.processed_dir was later patched to
    point somewhere completely different -- this silently broke test
    isolation (an earlier test calling this with real, unpatched
    settings poisoned the cache for every later test in the same pytest
    process, even ones using a correctly-isolated override file) and
    would have been a real production bug too (the cache would never
    refresh if MIMIC files were downloaded, or settings changed, after
    the first call in a long-running process). Fixed by making every
    relevant input (override_path, override_mtime, ktas_path, demo_dir)
    an explicit parameter Streamlit's cache key genuinely depends on.
    override_mtime is included specifically so editing the override
    file's contents (as several tests do, writing different fixture
    data across different tests) busts the cache even though
    override_path's path string itself may not change between tests
    using the same tmp_path pattern.

    SECOND BUG FIX (found during a later review pass, same root cause as
    above, just not yet extended to the other two real data sources):
    ktas_mtime and demo_mtime were originally absent, so the cache was
    keyed on ktas_path/demo_dir's PATH strings only, never their actual
    file contents. Confirmed directly: rebuilding the real KTAS CSV in
    place (same path, genuinely different row count) while a process was
    already running and had already cached a result returned the STALE,
    pre-rebuild row count on every subsequent call, with no way to
    refresh short of restarting the whole process. ktas_mtime is that
    one file's real mtime; demo_mtime is the MAXIMUM mtime across all six
    real files in the MIMIC demo directory (edstays, triage, vitalsign,
    diagnosis, medrecon, pyxis -- see app/data_pipeline/mimic_adapter.py
    for why all six are required), so a change to ANY of the six busts
    the cache, not just a change to whichever one happens to be checked
    first.
    """
    if override_path.exists():
        return read_jsonl(override_path)

    if not ktas_path.exists():
        # Caller (load_cases) handles the missing-KTAS UI/stop path;
        # this raises so that case is distinguishable from a genuinely
        # empty real dataset.
        raise FileNotFoundError(f"KTAS CSV not found at {ktas_path}")

    ktas_cases, _ = load_ktas_cases(ktas_path)
    records = [case.model_dump(mode="json") for case in ktas_cases]

    try:
        mimic_cases, _ = load_mimic_demo_cases(demo_dir)
        records.extend(case.model_dump(mode="json") for case in mimic_cases)
    except FileNotFoundError:
        # MIMIC demo files have not been downloaded in this environment.
        # Degrade to KTAS-only rather than crash -- see
        # scripts/download_mimic_ed_demo.py to fetch the real demo data.
        pass

    return records


def load_cases() -> list[dict]:
    """
    Loads cases from BOTH datasets, KTAS and MIMIC demo, so the in-app
    case list reflects both rather than whichever dataset a script was
    last run against -- exactly the kind of staleness that caused a real
    bug found during a later review pass (scripts/build_sample_cases.py
    --dataset demo using a legacy path that mislabelled every MIMIC case
    as Kaggle-KTAS; see KTAS_CHANGELOG.md). The expensive parsing work is
    delegated to _load_cases_cached() above (see its docstring for the
    caching design and the bug that shaped it); this wrapper handles the
    session-state and file-write side effects that must run on every
    call, not just on a true cache miss.

    TEST/OVERRIDE ENTRY POINT: if
    settings.processed_dir/"frontend_cases_override.jsonl" exists, its
    contents are used directly and neither real adapter is called at
    all. This exists specifically so tests can inject small, fully
    controlled fixture data by writing this file BEFORE frontend.app is
    ever imported -- writing a file to disk is always safe, whereas
    monkeypatching an attribute on the frontend.app module itself is
    NOT safe in this Streamlit version (1.56.0): resolving a patch
    target inside frontend.app forces an import of that module, which
    executes its top-level st.tabs()/st.set_page_config() calls outside
    a real Streamlit script-run context, corrupting Streamlit's internal
    form-tracking state for the rest of the process (confirmed directly:
    the exact same "Forms cannot be nested in other forms" failure
    reproduces from a monkeypatch targeting a completely unrelated,
    non-Streamlit-related frontend.app attribute, with zero relation to
    azure_configured or any specific branch -- see KTAS_CHANGELOG.md and
    tests/test_frontend.py's isolated_processed_dir fixture docstring for
    the full investigation). This override file is never written by
    this function itself, and is NOT the same file as
    streamlit_runtime_cases.jsonl below (which IS written by this
    function, as an output for the AutoGen evidence tools to read) or
    triage_cases_sample.jsonl (owned by scripts/build_sample_cases.py).
    Three distinctly-named files for three distinct purposes,
    deliberately, after a related mix-up (the build_sample_cases.py bug
    above) already happened once from files with overlapping purposes.

    Outside of tests, this override file should not exist, and real
    KTAS+MIMIC live-loading is what runs.

    KTAS is the established, always-present default for live-loading --
    a KTAS loading failure is still fatal, exactly as before. MIMIC demo
    data depends on whether data/raw/mimic-iv-ed-demo/2.2/ed/ has
    actually been downloaded (see scripts/download_mimic_ed_demo.py); if
    it has not, this degrades gracefully to KTAS-only rather than
    crashing the whole app, with a clear, visible note rather than a
    silent omission.
    """
    override_path = settings.processed_dir / "frontend_cases_override.jsonl"
    override_mtime = override_path.stat().st_mtime if override_path.exists() else None
    ktas_path = settings.raw_ktas_csv
    ktas_mtime = ktas_path.stat().st_mtime if ktas_path.exists() else None

    demo_dir = settings.raw_demo_dir
    demo_mtime = None
    if demo_dir.exists():
        demo_file_mtimes = [f.stat().st_mtime for f in demo_dir.glob("*.csv.gz")]
        if demo_file_mtimes:
            demo_mtime = max(demo_file_mtimes)

    if not override_path.exists() and not ktas_path.exists():
        st.error(
            "No KTAS data found. Run:\n\n```\npython scripts/run_ktas_pipeline.py\n```"
        )
        st.stop()

    records = _load_cases_cached(
        override_path, override_mtime, ktas_path, ktas_mtime, demo_dir, demo_mtime
    )

    st.session_state["_mimic_demo_available"] = any(
        r.get("source_dataset", "").startswith("MIMIC") for r in records
    )

    # The AutoGen evidence-lookup tools (app/agents/autogen_team.py,
    # app/agents/autogen_multi_agent_team.py) read cases from a file path
    # on disk, not from this in-memory list -- they have their own
    # independent contract with a JSONL file. Write the merged records to
    # a dedicated runtime file (deliberately NOT
    # triage_cases_sample.jsonl, which scripts/build_sample_cases.py owns
    # and other processes may still read) so those tools can correctly
    # look up MIMIC cases too, not just whichever dataset
    # build_sample_cases.py was last run against.
    write_jsonl(settings.processed_dir / "streamlit_runtime_cases.jsonl", records)

    return records


def load_json_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_model_registry() -> dict | None:
    return load_json_file(settings.model_registry_path)


def render_dataset_filtered_case_selector(
    records: list[dict],
    widget_key_prefix: str,
    label: str = "ED Stay",
    show_label: bool = False,
) -> dict:
    """
    Renders a dataset filter (All / KTAS only / MIMIC demo only) followed
    by a case selectbox restricted to the chosen dataset, and returns the
    selected case record.

    This exists specifically to make the MIMIC cases that load_cases()
    already merges into `records` genuinely visible and usable, not just
    technically present -- without a filter, MIMIC's 222 cases are mixed
    in among 1267 KTAS cases with no way to find them except scrolling
    past all of them. Counts are shown on each filter option so the
    split is visible at a glance, before even opening the case dropdown.

    widget_key_prefix must be unique per call site (this is used by both
    the Triage Review tab (case selector + the in-page case chat), which need
    independent selection state) since Streamlit requires unique widget
    keys.
    """
    ktas_records = [r for r in records if r.get("source_dataset") == "Kaggle-KTAS"]
    mimic_records = [
        r for r in records if r.get("source_dataset") == "MIMIC-IV-ED-Demo-v2.2"
    ]
    other_records = [
        r
        for r in records
        if r.get("source_dataset") not in ("Kaggle-KTAS", "MIMIC-IV-ED-Demo-v2.2")
    ]

    # MIMIC-IV-ED Demo is the default dataset for this research phase (Dylan's
    # decision), so it is listed first and selected by default. KTAS is a
    # separate option. The two datasets are deliberately NEVER shown combined --
    # they have different provenance, labels (KTAS acuity vs ESI), and clinical
    # meaning, so there is no "all datasets" view. The user sees exactly one
    # dataset at a time.
    filter_options: list[str] = []
    if mimic_records:
        filter_options.append(f"MIMIC demo only ({len(mimic_records)})")
    filter_options.append(f"KTAS only ({len(ktas_records)})")
    # If neither real adapter happens to be the source (should not occur
    # outside of malformed test data, but defensive rather than
    # crashing), surface it rather than silently dropping those records
    # from every filter option.
    if other_records:
        filter_options.append(f"Other ({len(other_records)})")

    dataset_filter = st.radio(
        "Filter by dataset",
        filter_options,
        index=0,  # defaults to MIMIC demo only when present, else KTAS only
        horizontal=True,
        key=f"{widget_key_prefix}_dataset_filter",
    )

    if dataset_filter.startswith("KTAS only"):
        filtered_records = ktas_records
    elif dataset_filter.startswith("MIMIC demo only"):
        filtered_records = mimic_records
    elif dataset_filter.startswith("Other"):
        filtered_records = other_records
    else:
        # Defensive fallback (should match the first option). Never returns the
        # combined record set, since the datasets are not shown combined.
        filtered_records = mimic_records or ktas_records

    case_options = {
        f"Stay {r['stay_id']} — "
        f"{(r.get('triage') or {}).get('chiefcomplaint') or 'No complaint'}": r
        for r in filtered_records
    }
    if not case_options:
        st.warning("No cases match this filter.")
        st.stop()

    selected_label = st.selectbox(
        label, list(case_options.keys()),
        label_visibility="visible" if show_label else "collapsed",
        key=f"{widget_key_prefix}_case_select",
    )
    return case_options[selected_label]


def azure_openai_configured() -> bool:
    """Single source of truth for whether the AutoGen chat/explanation layer can
    run. Used consistently to gate the Multi-Agent Team Explanation button and
    the case chat, and to choose the correct 'unavailable' message."""
    return load_azure_config() is not None


def fmt_pct(value) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def _status_badge(status: str) -> str:
    if status in ("PASS", "TRIAGE_INPUT_DATA_COMPLETE"):
        return f"✅ {status}"
    if status in ("NOT_CONFIGURED", "NOT_REQUESTED", "NOT_RUN"):
        return f"⚪ {status}"
    if "FAIL" in status or "MISSING" in status or "NEEDS" in status or "ERROR" in status:
        return f"⚠️ {status}"
    return f"ℹ️ {status}"


REASON_CODE_SEVERITY_PREFIXES = ("CRITICAL", "FORBIDDEN")


def _flag_icon(flag: str) -> str:
    upper = flag.upper()
    if any(p in upper for p in REASON_CODE_SEVERITY_PREFIXES):
        return "🔴"
    if "MISSING" in upper or "CONCERN" in upper:
        return "🟡"
    return "ℹ️"

st.title("🏥 AI Triage Agentic Workflow")
st.caption(
    "Multi-dataset research workflow (MIMIC-IV-ED Demo by default; Kaggle KTAS "
    "available as a separate dataset). Deterministic data validation and safety "
    "checks, a provisional Manchester-style research ruleset (NOT the official "
    "Manchester Triage System and NOT clinically approved), an ML research "
    "estimate for KTAS cases only, and an AutoGen explanation/chat layer. "
    "Research/demonstration only — not for clinical use; clinician review "
    "required on every output."
)

def _workflow_action_badge(action: str) -> str:
    if action == "ESCALATION_REQUIRED":
        return f"🔴 {action}"
    if action == "CLINICIAN_INTERVENTION_REQUIRED":
        return f"🟡 {action}"
    return f"🟢 {action}"


def build_criteria_table(result) -> list[dict]:
    """
    Builds a Trial Matcher-style criteria table: one row per
    individually-checkable criterion, each with a Criterion name, a
    Status (one of MET / NOT_MET / UNKNOWN / NOT_APPLICABLE), Evidence
    (the real value or fact that determined the status), and Missing
    info (what specific field would resolve an UNKNOWN, blank
    otherwise).

    Returned as a plain list of dicts (not rendered directly) so this
    can be unit-tested independent of any Streamlit rendering --
    rendering is a thin wrapper around this in render_assessment_card.

    Every row checks a REAL field already present on WorkflowResult; no
    new computation or invented criteria. Status definitions used
    throughout:
      - MET: the criterion's condition is satisfied, based on real,
        present data.
      - NOT_MET: the criterion's condition is checked and found false,
        based on real, present data (this is a normal, often desirable
        outcome -- e.g. "high-risk complaint pattern: NOT_MET" is good
        news, not a problem).
      - UNKNOWN: the data needed to evaluate this criterion is missing,
        so the criterion genuinely cannot be checked either way -- this
        is different from NOT_MET and must never be silently collapsed
        into it.
      - NOT_APPLICABLE: the criterion does not apply to this case at all
        (e.g. a Manchester category criterion when no approved ruleset
        exists for ANY case yet, regardless of this case's data).
    """
    dec = result.decision
    ti = result.triage_input
    safety = result.safety_review
    ml = result.ml_prediction

    rows: list[dict] = []

    # Chief complaint present
    if ti.chiefcomplaint and str(ti.chiefcomplaint).strip():
        rows.append({
            "Criterion": "Chief complaint recorded",
            "Status": "MET",
            "Evidence": f'"{ti.chiefcomplaint}"',
            "Missing info": "",
        })
    else:
        rows.append({
            "Criterion": "Chief complaint recorded",
            "Status": "UNKNOWN",
            "Evidence": "Field is missing or blank.",
            "Missing info": "chiefcomplaint",
        })

    # Critical vitals complete
    if not safety.critical_missing_vitals:
        rows.append({
            "Criterion": "All critical vitals recorded",
            "Status": "MET",
            "Evidence": "temperature, heartrate, resprate, o2sat, sbp all present.",
            "Missing info": "",
        })
    else:
        rows.append({
            "Criterion": "All critical vitals recorded",
            "Status": "UNKNOWN",
            "Evidence": f"{len(safety.critical_missing_vitals)} of 5 critical vitals are missing.",
            "Missing info": ", ".join(safety.critical_missing_vitals),
        })

    # Critical physiology flagged (a real vital is present but in a dangerous range)
    if dec.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED" or any(
        f.startswith("CRITICAL_PHYSIOLOGY") for f in safety.data_quality_flags
    ):
        rows.append({
            "Criterion": "Critical physiology flagged",
            "Status": "MET",
            "Evidence": ", ".join(
                f for f in safety.data_quality_flags if f.startswith("CRITICAL_PHYSIOLOGY")
            ) or dec.classification_status,
            "Missing info": "",
        })
    elif safety.critical_missing_vitals:
        rows.append({
            "Criterion": "Critical physiology flagged",
            "Status": "UNKNOWN",
            "Evidence": "Cannot be determined -- one or more critical vitals needed to check this are missing.",
            "Missing info": ", ".join(safety.critical_missing_vitals),
        })
    else:
        rows.append({
            "Criterion": "Critical physiology flagged",
            "Status": "NOT_MET",
            "Evidence": "All recorded critical vitals are within the checked thresholds.",
            "Missing info": "",
        })

    # High-risk complaint pattern (added this session alongside the
    # is_safe_to_present fix -- see app/agents/safety_review_agent.py)
    if ti.chiefcomplaint:
        if safety.high_risk_complaint_detected:
            matched = [
                f.split(":", 1)[1]
                for f in safety.data_quality_flags
                if f.startswith("HIGH_RISK_COMPLAINT_PATTERN:")
            ]
            rows.append({
                "Criterion": "High-risk complaint pattern",
                "Status": "MET",
                "Evidence": f'Matched: {", ".join(matched)} (from "{ti.chiefcomplaint}").',
                "Missing info": "",
            })
        else:
            rows.append({
                "Criterion": "High-risk complaint pattern",
                "Status": "NOT_MET",
                "Evidence": (
                    f'"{ti.chiefcomplaint}" does not match any of the known '
                    f"high-risk complaint keywords."
                ),
                "Missing info": "",
            })
    else:
        rows.append({
            "Criterion": "High-risk complaint pattern",
            "Status": "UNKNOWN",
            "Evidence": "Cannot be checked -- chief complaint is missing.",
            "Missing info": "chiefcomplaint",
        })

    # Approved Manchester ruleset available -- NOT_APPLICABLE rather than
    # NOT_MET for every single case right now, since no ruleset has ever
    # been registered for ANY case in this project (see
    # app/rules/manchester_engine.py::register_approved_ruleset) -- this
    # is a project-wide fact, not something about this specific case's
    # data, so NOT_MET (implying this case specifically failed a check
    # that could otherwise pass) would misrepresent it.
    # Manchester ruleset status. Three genuinely different states now:
    #   - a clinician-APPROVED ruleset is active        -> MET
    #   - a PROVISIONAL (unvalidated research) ruleset
    #     is active (the default in this build)          -> NOT_MET, because
    #     "approved ruleset" specifically is NOT satisfied; the category was
    #     produced by an unapproved provisional ruleset. Reporting MET here
    #     would let a provisional category masquerade as an approved one.
    #   - no ruleset registered at all                    -> NOT_APPLICABLE
    status_is_provisional = str(
        getattr(dec, "classification_status", "")
    ).startswith("PROVISIONAL_MTS_CATEGORY")
    if dec.ruleset_id is not None and not status_is_provisional:
        rows.append({
            "Criterion": "Approved Manchester ruleset available",
            "Status": "MET",
            "Evidence": f"ruleset_id={dec.ruleset_id}",
            "Missing info": "",
        })
    elif dec.ruleset_id is not None and status_is_provisional:
        rows.append({
            "Criterion": "Approved Manchester ruleset available",
            "Status": "NOT_MET",
            "Evidence": (
                f"A PROVISIONAL, unvalidated research ruleset is active "
                f"(ruleset_id={dec.ruleset_id}). This is NOT a clinician-approved "
                "ruleset and NOT the official Manchester Triage System; the "
                "category below is provisional and requires clinician confirmation."
            ),
            "Missing info": "Clinical approval of the ruleset is still required.",
        })
    else:
        rows.append({
            "Criterion": "Approved Manchester ruleset available",
            "Status": "NOT_APPLICABLE",
            "Evidence": (
                "No ruleset is registered for ANY case in this "
                "project right now -- this reflects the project's current phase, "
                "not anything about this specific case."
            ),
            "Missing info": "",
        })

    # ML research estimate available. Dataset-specific:
    #   KTAS  -> KTAS model predicts a KTAS class
    #   MIMIC -> MIMIC acuity model predicts acuity, mapped to MTS display
    if ml.prediction_available:
        if ml.prediction_scale == "MIMIC_ACUITY_MAPPED_TO_MTS":
            evidence = (
                f"predicted_mimic_acuity={ml.predicted_mimic_acuity} "
                f"-> {ml.mapped_mts_category} "
                f"(model {ml.model_name} v{ml.model_version})"
            )
        else:
            evidence = (
                f"predicted_ktas_class={ml.predicted_ktas_class} "
                f"(model {ml.model_name} v{ml.model_version})"
            )
        rows.append({
            "Criterion": "ML research estimate available",
            "Status": "MET",
            "Evidence": evidence,
            "Missing info": "",
        })
    elif ti.source_dataset not in ("Kaggle-KTAS", "MIMIC-IV-ED-Demo-v2.2"):
        rows.append({
            "Criterion": "ML research estimate available",
            "Status": "NOT_APPLICABLE",
            "Evidence": (
                f"No model has been trained for {ti.source_dataset} data."
            ),
            "Missing info": "",
        })
    else:
        rows.append({
            "Criterion": "ML research estimate available",
            "Status": "NOT_MET",
            "Evidence": ml.model_note or "No trained model registry found.",
            "Missing info": "",
        })

    # Leakage guard passed
    if safety.leakage_guard_passed:
        rows.append({
            "Criterion": "Leakage guard passed (no retrospective data used)",
            "Status": "MET",
            "Evidence": "No outcome/retrospective fields detected in triage-time input.",
            "Missing info": "",
        })
    else:
        leakage_flags = [f for f in safety.data_quality_flags if f.startswith("LEAKAGE_DETECTED")]
        rows.append({
            "Criterion": "Leakage guard passed (no retrospective data used)",
            "Status": "NOT_MET",
            "Evidence": "; ".join(leakage_flags) or "Leakage guard reported a failure.",
            "Missing info": "",
        })

    return rows


def _render_mimic_acuity_badge(fa) -> None:
    """
    Large, high-contrast coloured assessment badge for a MIMIC case, driven by
    the override-adjusted ML acuity (final_acuity_assessment). Colour + text
    together (never colour alone). Shows category, priority, max wait, and the
    predicted acuity; notes when the deterministic vital override escalated it.
    """
    from app.rules.acuity_mts_mapping import MTS_DISPLAY_HEX
    hexes = MTS_DISPLAY_HEX.get(fa.colour or "", {"bg": "#444444", "fg": "#ffffff"})
    bg, fg = hexes["bg"], hexes["fg"]
    name = (fa.category or "").split(" (")[0].upper()
    colour_word = (fa.colour or "").upper()

    st.markdown(
        f"""
        <div style="background:{bg};color:{fg};border-radius:12px;padding:18px 22px;margin-bottom:6px;">
          <div style="font-size:13px;letter-spacing:1px;opacity:0.9;">ASSESSMENT STATUS — PROVISIONAL · CLINICIAN REVIEW REQUIRED</div>
          <div style="font-size:40px;font-weight:800;line-height:1.1;margin-top:4px;">{colour_word}</div>
          <div style="font-size:24px;font-weight:700;">{name}</div>
          <div style="font-size:16px;margin-top:6px;">
            Priority {fa.priority} &nbsp;·&nbsp; Max wait {fa.max_wait_minutes} min
            &nbsp;·&nbsp; Predicted MIMIC acuity: {fa.ml_predicted_acuity}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if fa.override_applied:
        st.warning(
            f"⚠️ **Deterministic vital override applied ({fa.override_tier}).** "
            f"The ML model predicted acuity {fa.ml_predicted_acuity}, but "
            f"{', '.join(fa.override_flags)} forced escalation to **{fa.category}**. "
            "Escalate-only safety floor — clinician review required."
        )
    st.caption(
        "Predicted by the MIMIC acuity model and shown using a five-level "
        "colour/priority display. Provisional and not clinically approved; a "
        "clinician must confirm or override."
    )


def _render_ktas_assessment_header(stay_id, ml) -> None:
    """
    KTAS assessment header: KTAS research estimate only. NO Manchester/MTS
    colour card. Explicitly states MTS is not applied to KTAS.
    """
    top1, top2 = st.columns([1, 4])
    with top1:
        st.metric("Stay ID", str(stay_id))
    with top2:
        st.markdown("**Assessment status — KTAS research estimate**")
        if ml.prediction_available and ml.predicted_ktas_class is not None:
            st.markdown(
                f"Predicted **KTAS class {ml.predicted_ktas_class}** "
                f"· emergency estimate {fmt_pct(ml.emergency_research_estimate)} "
                f"· `{getattr(ml, 'model_name', '')}`"
            )
        else:
            st.markdown("KTAS model output not available.")
        st.info(
            "Manchester/MTS category is **not applied to KTAS cases** — KTAS is a "
            "different triage system. This is a KTAS research estimate only and "
            "requires clinician review."
        )


def _render_safety_crosscheck(dec, workflow_action) -> None:
    """
    Demoted deterministic safety cross-check line (the rules engine, no longer
    the headline). Surfaces critical/concern vital flags so a disagreement with
    the ML view is never hidden.
    """
    status = str(getattr(dec, "classification_status", ""))
    codes = [c for c in getattr(dec, "reason_codes", []) if c.startswith(("CRITICAL_", "CONCERN_"))]
    if status == "CRITICAL_PHYSIOLOGY_FLAGGED" or any(c.startswith("CRITICAL_") for c in codes):
        st.error(
            "🛑 **Deterministic safety cross-check: CRITICAL vital(s) flagged** — "
            + (", ".join(codes) if codes else status)
            + ". This is independent of the ML prediction; review urgently."
        )
    elif status == "PHYSIOLOGY_CONCERN_FLAGGED" or any(c.startswith("CONCERN_") for c in codes):
        st.warning(
            "⚠️ **Deterministic safety cross-check: concerning vital(s) flagged** — "
            + (", ".join(codes) if codes else status) + "."
        )
    else:
        st.caption("✓ Deterministic safety cross-check: no critical or concerning vitals flagged.")


def render_multi_agent_explanation(result) -> None:
    """
    Multi-Agent Team Explanation — the headline explanation section in Triage
    Review. A visible Generate button runs the REAL AutoGen four-agent team for
    the selected case. The agents only explain the already-computed result (they
    never create/change a category, diagnose, or recommend treatment). When Azure
    OpenAI is not configured, the button is not runnable and a clear message is
    shown; the static Supporting model evidence section below still works.
    """
    stay_id = result.stay_id
    st.markdown("### 🤖 Multi-Agent Team Explanation")

    if not azure_openai_configured():
        st.info("Multi-agent explanation unavailable: Azure OpenAI is not configured.")
        st.caption(
            "The agents explain the already-computed result and require Azure "
            "OpenAI. The Supporting model evidence section below does not require it."
        )
        return

    cases_path = settings.processed_dir / "streamlit_runtime_cases.jsonl"
    state_key = f"team_explanation_{result.triage_input.source_dataset}_{stay_id}"

    if st.button("Generate Multi-Agent Team Explanation", key=f"gen_team_{stay_id}"):
        with st.spinner("Running the four-agent team for this case..."):
            st.session_state[state_key] = asyncio.run(
                run_team_explanation(stay_id, cases_path=cases_path)
            )

    team_result = st.session_state.get(state_key)
    if not team_result:
        st.caption("Click the button to generate a multi-agent explanation for this case.")
        return

    status = team_result.get("status")
    if status == "NOT_CONFIGURED":
        st.info("Multi-agent explanation unavailable: Azure OpenAI is not configured.")
    elif status in ("SAFETY_FAIL", "ERROR"):
        st.error("🚨 Team explanation failed or was blocked by the safety validator:")
        for failure in team_result.get("safety_failures", []):
            st.error(f"• {failure}")
        if team_result.get("agent_turns"):
            with st.expander("Raw agent turns (failed — do not act on this)", expanded=False):
                for turn in team_result["agent_turns"]:
                    st.write(f"**{turn['agent']}:** {turn['text']}")
    else:
        for turn in team_result.get("agent_turns", []):
            with st.chat_message("assistant"):
                st.markdown(f"**{turn['agent']}**")
                st.write(turn["text"])
        st.success("✅ Team explanation passed the safety filter.")
        st.info("🔒 Clinician confirmation is ALWAYS required before any action on this output.")


def render_supporting_evidence(result) -> None:
    """
    Supporting model evidence — the static, computed explanation, demoted into a
    collapsed section beneath the Multi-Agent Team Explanation. Always available
    (needs no Azure). Shows triage-time inputs, missing fields, model confidence,
    class probabilities, and the deterministic override result. If a critical
    override / missing vital fired, it opens automatically.
    """
    ti = result.triage_input
    ml = result.ml_prediction
    fa = getattr(result, "final_acuity_assessment", None)
    is_mimic = ti.source_dataset == "MIMIC-IV-ED-Demo-v2.2"
    auto_open = bool(getattr(fa, "override_applied", False))

    with st.expander("Supporting model evidence", expanded=auto_open):
        st.markdown("**Triage-time inputs the model used**")
        cc = ti.chiefcomplaint or "—"
        vitals_bits = []
        for label, val, unit in [
            ("HR", ti.heartrate, "bpm"), ("RR", ti.resprate, "/min"),
            ("SpO₂", ti.o2sat, "%"), ("SBP", ti.sbp, "mmHg"),
            ("DBP", ti.dbp, "mmHg"), ("Temp", ti.temperature, f"°{(ti.temperature_unit or 'C')}"),
        ]:
            if val is not None:
                vitals_bits.append(f"{label} {val}{unit}")
        pain = ti.pain if ti.pain is not None else "—"
        st.write(f"Presenting complaint: **{cc}**")
        st.write("Vital signs: " + (", ".join(vitals_bits) if vitals_bits else "none recorded"))
        st.write(f"Pain score: **{pain}**")

        missing = [
            lbl for lbl, v in [
                ("chief complaint", ti.chiefcomplaint), ("heart rate", ti.heartrate),
                ("resp rate", ti.resprate), ("SpO₂", ti.o2sat), ("systolic BP", ti.sbp),
                ("temperature", ti.temperature), ("pain", ti.pain),
            ] if v is None
        ]
        if missing:
            st.warning("Missing triage-time fields: " + ", ".join(missing)
                       + " — these limit the prediction and are flagged for clinician review.")
        else:
            st.caption("No core triage-time fields are missing.")

        if not ml.prediction_available:
            st.info("No ML prediction is available for this case — " + (ml.model_note or ""))
            return

        st.markdown("**Model confidence**")
        st.write(f"Top-class confidence: {fmt_pct(ml.top_class_confidence)} "
                 "(research-grade model confidence, not a clinical probability).")
        _render_class_probabilities(ml, is_mimic)

        if is_mimic and fa is not None and fa.applicable:
            st.markdown("**Deterministic safety override**")
            if fa.override_applied:
                st.error(
                    f"⚠️ Override CHANGED the result. The model predicted acuity "
                    f"{fa.ml_predicted_acuity}, but {', '.join(fa.override_flags)} "
                    f"({fa.override_tier}) escalated the final category to "
                    f"**{fa.category}**. Escalate-only — it can raise urgency, never lower it."
                )
            else:
                st.success(
                    "✓ No extreme/critical-vital override triggered; the final "
                    f"category (**{fa.category}**) is the model's own prediction, unchanged."
                )


def _render_class_probabilities(ml, is_mimic: bool) -> None:
    """Labelled class-probability display (#9). For MIMIC, map each acuity to its
    category/colour so the numbers are clinically readable, as a compact table."""
    probs = ml.mimic_acuity_probabilities if is_mimic else ml.ktas_class_probabilities
    if not probs:
        return
    if is_mimic:
        from app.rules.acuity_mts_mapping import MIMIC_ACUITY_TO_MTS
        st.markdown("**Class probabilities (MIMIC acuity)**")
        rows = []
        for level in [1, 2, 3, 4, 5]:
            p = probs.get(str(level), probs.get(level, 0.0)) or 0.0
            disp = MIMIC_ACUITY_TO_MTS.get(level, {})
            rows.append({
                "Acuity": level,
                "Category": disp.get("category", "—"),
                "Max wait": f"{disp.get('max_wait_minutes', '—')} min",
                "Probability": fmt_pct(p),
            })
        st.dataframe(rows, width="stretch")
    else:
        st.markdown("**Class probabilities (KTAS class)**")
        rows = [
            {"KTAS class": k, "Probability": fmt_pct(probs[k])}
            for k in sorted(probs, key=lambda x: str(x))
        ]
        st.dataframe(rows, width="stretch")


def render_case_chat_panel(result) -> None:
    """
    In-page clinician chat scoped to the CURRENTLY selected case, rendered as a
    MAIN visible section (not hidden in an expander). The nurse does not re-enter
    the stay ID -- it is taken from `result`. Suggested-question buttons and the
    typed input use the SAME response path. Always renders a visible assistant
    answer (never a blank bubble); shows a clear message when Azure is absent.
    """
    stay_id = result.stay_id
    is_mimic = result.triage_input.source_dataset == "MIMIC-IV-ED-Demo-v2.2"

    st.markdown("### 💬 Ask about this case")
    if not azure_openai_configured():
        st.info("Case chat unavailable: Azure OpenAI is not configured.")
        return

    st.caption(f"Scoped to stay {stay_id} — you do not need to type the ID.")
    cases_path = settings.processed_dir / "streamlit_runtime_cases.jsonl"
    chat_key = f"triage_case_chat_{result.triage_input.source_dataset}_{stay_id}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    suggestions = (
        ["Why was this category chosen?", "What vitals affected the result?",
         "Was any override applied?", "What information is missing?"]
        if is_mimic else
        ["Why this KTAS class?", "What vitals affected the result?",
         "What information is missing?"]
    )
    cols = st.columns(len(suggestions))
    clicked = None
    for col, q in zip(cols, suggestions):
        if col.button(q, key=f"{chat_key}_sugg_{q}"):
            clicked = q

    for msg in st.session_state[chat_key]:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    typed = st.chat_input(f"Ask about this case (stay {stay_id})...", key=f"{chat_key}_input")
    prompt = clicked or typed
    if prompt:
        st.session_state[chat_key].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)
        full_prompt = f"Regarding stay {stay_id}: {prompt}"
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                res = asyncio.run(run_single_question(full_prompt, cases_path=cases_path))
            status = res.get("status")
            if status == "SAFETY_FAIL":
                answer = "🚨 Reply blocked by safety checks: " + str(
                    res.get("reply_text") or res.get("reason") or ""
                )
                st.error(answer)
            elif status == "NOT_CONFIGURED":
                answer = res.get("reply_text") or (
                    "Case chat unavailable: Azure OpenAI is not configured."
                )
                st.info(answer)
            else:
                answer = (
                    res.get("reply_text")
                    or res.get("answer")
                    or res.get("reply")
                    or "No response text returned by the chat agent."
                )
                st.write(answer)
        st.session_state[chat_key].append({"role": "assistant", "content": answer})


def render_assessment_card(result) -> None:
    """
    Trial Matcher-style assessment card: every field a clinician needs to
    triage their trust in this case's automated output, in one place,
    without having to piece it together from scattered metrics.

    Covers exactly the nine fields requested: assessment status, research
    model output, evidence used, matched indicators / reason codes,
    missing information, uncertainty, workflow action, clinician review
    requirement, and audit/log reference. Every value here is read
    directly from the already-computed WorkflowResult -- this function
    adds no new decision logic, it only lays out what already exists.
    """
    dec = result.decision
    ml = result.ml_prediction
    ti = result.triage_input
    safety = result.safety_review
    fa = getattr(result, "final_acuity_assessment", None)
    is_mimic = ti.source_dataset == "MIMIC-IV-ED-Demo-v2.2"
    is_ktas = ti.source_dataset == "Kaggle-KTAS"

    with st.container(border=True):
        # ── 1. Assessment status — dataset-aware headline ──────────────
        if is_mimic and fa is not None and fa.applicable and fa.category:
            _render_mimic_acuity_badge(fa)
        elif is_ktas:
            _render_ktas_assessment_header(result.stay_id, ml)
        else:
            # Fallback (e.g. other datasets / no prediction): keep a plain
            # status line, never a coloured MTS badge.
            top1, top2, top3 = st.columns([1, 3, 1])
            with top1:
                st.metric("Stay ID", str(result.stay_id))
            with top2:
                st.markdown("**Assessment status**")
                st.markdown(f"`{dec.classification_status}` — no category assigned")
            with top3:
                st.markdown("**Workflow action**")
                st.markdown(_workflow_action_badge(result.workflow_action))

        # ── Deterministic safety cross-check (demoted, always shown) ────
        # The rules engine is no longer the headline; it runs underneath as a
        # deterministic vital-sign cross-check. Its disagreement with the ML
        # view (e.g. ML says low acuity, vitals say critical) is the single most
        # important safety signal, so it is always surfaced here.
        _render_safety_crosscheck(dec, result.workflow_action)

        st.markdown("---")

        # ── 2 & 6. Research model output + uncertainty ──────────────────
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Research model output**")
            if not ml.prediction_available:
                st.write("Not available — " + ml.model_note)
            elif is_mimic:
                st.write(f"Predicted MIMIC acuity: **{ml.predicted_mimic_acuity}** (ESI)")
                st.write(
                    f"Mapped display: **{ml.mapped_mts_category}** "
                    f"(priority {ml.mapped_mts_priority}, max wait {ml.mapped_mts_max_wait_minutes} min)"
                )
                st.caption(f"Model {ml.model_name} · {ml.model_note}")
            else:  # KTAS
                st.write(
                    f"Predicted KTAS class: **{ml.predicted_ktas_class}** "
                    f"(model {ml.model_name} v{ml.model_version})"
                )
                st.write(f"Emergency estimate: {fmt_pct(ml.emergency_research_estimate)}")
                st.caption("Manchester/MTS is not applied to KTAS cases.")
        with c2:
            st.markdown("**Uncertainty**")
            if ml.prediction_available:
                st.write(f"Top-class confidence: {fmt_pct(ml.top_class_confidence)}")
                st.caption("Research-grade model confidence — not a clinical probability.")
            else:
                st.write("N/A — no model prediction to assess confidence on.")
        with c3:
            st.markdown("**Clinician review requirement**")
            st.write("🔒 **Always required**" if dec.requires_clinician_review else "Not flagged (unexpected)")
            st.caption("This output is never final without human confirmation.")

        st.markdown("---")

        # ── 3. Evidence used ──────────────────────────────────────────
        st.markdown("**Evidence used** (triage-time inputs only — no retrospective data)")
        e1, e2, e3, e4, e5, e6, e7 = st.columns(7)
        e1.metric("Chief complaint", (ti.chiefcomplaint or "⚠️ Missing")[:18])
        e2.metric(f"Temp (°{ti.temperature_unit})", ti.temperature if ti.temperature is not None else "⚠️")
        e3.metric("HR", ti.heartrate if ti.heartrate is not None else "⚠️")
        e4.metric("RR", ti.resprate if ti.resprate is not None else "⚠️")
        e5.metric("SpO2", ti.o2sat if ti.o2sat is not None else "⚠️")
        e6.metric("SBP", ti.sbp if ti.sbp is not None else "⚠️")
        e7.metric("Pain", ti.nrs_pain if ti.nrs_pain is not None else (ti.pain or "⚠️"))

        # ── 4. Matched indicators / reason codes ────────────────────────
        st.markdown("**Matched indicators / reason codes**")
        if dec.reason_codes:
            st.write(" · ".join(f"`{c}`" for c in dec.reason_codes))
        else:
            st.write("None.")

        # ── 5. Missing information ──────────────────────────────────────
        st.markdown("**Missing information**")
        missing = result.data_validation.missing_required_fields
        non_informative = result.data_validation.non_informative_fields
        if missing or non_informative:
            if missing:
                st.write(f"⚠️ Missing required fields: {', '.join(missing)}")
            if non_informative:
                st.write(f"ℹ️ Non-informative fields: {', '.join(non_informative)}")
        else:
            st.write("None — all required triage-time fields present.")

        if not safety.is_safe_to_present:
            st.error(
                "🔴 Safety review flags this case as not safe to present without "
                "clinician attention: " + ", ".join(safety.critical_missing_vitals or dec.reason_codes)
            )

        st.markdown("---")

        # ── Trial Matcher-style criteria table ───────────────────────────
        # One row per individually-checkable criterion (Criterion /
        # Status / Evidence / Missing info), matching the structured
        # criteria-checked -> matched/not-matched/unknown -> evidence ->
        # missing-data pattern this card's docstring describes. See
        # build_criteria_table() above for the full status semantics
        # (MET / NOT_MET / UNKNOWN / NOT_APPLICABLE are NOT
        # interchangeable -- UNKNOWN specifically means "cannot be
        # checked due to missing data", never silently treated as
        # NOT_MET).
        st.markdown("**Criteria checked**")
        criteria_rows = build_criteria_table(result)
        status_icons = {
            "MET": "✅ MET",
            "NOT_MET": "⬜ NOT_MET",
            "UNKNOWN": "❓ UNKNOWN",
            "NOT_APPLICABLE": "➖ NOT_APPLICABLE",
        }
        display_rows = [
            {
                "Criterion": r["Criterion"],
                "Status": status_icons.get(r["Status"], r["Status"]),
                "Evidence": r["Evidence"],
                "Missing info": r["Missing info"] or "—",
            }
            for r in criteria_rows
        ]
        st.dataframe(display_rows, width="stretch", hide_index=True)

        st.markdown("---")

        # ── 9. Audit / log reference ─────────────────────────────────────
        st.markdown("**Audit / log reference**")
        run_start = result.audit.get("run_start_utc", "unknown")
        run_end = result.audit.get("run_end_utc", "unknown")
        st.caption(
            f"stay_id={result.stay_id} · workflow_version={result.audit.get('workflow_version', 'unknown')} "
            f"· run_start_utc={run_start} · run_end_utc={run_end}"
        )
        st.caption(
            "Full workflow output (including this audit record) is shown in the "
            "'Full workflow output (JSON)' expander below, and any saved clinician "
            "review for this stay appears in the Clinician Review section and the "
            "Audit Log tab."
        )


records = load_cases()

tab_triage, tab_followup, tab_governance, tab_queue, tab_audit, tab_models = st.tabs(
    [
        "🩺 Triage Review",
        "🔄 Follow-Up Comparison",
        "🔒 Governance",
        "📋 Review Queue",
        "📜 Audit Log",
        "📊 Model Performance",
    ]
)


# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — TRIAGE REVIEW
# ═══════════════════════════════════════════════════════════════════════════
with tab_triage:
    st.subheader("Select ED Stay for Review")

    selected_record = render_dataset_filtered_case_selector(records, "triage_review")
    case = EDTriageCase(**selected_record)

    with st.spinner("Running deterministic triage workflow..."):
        result = run_workflow(case, include_llm_explanation=False)

    # Append this run to the persistent workflow-run audit log (one record per
    # run). On Azure's ephemeral storage this is not a durable trail; a hardened
    # deployment should route it to Azure Blob/Table/Cosmos.
    try:
        import uuid as _uuid
        from datetime import datetime, timezone
        from app.schemas.workflow_run import build_workflow_run_record
        from app.storage.workflow_run_repository import append_workflow_run
        _rec = build_workflow_run_record(
            result,
            run_id=str(_uuid.uuid4()),
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        append_workflow_run(settings.processed_dir / "workflow_runs.jsonl", _rec)
    except Exception:
        pass  # audit logging must never break the assessment view

    ti = result.triage_input
    labels = result.retrospective_labels

    st.markdown("---")
    render_assessment_card(result)

    st.markdown("---")
    # Multi-Agent Team Explanation is the headline explanation section.
    render_multi_agent_explanation(result)
    # Static computed evidence, demoted to a collapsed section beneath it.
    render_supporting_evidence(result)

    st.markdown("---")
    # In-page clinician chat scoped to THIS case (no need to re-enter stay ID).
    render_case_chat_panel(result)

    st.markdown("---")
    with st.expander(
        "📋 Triage-Time Inputs (available at triage — no retrospective data)",
        expanded=True,
    ):
        v_col1, v_col2, v_col3, v_col4 = st.columns(4)
        v_col1.metric("Chief Complaint", ti.chiefcomplaint or "⚠️ MISSING")
        v_col2.metric("Arrival", ti.arrival_transport or "Unknown")
        v_col3.metric("Gender", ti.gender or "Not recorded")
        v_col4.metric("Age", ti.age if ti.age is not None else "Not recorded")

        st.markdown("**Vital Signs**")
        v1, v2, v3, v4, v5, v6, v7 = st.columns(7)
        v1.metric(f"Temp (°{ti.temperature_unit})", ti.temperature if ti.temperature is not None else "⚠️ MISSING")
        v2.metric("HR (bpm)", ti.heartrate if ti.heartrate is not None else "⚠️ MISSING")
        v3.metric("RR (/min)", ti.resprate if ti.resprate is not None else "⚠️ MISSING")
        v4.metric("SpO2 (%)", ti.o2sat if ti.o2sat is not None else "⚠️ MISSING")
        v5.metric("SBP (mmHg)", ti.sbp if ti.sbp is not None else "⚠️ MISSING")
        v6.metric("DBP (mmHg)", ti.dbp if ti.dbp is not None else "⚠️ MISSING")
        v7.metric(
            "Pain (0-10)",
            ti.nrs_pain if ti.nrs_pain is not None else (ti.pain or "⚠️ MISSING"),
        )

        if result.data_validation.missing_required_fields:
            st.warning(
                f"Missing fields: {', '.join(result.data_validation.missing_required_fields)}"
            )
        if result.data_validation.non_informative_fields:
            st.info(
                f"Non-informative fields: {', '.join(result.data_validation.non_informative_fields)}"
            )

    st.markdown("---")
    st.subheader("✅ Clinician Review")

    review_log_path = settings.processed_dir / "human_reviews.jsonl"
    existing_reviews = get_reviews_for_stay(review_log_path, case.stay_id)

    if existing_reviews:
        st.markdown(f"**{len(existing_reviews)} existing review(s) for this stay:**")
        for rev in existing_reviews:
            with st.expander(
                f"{rev.reviewer_role.upper()} — {rev.review_status} — {rev.created_at_utc[:19]}"
            ):
                st.json(rev.model_dump(mode="json"))
    else:
        st.info("No reviews saved for this stay yet.")

    with st.form(f"review_form_{case.stay_id}"):
        st.markdown("**Submit clinician review**")

        r1, r2 = st.columns(2)
        reviewer_role = r1.selectbox(
            "Reviewer role",
            ["triage_nurse", "emergency_physician", "researcher", "supervisor"],
            key=f"role_{case.stay_id}",
        )
        review_status = r2.selectbox(
            "Review decision",
            [
                "ACCEPTED_AS_PRESENTED",
                "OVERRIDE_REQUIRED",
                "ESCALATION_REQUIRED",
                "REQUEST_MORE_INFORMATION",
                "REJECTED_DATA_QUALITY",
                "NOT_REVIEWED",
            ],
            key=f"status_{case.stay_id}",
        )

        default_comment = (
            "Missing or limited triage fields — human data review required."
            if result.data_validation.requires_human_data_review
            else "Triage data complete. Review completed."
        )
        review_comment = st.text_area(
            "Review notes", value=default_comment, key=f"comment_{case.stay_id}"
        )

        submitted = st.form_submit_button("💾 Save Review to Audit Log")

        if submitted:
            record = HumanReviewRecord(
                review_id=str(uuid4()),
                stay_id=case.stay_id,
                source_dataset=case.source_dataset,
                reviewer_role=reviewer_role,
                review_status=review_status,
                review_comment=review_comment,
                created_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            append_human_review(review_log_path, record)
            st.success("✅ Review saved to audit log.")
            st.rerun()

    st.markdown("---")
    st.subheader("🔍 Clinical Safety Assessment — full detail")
    st.caption(
        "Full drill-down on the assessment card above. Deterministic physiology "
        "and pathway analysis — no LLM. No MTS category assigned without an "
        "approved clinical ruleset. Clinician confirmation ALWAYS required."
    )

    dec = result.decision
    if dec.priority is not None:
        st.markdown(
            f"**{dec.category}** — max wait {dec.max_wait_minutes} minutes — "
            f"status: {dec.classification_status}"
        )
    else:
        st.error(
            f"⚠️ **{dec.classification_status.replace('_', ' ')}**\n\n"
            "No Manchester category assigned. Clinician review required."
        )

    if dec.reason_codes:
        with st.expander("Rules engine reason codes"):
            for code in dec.reason_codes:
                st.code(f"{_flag_icon(code)} {code}")

    st.info("🔒 **Clinician confirmation is ALWAYS required** before any action on this output.")

    safety = result.safety_review
    if safety.data_quality_flags:
        with st.expander(
            f"⚠️ Safety Flags ({len(safety.data_quality_flags)} issues detected)",
            expanded=True,
        ):
            for flag in safety.data_quality_flags:
                st.write(f"{_flag_icon(flag)} {flag}")
    else:
        st.success("✅ No deterministic vital-sign safety flags detected")

    ml = result.ml_prediction
    is_mimic_case = result.triage_input.source_dataset == "MIMIC-IV-ED-Demo-v2.2"
    st.markdown("---")

    if is_mimic_case:
        # ── MIMIC-IV-ED acuity model — full detail (NO KTAS labels) ──────────
        st.subheader("🧠 MIMIC-IV-ED Acuity Model — full detail")
        st.caption(
            "Research-grade estimate trained on public MIMIC-IV-ED Demo acuity "
            "labels. Research estimate only. Not official Manchester Triage "
            "System. Clinical validation required before any use."
        )
        if ml.prediction_available:
            m1, m2, m3 = st.columns(3)
            m1.metric(
                "Predicted MIMIC acuity",
                ml.predicted_mimic_acuity if ml.predicted_mimic_acuity is not None else "N/A",
                help=f"ESI acuity 1 (most urgent) .. 5 (least). Model: {ml.model_name}",
            )
            m2.metric("Mapped category", ml.mapped_mts_category or "N/A",
                      help=f"Priority {ml.mapped_mts_priority}, max wait {ml.mapped_mts_max_wait_minutes} min")
            m3.metric("Model confidence", fmt_pct(ml.top_class_confidence),
                      help="Top-class probability — not a clinical probability")
            _render_class_probabilities(ml, is_mimic=True)
            st.warning(
                f"⚠️ Model: **{ml.model_name}** v{ml.model_version} · target "
                "mimic_demo.acuity.v1 — research estimate only. Predicted acuity "
                "maps to the five-level colour/priority display. Clinician review required."
            )
        else:
            st.info(f"MIMIC acuity prediction not available: {ml.model_note}.")

    else:
        # ── KTAS model — full detail (unchanged for KTAS cases) ──────────────
        st.subheader("🤖 KTAS Research Estimate — full detail")
        st.caption(
            "Research-grade estimate trained on the public Kaggle KTAS dataset. "
            "NOT a Manchester triage category. NOT a validated clinical risk score."
        )
        if ml.prediction_available:
            ml1, ml2, ml3 = st.columns(3)
            ml1.metric(
                "Predicted KTAS Class",
                ml.predicted_ktas_class if ml.predicted_ktas_class is not None else "N/A",
                help=f"1=most critical .. 5=least critical (research estimate). Model: {ml.model_name}",
            )
            ml2.metric(
                "Emergency Estimate",
                fmt_pct(ml.emergency_research_estimate),
                help="P(KTAS_expert in {1,2,3}) — research only",
            )
            ml3.metric(
                "Model Confidence",
                fmt_pct(ml.top_class_confidence),
                help="Top-class probability from the trained model — not a clinical probability",
            )
            _render_class_probabilities(ml, is_mimic=False)
            st.warning(
                f"⚠️ Model: **{ml.model_name}** v{ml.model_version} — "
                "research estimate only. Clinical validation required before any use."
            )
        else:
            st.info(
                f"ML prediction not available: {ml.model_note}. "
                "Train models with: `python ml_training/train_all_models.py`"
            )

    with st.expander("🔍 Full workflow output (JSON)"):
        st.json(result.model_dump(mode="json"))


# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — FOLLOW-UP COMPARISON (explicit, user-declared workflow capability)
# ═══════════════════════════════════════════════════════════════════════════
with tab_followup:
    st.subheader("🔄 Follow-Up Triage Comparison")
    st.warning(
        "**This is a demonstration workflow capability, not automatic "
        "repeat-patient detection.** The public KTAS dataset has no real "
        "patient identifier, so this system never tries to guess which "
        "rows belong to the same person. You must explicitly declare which "
        "two stay_ids represent the same patient at different timepoints. "
        "Once declared, the comparison below is fully deterministic."
    )

    # Restricted to one dataset at a time, deliberately. KTAS and MIMIC
    # demo are two separate public/synthetic datasets with no shared
    # patient population -- a "same patient, different visit" link
    # across them would not correspond to any real or even plausible
    # scenario, unlike linking two stay_ids within the same dataset
    # (which is itself already a constructed demonstration, per the
    # warning above, but at least a coherent one). The
    # dataset_consistency_warning already built into
    # app/agents/followup_comparison_agent.py still fires if this
    # restriction is ever bypassed (e.g. a future caller of the
    # underlying API route directly), but preventing the confusing
    # choice in this picker is strictly better than relying on a
    # post-submission warning alone.
    followup_ktas_records = [r for r in records if r.get("source_dataset") == "Kaggle-KTAS"]
    followup_mimic_records = [
        r for r in records if r.get("source_dataset") == "MIMIC-IV-ED-Demo-v2.2"
    ]
    followup_dataset_options = []
    if followup_ktas_records:
        followup_dataset_options.append(f"Kaggle-KTAS ({len(followup_ktas_records)} stays)")
    if followup_mimic_records:
        followup_dataset_options.append(
            f"MIMIC-IV-ED-Demo-v2.2 ({len(followup_mimic_records)} stays)"
        )

    followup_dataset_choice = st.radio(
        "Dataset for this comparison (both stays must be from the same dataset)",
        followup_dataset_options,
        horizontal=True,
        key="followup_dataset_filter",
    )
    stay_id_choices = sorted(
        {
            r["stay_id"]
            for r in (
                followup_ktas_records
                if followup_dataset_choice.startswith("Kaggle-KTAS")
                else followup_mimic_records
            )
        }
    )

    with st.form("followup_link_form"):
        f1, f2 = st.columns(2)
        previous_stay_id = f1.selectbox(
            "Previous stay_id (earlier visit)", stay_id_choices, key="followup_prev",
        )
        new_stay_id = f2.selectbox(
            "New stay_id (this visit — declared as a follow-up)",
            stay_id_choices,
            index=min(1, len(stay_id_choices) - 1),
            key="followup_new",
        )
        linked_by = st.text_input("Linked by (your name / role)", value="dylan_demo")
        link_reason = st.text_area(
            "Reason for linking these two stays",
            value="Same patient returned to ED; demonstrating escalation workflow.",
        )
        is_demo = st.checkbox(
            "This is a constructed/demonstration scenario (not a real linked patient)",
            value=True,
            help=(
                "Leave this checked for any walkthrough or test scenario. "
                "Only uncheck this if you are recording a genuine clinical "
                "assertion that two real stay_ids are the same patient."
            ),
        )
        submitted = st.form_submit_button("Compare")

    if submitted:
        if previous_stay_id == new_stay_id:
            st.error("Previous and new stay_id must be different.")
        else:
            previous_case = EDTriageCase(**next(r for r in records if r["stay_id"] == previous_stay_id))
            new_case = EDTriageCase(**next(r for r in records if r["stay_id"] == new_stay_id))

            previous_result = run_workflow(previous_case, include_llm_explanation=False)
            new_result = run_workflow(new_case, include_llm_explanation=False)

            link = FollowUpLinkRequest(
                previous_stay_id=previous_stay_id,
                new_stay_id=new_stay_id,
                linked_by=linked_by,
                link_reason=link_reason,
                is_demonstration_scenario=is_demo,
            )
            comparison = compare_follow_up(link, previous_result, new_result)

            followup_log_path = settings.processed_dir / "followup_comparisons.jsonl"
            append_followup_comparison(followup_log_path, comparison)

            st.markdown("---")
            if comparison.is_demonstration_scenario:
                st.info("🧪 DEMONSTRATION SCENARIO — not a real linked patient encounter.")

            if comparison.dataset_consistency_warning:
                st.warning(comparison.dataset_consistency_warning)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Previous status", comparison.previous_classification_status)
            c2.metric("New status", comparison.new_classification_status)
            c3.metric(
                "Escalation detected",
                "🔴 YES" if comparison.escalation_detected else "🟢 NO",
            )
            c4.metric("Workflow action", comparison.workflow_action)

            st.markdown("**Escalation note (shown to clinician):**")
            st.write(comparison.escalation_note)

            st.markdown("**Vital-by-vital comparison:**")
            delta_table = [
                {
                    "Field": d.field_name,
                    "Previous": d.previous_value,
                    "New": d.new_value,
                    "Unit": d.unit,
                    "Direction": d.direction,
                    "Notable": "⚠️ Yes" if d.clinically_notable else "No",
                }
                for d in comparison.vital_deltas
            ]
            st.dataframe(delta_table, width="stretch")

            st.caption(
                f"requires_clinician_review: {comparison.requires_clinician_review} — "
                "this comparison does not assign a Manchester or KTAS category itself; "
                "it reports the two deterministic decisions already produced for each stay."
            )

    st.markdown("---")
    st.subheader("📜 Follow-up history for a stay")
    history_stay_id = st.selectbox(
        "View history for stay_id", stay_id_choices, key="followup_history_select"
    )
    followup_log_path = settings.processed_dir / "followup_comparisons.jsonl"
    history = get_followup_history_for_stay(followup_log_path, history_stay_id)
    if not history:
        st.caption("No follow-up comparisons recorded for this stay yet.")
    else:
        history_table = [
            {
                "Previous stay_id": h.previous_stay_id,
                "New stay_id": h.new_stay_id,
                "Linked by": h.linked_by,
                "Escalation detected": "🔴 Yes" if h.escalation_detected else "🟢 No",
                "Workflow action": h.workflow_action,
                "Demonstration": "Yes" if h.is_demonstration_scenario else "No",
            }
            for h in history
        ]
        st.dataframe(history_table, width="stretch")


# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — CLINICIAN CHAT (real AutoGen agent)
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# TAB 4 — GOVERNANCE DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════
with tab_governance:
    st.subheader("🔒 Responsible AI Governance Dashboard")
    st.caption(
        "Five-stage review gate. This is evidence for review — not a clinical certification."
    )

    dataset_audit = load_json_file(settings.processed_dir / "dataset_audit_report.json")
    missing_inputs = load_json_file(settings.processed_dir / "missing_triage_inputs_report.json")
    schema_report = load_json_file(settings.processed_dir / "schema_report.json")
    model_eval = load_json_file(settings.processed_dir / "model_evaluation_report.json")
    review_log_path = settings.processed_dir / "human_reviews.jsonl"
    human_reviews = read_human_reviews(review_log_path)

    # Computed live from records (the same merged KTAS+MIMIC list every
    # other tab uses), not hardcoded -- this was previously a fixed
    # string ("Kaggle Emergency Service - KTAS Triage Application
    # (public, 1267 rows)") that went stale the moment MIMIC cases were
    # merged into the app elsewhere, since it kept claiming KTAS was the
    # only dataset loaded. A hardcoded replacement under different
    # specific numbers would only repeat that same mistake the next time
    # the loaded datasets or their sizes change.
    _dataset_counts = {}
    for r in records:
        _dataset_counts[r.get("source_dataset", "Unknown")] = (
            _dataset_counts.get(r.get("source_dataset", "Unknown"), 0) + 1
        )
    _dataset_description_parts = [
        f"{name} (public, {count} rows)" for name, count in sorted(_dataset_counts.items())
    ]
    dataset_description = (
        " + ".join(_dataset_description_parts) if _dataset_description_parts else "No data loaded"
    )

    reviewed_stay_ids = {int(r.stay_id) for r in human_reviews}
    missing_cases = (missing_inputs or {}).get("missing_cases", [])
    missing_stay_ids = {int(c["stay_id"]) for c in missing_cases if c.get("stay_id")}
    unreviewed_missing = missing_stay_ids - reviewed_stay_ids

    blocking_issues = ["No clinician-approved Manchester triage ruleset configured."]
    if unreviewed_missing:
        blocking_issues.append(
            f"{len(unreviewed_missing)} cases with missing triage data have no human review."
        )
    if not schema_report:
        blocking_issues.append("Schema verification report not found.")

    # Research-demo readiness and clinical-use readiness are tracked
    # separately. BUG FIX (found during third-party review): this
    # previously always hardcoded NOT_READY_FOR_CLINICAL_USE regardless of
    # the unreviewed_missing computation just above, even though that
    # computation is the same one app/api/governance_routes.py's backend
    # API route already uses to correctly distinguish
    # READY_FOR_RESEARCH_DEMO_ONLY from NOT_READY_FOR_CLINICAL_USE. The
    # mismatch meant a fully-reviewed dataset still displayed as if no
    # review had happened. Clinical-use readiness is intentionally always
    # blocked here -- that verdict does not depend on review completeness,
    # since no clinician-approved Manchester ruleset exists regardless.
    research_demo_ready = not unreviewed_missing and schema_report is not None
    research_demo_status = (
        "READY_FOR_RESEARCH_DEMO_ONLY" if research_demo_ready else "PARTIAL — outstanding review items below"
    )

    rd_col, cu_col = st.columns(2)
    with rd_col:
        if research_demo_ready:
            st.success(f"🟢 Research demo readiness: **{research_demo_status}**")
        else:
            st.warning(f"🟡 Research demo readiness: **{research_demo_status}**")
    with cu_col:
        st.error("🔴 Clinical-use readiness: **NOT_READY_FOR_CLINICAL_USE**")

    st.markdown("**Blocking Issues (for clinical use):**")
    for issue in blocking_issues:
        st.error(f"• {issue}")

    st.markdown("---")
    st.markdown("### Five-Stage Review Gate")

    stages = {
        "1. Intake": {
            "status": "PASS",
            "description": "System purpose, dataset, deployment context, and risk classification documented.",
            "evidence": {
                "system_name": "AI Triage Agentic Workflow",
                "purpose": "Research prototype for emergency department triage decision support",
                "intended_use": "Support triage nurses with structured evidence — not autonomous decisions",
                "not_intended_use": "Autonomous triage, diagnosis, treatment, or clinical routing",
                "dataset": dataset_description,
                "orchestration_framework": "autogen-agentchat 0.7.5 for the explanation/chat layer only",
                "deployment_status": "Research prototype — not deployed clinically",
                "risk_classification": "HIGH RISK — emergency care system",
            },
        },
        "2. Scope": {
            "status": "PASS",
            "description": "Risk tier set. High-risk because it relates to emergency care.",
            "evidence": {
                "risk_tier": "HIGH",
                "reason": "Emergency department triage; future UHL/MIMIC validation will involve real patient data",
                "required_evaluations": [
                    "Data completeness and quality", "Leakage guard verification",
                    "Under-triage rate", "Subgroup performance",
                    "Forbidden-phrase detection in LLM/AutoGen outputs",
                    "Missing-vital sensitivity analysis",
                ],
            },
        },
        "3. Assess": {
            "status": "PASS" if dataset_audit else "WARNING",
            "description": "Evaluation pipeline: data quality, leakage, missing data, ML metrics.",
            "evidence": {
                "dataset_audit": "PASS" if dataset_audit else "MISSING — run scripts/run_ktas_pipeline.py",
                "schema_verification": "PASS" if schema_report else "MISSING",
                "leakage_guard": "PASS — retrospective KTAS fields excluded from triage input and ML features",
                "missing_data_report": f"{len(missing_cases)} cases with missing triage fields",
                "human_reviews": f"{len(human_reviews)} clinician reviews logged",
                "ktas_model_report": "PASS" if model_eval else "MISSING",
                "unit_tests": "Run pytest locally for the current count; see KTAS_CHANGELOG.md for test-count history per change.",
            },
        },
        "4. Probe": {
            "status": "PARTIAL",
            "description": "Clinician or domain reviewer manually tests realistic, edge-case, and unsafe inputs.",
            "evidence": {
                "human_review_records": len(human_reviews),
                "missing_cases_reviewed": len(missing_stay_ids & reviewed_stay_ids),
                "missing_cases_unreviewed": len(unreviewed_missing),
                "red_team_testing": "Pending — adversarial triage and adversarial chat-agent prompts not yet systematically tested",
                "edge_case_testing": "Use Review Queue to probe missing-data and high-risk cases",
            },
        },
        "5. Decide": {
            "status": "NOT_READY_FOR_CLINICAL_USE",
            "description": "Release decision with rationale and evidence links.",
            "evidence": {
                "decision": "NOT_READY_FOR_CLINICAL_USE",
                "rationale": [
                    "System is a research prototype — no clinical validation completed",
                    "Manchester rules engine not configured (no clinician-approved ruleset)",
                    "ML models trained on a small public KTAS dataset (1267 rows), not validated against UHL ground truth",
                    "AutoGen chat agent has not been exercised against a live model in this environment — only against scripted test responses",
                    "Formal EU AI Act Annex IV documentation, post-market monitoring, and qualified legal review required for regulated deployment",
                ],
                "approved_for": "Research, demonstration, and further development only",
            },
        },
    }

    for stage_name, stage_data in stages.items():
        status = stage_data["status"]
        icon = "✅" if status == "PASS" else "⚠️" if status in ("WARNING", "PARTIAL") else "🔴"
        with st.expander(f"{icon} {stage_name}: {stage_data['description']}"):
            st.markdown(f"**Status:** {status}")
            st.json(stage_data["evidence"])

    st.markdown("---")
    st.markdown("### Governance Controls")

    controls = {
        "Leakage guard": ("PASS", "Retrospective KTAS fields (KTAS_expert, KTAS_RN, mistriage, Error_group, Diagnosis in ED, Disposition, Length of stay, KTAS duration) excluded from all triage inputs and ML features."),
        "Data validation agent": ("PASS", "Missing and non-informative fields flagged on every case."),
        "Clinical safety rules": ("ACTIVE", "Vital-sign safety detection always active. A PROVISIONAL, unvalidated Manchester-style research ruleset is active by default, so cases receive provisional categories. These are NOT the official Manchester Triage System, NOT clinically approved, and require clinician review on every output."),
        "MTS pathway status": ("PROVISIONAL_RULESET_ACTIVE", "Complaint pathways run under a provisional research ruleset (heuristics informed by published MTS discriminators, not the licensed MTS flowcharts). No clinician-approved ruleset is registered; set PROVISIONAL_MTS_MODE=off to fully gate."),
        "KTAS-to-Manchester mapping": ("NOT_IMPLEMENTED", "KTAS and Manchester are different scales. No conversion exists anywhere in this codebase."),
        "AutoGen chat agent safety filter": ("PASS", "Every chat reply is checked against the shared forbidden-phrase filter and a human-review-reference requirement before being shown."),
        "AutoGen agent tool scope": ("PASS", "The agent's only tool reads already-computed deterministic evidence; it has no tool that can set a triage category or modify a vital sign."),
        "Clinician review requirement": ("PASS", "requires_clinician_review=True on ALL rules-engine outputs."),
        "Audit logging": ("PASS", "All clinician reviews logged with timestamp, role, and reason."),
        "Schema verification": ("PASS" if schema_report else "MISSING", "Column headers verified against the KTAS CSV adapter's expected schema."),
        "Clinical use guardrail": ("PASS", "System explicitly declares NOT_FOR_CLINICAL_USE everywhere."),
    }

    for control_name, (status, detail) in controls.items():
        if status == "PASS":
            st.success(f"✅ **{control_name}**: {detail}")
        elif status == "MISSING":
            st.warning(f"⚠️ **{control_name}**: {detail}")
        else:
            st.info(f"ℹ️ **{control_name}** ({status}): {detail}")


# ═══════════════════════════════════════════════════════════════════════════
# TAB 5 — REVIEW QUEUE
# ═══════════════════════════════════════════════════════════════════════════
with tab_queue:
    st.subheader("📋 Human Review Queue")

    missing_inputs_data = load_json_file(
        settings.processed_dir / "missing_triage_inputs_report.json"
    )
    # Scope clarified explicitly: this report (built by
    # scripts/inspect_missing_triage_inputs.py, which itself already
    # self-documents "dataset": "Kaggle-KTAS" in its own JSON output)
    # only ever covers the KTAS dataset, never MIMIC. With MIMIC cases
    # now visible elsewhere in the app (see the dataset filter on the
    # Triage Review tab), a generic "cases with
    # missing triage data" caption would read as if this queue covers
    # both datasets, when it only ever covers one. Read live from the
    # report's own "dataset" field rather than hardcoded again, so this
    # stays accurate automatically if inspect_missing_triage_inputs.py
    # is ever extended to cover MIMIC too.
    _queue_dataset_label = (missing_inputs_data or {}).get("dataset", "an unspecified dataset")
    st.caption(
        f"Cases with missing triage data that require clinician attention. "
        f"Scope: **{_queue_dataset_label} only** -- this report does not "
        f"cover MIMIC demo cases, even though they are now selectable "
        f"elsewhere in this app (see the dataset filter on the Triage "
        f"Review tab)."
    )

    review_log_path = settings.processed_dir / "human_reviews.jsonl"
    queue_reviews = read_human_reviews(review_log_path)
    queue_reviewed_ids = {int(r.stay_id) for r in queue_reviews}

    if missing_inputs_data is None:
        st.error(
            "Missing triage inputs report not found. Run: "
            "`python scripts/inspect_missing_triage_inputs.py`"
        )
    else:
        queue_cases = missing_inputs_data.get("missing_cases", [])

        # Review action is the FIRST main section of the queue (Dylan's order).
        pending = [c for c in queue_cases if int(c.get("stay_id", 0)) not in queue_reviewed_ids]
        if pending:
            st.markdown("---")
            st.subheader("Review a pending case")

            queue_options = {
                f"Stay {c['stay_id']} — {c.get('chiefcomplaint', '?')}": c for c in pending
            }
            selected_queue = st.selectbox("Select pending case", list(queue_options.keys()))
            selected_case_data = queue_options[selected_queue]
            selected_stay_id = int(selected_case_data["stay_id"])

            st.write(
                f"**Missing fields:** {', '.join(selected_case_data.get('missing_fields', []))}"
            )

            with st.form(f"queue_review_{selected_stay_id}"):
                qr1, qr2 = st.columns(2)
                q_role = qr1.selectbox(
                    "Role", ["triage_nurse", "emergency_physician", "researcher"]
                )
                q_status = qr2.selectbox(
                    "Decision",
                    [
                        "REQUEST_MORE_INFORMATION", "OVERRIDE_REQUIRED",
                        "REJECTED_DATA_QUALITY", "NOT_REVIEWED",
                    ],
                )
                q_comment = st.text_area(
                    "Notes",
                    value=(
                        f"Missing fields: {', '.join(selected_case_data.get('missing_fields', []))}. "
                        "Review required."
                    ),
                )
                if st.form_submit_button("Save"):
                    record = HumanReviewRecord(
                        review_id=str(uuid4()),
                        stay_id=selected_stay_id,
                        # _queue_dataset_label is derived from
                        # missing_triage_inputs_report.json's own
                        # "dataset" field (see this tab's caption above),
                        # not hardcoded here a second time -- every case
                        # in this queue genuinely comes from that one
                        # report, confirmed earlier this session to be
                        # KTAS-only by construction
                        # (scripts/inspect_missing_triage_inputs.py only
                        # reads triage_input_only_sample.jsonl).
                        source_dataset=_queue_dataset_label,
                        reviewer_role=q_role,
                        review_status=q_status,
                        review_comment=q_comment,
                        created_at_utc=datetime.now(timezone.utc).isoformat(),
                    )
                    append_human_review(review_log_path, record)
                    st.success(f"Review saved for stay {selected_stay_id}")
                    st.rerun()
        else:
            st.success("✅ All missing-data cases have been reviewed.")


        total = len(queue_cases)
        reviewed = sum(
            1 for c in queue_cases if int(c.get("stay_id", 0)) in queue_reviewed_ids
        )
        unreviewed = total - reviewed

        # Computed live from the actual missing-cases data, not hardcoded,
        # so this note can never go stale the way a fixed percentage would.
        # Per Choi et al.'s published KTAS dataset, o2sat (Saturation) is
        # missing far more often than any other vital -- this is a known
        # property of the source data, not something specific to this
        # pipeline. Surfacing it here avoids the queue looking like every
        # one of these cases individually needs painstaking manual
        # attention, when in most cases the underlying cause is the same
        # well-known dataset limitation.
        o2sat_only_missing = sum(
            1 for c in queue_cases
            if c.get("missing_fields") == ["o2sat"]
        )
        if total > 0 and o2sat_only_missing / total > 0.5:
            st.info(
                f"ℹ️ {o2sat_only_missing} of {total} queued cases "
                "({:.0%}) are missing only oxygen saturation (SpO2). High "
                "SpO2 missingness is a known limitation of the public KTAS "
                "dataset (Choi et al.), not specific to this pipeline. "
                "These cases are listed here for workflow demonstration "
                "and audit completeness, not because each one individually "
                "requires the same depth of manual review as a case "
                "missing multiple vitals or a chief complaint.".format(
                    o2sat_only_missing / total
                )
            )

        q1, q2, q3 = st.columns(3)
        q1.metric("Total missing-data cases", total)
        q2.metric("Reviewed", reviewed)
        q3.metric("⚠️ Needs review", unreviewed)

        queue_table = []
        for c in queue_cases:
            stay_id_int = int(c.get("stay_id", 0))
            review_status = "REVIEWED" if stay_id_int in queue_reviewed_ids else "PENDING"
            queue_table.append(
                {
                    "Stay ID": stay_id_int,
                    "Chief Complaint": c.get("chiefcomplaint", "?"),
                    "Missing Fields": ", ".join(c.get("missing_fields", [])),
                    "Review Status": review_status,
                }
            )

        st.dataframe(queue_table, width="stretch")



# ═══════════════════════════════════════════════════════════════════════════
# TAB 6 — AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════
with tab_audit:
    st.subheader("📜 Clinician Review Audit Log")
    st.caption(
        "Complete history of all clinician reviews, across every dataset. "
        "This log is append-only."
    )

    audit_log_path = settings.processed_dir / "human_reviews.jsonl"
    all_reviews = read_human_reviews(audit_log_path)

    dataset_audit = load_json_file(settings.processed_dir / "dataset_audit_report.json")
    missing_report = load_json_file(settings.processed_dir / "missing_triage_inputs_report.json")

    # Both reports below are confirmed KTAS-only by construction:
    # dataset_audit_report.json is written by scripts/run_ktas_pipeline.py
    # against the KTAS dataset specifically, and
    # missing_triage_inputs_report.json is written by
    # scripts/inspect_missing_triage_inputs.py, which only reads
    # triage_input_only_sample.jsonl (also KTAS-only). Neither report
    # currently has a MIMIC equivalent. Explicit scope captions added
    # here, consistent with the Governance and Review Queue tabs above.
    # ── Readable dataset audit summary cards (both datasets) ─────────────────
    # MIMIC and KTAS are summarised as readable cards first; raw JSON is in
    # collapsed expanders below. MIMIC is the default dataset, so it is shown
    # first and the page no longer presents the audit as "KTAS-only".
    st.markdown("#### Dataset audit summaries")
    ds_col1, ds_col2 = st.columns(2)
    with ds_col1:
        with st.container(border=True):
            st.markdown("**MIMIC-IV-ED Demo** (default)")
            mimic_reg = load_json_file(settings.model_registry_path) or {}
            mimic_model = mimic_reg.get("best_mimic_acuity_model", {})
            st.write("Cases: **222**")
            st.write(f"Labelled rows for acuity model: **{mimic_model.get('n_samples', 207)}**")
            st.write("Tables: edstays, triage, vitalsign, diagnosis, medrecon, pyxis")
            st.write("Target: **acuity** (ESI) → mapped to MTS-style display")
            st.caption(
                "Excluded leakage fields: acuity-as-input, disposition, outtime, "
                "diagnoses, medication tables, later vitals. Clinical use: not "
                "allowed — research/demo only."
            )
    with ds_col2:
        with st.container(border=True):
            st.markdown("**Kaggle KTAS**")
            ktas_total = (dataset_audit or {}).get("total_rows") or (dataset_audit or {}).get("n_rows") or 1267
            st.write(f"Cases: **{ktas_total:,}**" if isinstance(ktas_total, int) else f"Cases: **{ktas_total}**")
            st.write("Triage-time fields: available; retrospective labels separated")
            st.write("Target: **KTAS_expert** (KTAS class)")
            st.caption(
                "Leakage status: no target leakage detected (KTAS duration, "
                "disposition, error group excluded). Clinical use: not allowed — "
                "research/demo only."
            )
    st.caption(
        "Datasets are kept SEPARATE and are never combined. MIMIC uses the "
        "acuity model; KTAS uses the KTAS model. Raw audit JSON is in the "
        "expanders below."
    )


    if dataset_audit:
        with st.expander("Advanced: raw KTAS dataset audit JSON", expanded=False):
            st.json(dataset_audit)
    if missing_report:
        with st.expander("Missing triage input report (Kaggle-KTAS only)", expanded=False):
            st.caption(
                "This report covers the Kaggle-KTAS dataset only. There is "
                "currently no equivalent missing-input report for MIMIC-IV-ED Demo."
            )
            st.metric("Cases with missing inputs", missing_report.get("cases_with_missing_triage_inputs"))
            st.metric("Missing case percent", f"{missing_report.get('missing_case_percent')}%")

    # The two reports below are regenerated by scripts/run_synthetic_walkthrough.py
    # and scripts/run_triage_indicator_matrix.py respectively (see
    # KTAS_CHANGELOG.md for when each was last regenerated). This is a
    # deliberately small "make the existing output log visible" addition,
    # not the larger, separately-scoped interactive Scenario Walkthrough
    # tab a more thorough fix would eventually build (which would let a
    # user re-run a scenario live, edit its inputs, and see backend
    # endpoints for each -- that remains intentionally deferred).
    walkthrough_log = load_json_file(settings.processed_dir / "synthetic_walkthrough_log.json")
    matrix_log = load_json_file(settings.processed_dir / "triage_indicator_matrix_log.json")

    if walkthrough_log:
        scenarios = walkthrough_log.get("scenarios", [])
        with st.expander(f"Synthetic walkthrough log ({len(scenarios)} scenarios)"):
            st.caption(
                "Generated by scripts/run_synthetic_walkthrough.py. These are "
                "constructed demonstration cases, not real patient data."
            )
            for s in scenarios:
                outcome = (s.get("result") or s.get("comparison") or {})
                workflow_action = outcome.get("workflow_action", "N/A")
                status = outcome.get("classification_status") or outcome.get(
                    "new_classification_status", ""
                )
                label = f"{s.get('scenario', '?')}"
                if status:
                    label += f" — {status}"
                label += f" — workflow_action: {workflow_action}"
                st.markdown(f"- {label}")
            st.json(walkthrough_log)

    if matrix_log:
        results = matrix_log.get("results", [])
        mode = matrix_log.get("mode", "")
        # Read mode-specific pass fields. The log uses
        # all_match_gated_expectation / matches_gated_expectation (gated) or the
        # provisional equivalents -- NOT a generic "all_pass"/"pass". Missing
        # fields must NOT be treated as failure.
        if "PROVISIONAL" in str(mode).upper():
            all_pass = matrix_log.get("all_match_provisional_expectation")
            row_field = "matches_provisional_expectation"
            mode_label = "provisional ruleset active"
        else:
            all_pass = matrix_log.get("all_match_gated_expectation")
            row_field = "matches_gated_expectation"
            mode_label = "gated / no-ruleset mode"
        # A provisional-mode log is descriptive (no pass/fail); treat absent
        # all_pass there as "not a regression check" rather than failure.
        passed = [r for r in results if r.get(row_field) is True]
        failed = [r for r in results if r.get(row_field) is False]

        with st.expander(
            f"Triage Indicator Matrix — {mode_label} "
            f"({len(results)} indicators)",
            expanded=True,
        ):
            st.caption(
                "Generated by scripts/run_triage_indicator_matrix.py. One row "
                "per Manchester-engine pathway/vital-threshold indicator."
            )
            if all_pass is True or (not failed and passed):
                st.success(
                    f"Status: PASS — {len(passed)} of {len(results)} indicators "
                    f"matched expected {mode_label.split(' ')[0]} behaviour. Failed: {len(failed)}."
                )
            elif failed:
                st.error(f"Status: FAIL — {len(failed)} of {len(results)} indicators did not match.")
            else:
                st.info(
                    f"Descriptive log ({len(results)} indicators) — this mode records "
                    "actual engine output rather than a pass/fail comparison."
                )
            # Readable table: Indicator | Expected | Actual | Result
            table = [
                {
                    "Indicator": r.get("indicator", "?"),
                    "Expected": r.get("expected_status_gated") or r.get("expected_status_provisional") or "—",
                    "Actual": r.get("actual_status", "—"),
                    "Result": "✓ match" if r.get(row_field) is True
                              else ("✗ mismatch" if r.get(row_field) is False else "—"),
                }
                for r in results
            ]
            st.dataframe(table, width="stretch")
            with st.expander("Advanced: raw indicator matrix JSON", expanded=False):
                st.json(matrix_log)

    st.markdown("---")
    if not all_reviews:
        st.info("No reviews logged yet. Submit a review from the Triage Review tab.")
    else:
        st.markdown(f"**Total reviews logged: {len(all_reviews)}**")

        audit_table = [
            {
                "Stay ID": r.stay_id,
                # source_dataset is Optional and may be None for any
                # review record saved before this field existed (see
                # app/schemas/review.py) -- rendered explicitly as
                # "Unknown (pre-dataset-tracking)" rather than a blank
                # cell, so it reads as a deliberate fact about that
                # record's age, not a rendering bug.
                "Source Dataset": r.source_dataset or "Unknown (pre-dataset-tracking)",
                "Reviewer Role": r.reviewer_role,
                "Decision": r.review_status,
                "Override": r.clinician_override or "",
                "Timestamp": r.created_at_utc[:19],
            }
            for r in reversed(all_reviews)
        ]
        st.dataframe(audit_table, width="stretch")

        st.markdown("---")
        for review in reversed(all_reviews):
            with st.expander(
                f"Stay {review.stay_id} "
                f"({review.source_dataset or 'Unknown (pre-dataset-tracking)'}) — "
                f"{review.review_status} — "
                f"{review.reviewer_role} — {review.created_at_utc[:19]}"
            ):
                st.json(review.model_dump(mode="json"))


# ═══════════════════════════════════════════════════════════════════════════
# TAB 7 — MODEL PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════
with tab_models:
    st.subheader("📊 ML Model Performance")
    st.caption(
        "Two separate model families: KTAS models (public Kaggle KTAS, 1267 rows) "
        "and a MIMIC-IV-ED Demo acuity model (207 rows). Neither is validated "
        "against UHL clinical ground truth. For research only."
    )

    registry = load_model_registry()
    eval_report = load_json_file(settings.processed_dir / "model_evaluation_report.json")

    if not registry:
        st.warning(
            "No trained models found.\n\n"
            "Run the full pipeline:\n```\npython scripts/run_ktas_pipeline.py\n```"
        )
    else:
        st.success(f"✅ Models trained — version: {registry.get('version')}")

        best_ktas = registry.get("best_ktas_model", {})
        best_em = registry.get("best_emergency_model", {})
        ktas_metrics = best_ktas.get("metrics", {})
        em_metrics = best_em.get("metrics", {})

        st.markdown("### Best Models Summary")
        m1, m2 = st.columns(2)
        with m1:
            st.markdown(f"**5-class KTAS model: {best_ktas.get('name', '?')}**")
            st.metric("Macro F1", f"{ktas_metrics.get('macro_f1', 0):.3f}")
            st.metric(
                "Under-triage rate ⚠️",
                f"{ktas_metrics.get('under_triage_rate', 0):.3f}",
                help="Fraction of cases where the model predicted LESS urgent than the expert label — the clinically dangerous direction.",
            )
            st.metric("Over-triage rate", f"{ktas_metrics.get('over_triage_rate', 0):.3f}")
        with m2:
            st.markdown(f"**Emergency binary model: {best_em.get('name', '?')}**")
            st.metric("AUROC", f"{em_metrics.get('macro_auroc', 0):.3f}")
            st.metric(
                "False-negative emergency rate ⚠️",
                f"{em_metrics.get('false_negative_emergency_rate', 0):.3f}",
                help="Fraction of true emergencies (KTAS 1-3) the model predicted as non-emergency.",
            )
            st.metric("Weighted F1", f"{em_metrics.get('weighted_f1', 0):.3f}")

        under_rate = ktas_metrics.get("under_triage_rate", 1.0)
        fn_rate = em_metrics.get("false_negative_emergency_rate", 1.0)
        if under_rate > 0.15 or fn_rate > 0.15:
            st.error(
                f"⚠️ Under-triage rate {under_rate:.1%} / false-negative-emergency rate "
                f"{fn_rate:.1%} — both well above what would be acceptable for any "
                "clinical use. This is a research metric on a 1267-row public dataset "
                "using only triage-time vitals and demographics. More data and "
                "clinical validation are required before these numbers mean anything "
                "beyond a research baseline."
            )
        else:
            st.warning(
                f"Under-triage rate {under_rate:.1%}, false-negative-emergency rate "
                f"{fn_rate:.1%}. Clinical validation still required before any use."
            )

        st.markdown("---")
        st.markdown("### All 5-Class KTAS Models Compared")
        all_ktas = registry.get("all_ktas_models", [])
        if all_ktas:
            comparison = [
                {
                    "Model": m["name"],
                    "Macro F1": round(m["metrics"].get("macro_f1", 0), 3),
                    "Under-triage rate": round(m["metrics"].get("under_triage_rate", 0), 3),
                    "Over-triage rate": round(m["metrics"].get("over_triage_rate", 0), 3),
                    "Selection score": round(m["metrics"].get("selection_score", 0), 3),
                }
                for m in all_ktas
            ]
            st.dataframe(comparison, width="stretch")

        st.markdown("---")
        st.markdown("### All Emergency Binary Models Compared")
        all_em = registry.get("all_emergency_models", [])
        if all_em:
            comparison_em = [
                {
                    "Model": m["name"],
                    "AUROC": round(m["metrics"].get("macro_auroc", 0), 3),
                    "False-negative rate": round(m["metrics"].get("false_negative_emergency_rate", 0), 3),
                    "Selection score": round(m["metrics"].get("selection_score", 0), 3),
                }
                for m in all_em
            ]
            st.dataframe(comparison_em, width="stretch")

        # ── MIMIC-IV-ED Demo acuity model section ──────────────────────────
        st.markdown("---")
        st.markdown("### MIMIC-IV-ED Demo — Acuity Model")
        mimic_info = registry.get("best_mimic_acuity_model")
        if not mimic_info:
            st.warning(
                "No MIMIC acuity model registered. Build it with:\n```\n"
                "python scripts/build_mimic_demo_labels.py\n"
                "python ml_training/train_mimic_acuity_model.py\n```"
            )
        else:
            cv = mimic_info.get("cv_accuracy")
            dist = mimic_info.get("class_distribution", {})
            mm1, mm2 = st.columns(2)
            with mm1:
                st.markdown(f"**Model:** {mimic_info.get('name', '?')}")
                st.metric(
                    "CV accuracy",
                    f"{cv:.3f}" if isinstance(cv, (int, float)) else "n/a",
                    help="Stratified cross-validation accuracy on the demo subset.",
                )
                st.write(f"Dataset: **{mimic_info.get('dataset', '?')}**")
                st.write(f"Target: **{mimic_info.get('target', 'acuity')}** (ESI)")
                st.write(f"Labelled training rows: **{mimic_info.get('n_samples', '?')}**")
            with mm2:
                st.markdown("**Class distribution (acuity → count)**")
                st.write({str(k): dist.get(str(k), 0) for k in [1, 2, 3, 4, 5]})
                st.caption(
                    "Predicted acuity maps to the five-level colour/priority "
                    "display (acuity_to_mts_display_v1). A deterministic "
                    "escalate-only vital override may raise extreme/critical cases."
                )
            st.error(
                "⚠️ **Demo-size, class-imbalanced — NOT clinical evidence.** "
                f"{mimic_info.get('small_data_warning', '')} "
                "No UHL / full-MIMIC / prospective / clinician validation. "
                "Research and pipeline demonstration only."
            )
            with st.expander("MIMIC model leakage features (never used as inputs)"):
                st.json(mimic_info.get("blocked_leakage_features", []))

        st.markdown("---")
        st.markdown("### KTAS Training Data Summary")
        st.markdown(
            f"""
| Property | Value |
|---|---|
| Dataset | {registry.get('dataset', '?')} |
| Training samples | {registry.get('n_samples', '?')} |
| Features | {len(registry.get('feature_names', []))} |
| Version | {registry.get('version', '?')} |
| Trained at | {str(registry.get('created_at_utc', '?'))[:19]} |
            """
        )

        with st.expander("Blocked leakage features (never used as model inputs)"):
            st.json(registry.get("blocked_leakage_features", []))

        st.warning("⚠️ **Research Note:** " + registry.get("research_note", ""))

        with st.expander("Full registry JSON"):
            st.json(registry)

    if eval_report:
        with st.expander("Full model evaluation report JSON"):
            st.json(eval_report)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### System Status")

    has_models = settings.model_registry_path.exists()
    st.markdown(f"**ML Models:** {'✅ Trained' if has_models else '⬜ Not trained'}")

    azure_ok = load_azure_config() is not None
    st.markdown(f"**Azure OpenAI / AutoGen chat:** {'✅ Configured' if azure_ok else '⬜ Not configured'}")

    mimic_demo_path = settings.raw_demo_dir / "edstays.csv.gz"
    mimic_full_path = settings.raw_ed_dir / "edstays.csv.gz"
    st.markdown(f"**MIMIC-IV-ED Demo:** {'✅ Loaded' if mimic_demo_path.exists() else '⬜ Not loaded'}")
    st.markdown(f"**MIMIC-IV-ED Full:** {'✅ Loaded' if mimic_full_path.exists() else '⬜ Awaiting approval'}")

    st.markdown("---")
    st.markdown("### Quick Start")
    st.code(
        """# 1. Run the full KTAS pipeline
python scripts/run_ktas_pipeline.py

# 2. Run tests
pytest

# 3. Run the API (separate terminal)
uvicorn app.main:app --reload

# 4. Run this UI (separate terminal)
streamlit run frontend/app.py
""",
        language="bash",
    )

    st.markdown("---")
    st.caption(
        "NOT FOR CLINICAL USE\n\n"
        "Research prototype — KTAS is not Manchester Triage Scale. "
        "No KTAS-to-Manchester mapping exists. AutoGen orchestrates the "
        "explanation/chat layer only; it has no authority over any clinical decision."
    )
