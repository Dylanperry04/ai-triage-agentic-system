"""
AI Triage Agentic Workflow - Streamlit UI (full MIMIC-IV-ED only; backend-driven).

The only prediction/training dataset is full MIMIC-IV-ED (credentialed), read from
MIMIC_FULL_ED_DIR on an approved environment; the trained model is read from
MIMIC_FULL_MODEL_PATH. Without MIMIC_FULL_ED_DIR, the app serves no cases; without
MIMIC_FULL_MODEL_PATH, cases can be listed but predictions are withheld. Retired
non-full datasets are not prediction sources and do not appear in the model, UI,
status, prediction path, or model-performance page. Synthetic MIMIC-shaped
fixtures are used only for automated tests and the default Azure supervisor
demo; a governed full-MIMIC Azure demo must explicitly disable synthetic
fallback. Synthetic fixtures are never a clinical or patient-data source.

The frontend is presentation-only for sensitive operations: every protected
action and identity/permission check goes through frontend/api_client.py to the
FastAPI backend (the sole enforcement boundary). Public-demo synthetic logs may
be read locally for display only.

Tabs:
  1. Triage Review     - select a case (from the backend), run the assessment
                          server-side, view the result + safety flags, submit a
                          clinician review.
  2. Maintainability    - additional information, reassessment, and future
                          extension evidence.
  3. Governance         - responsible-AI review gate + system health checks.
  4. Review Queue       - backend-driven, full-MIMIC-only review queue.
  5. Audit Log          - clinician review / workflow audit history.
  6. Model Performance  - full-MIMIC model status and (if produced on the
                          approved environment) the safety-first comparison.
"""
from __future__ import annotations

import json
import os
import sys
import html
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.rules.acuity_mts_mapping import MTS_DISPLAY_HEX, map_acuity_to_mts


CASE_SELECTOR_LIMIT = 50
CATEGORY_REASONING_QUESTION = (
    "Explain why the system showed this exact acuity/category estimate. "
    "Focus only on decision-driving evidence: chief complaint/pathway, "
    "abnormal or missing vitals, deterministic safety/rule signals, ML "
    "probability/confidence, and any override. Do not re-list normal vitals "
    "unless they explain why vitals did not drive the estimate."
)


st.set_page_config(
    page_title="AI Triage Agentic Workflow",
    page_icon="AI",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Register the provisional MTS research ruleset at startup (default-on; set
# PROVISIONAL_MTS_MODE=off to disable). Makes the engine assign provisional,
# clinician-review-required Manchester categories. NOT the official MTS and NOT
# clinically approved -- see app/rules/provisional_mts_ruleset.py.


# -- Helper functions ----------------------------------------------------------

def load_cases() -> list[dict]:
    """Return the live case list from the FastAPI backend (GET /cases).

    The only live dataset is full MIMIC-IV-ED (credentialed), served by the
    backend; the local KTAS/demo loaders have been removed. Returns [] if no
    cases are available (e.g. this sandbox with no credentialed data), and never
    halts the app. The returned dicts are the backend's redacted summaries
    (case_uid + triage view; no raw identifiers)."""
    from frontend import api_client
    try:
        resp = api_client.list_cases(limit=200, offset=0)
        return resp.get("cases", []) if isinstance(resp, dict) else []
    except Exception:
        return []


def load_json_file(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_model_registry() -> dict | None:
    return load_json_file(settings.model_registry_path)


def presentation_ui_mode() -> bool:
    return os.environ.get("PRESENTATION_UI_MODE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def render_backend_case_selector(
    widget_key_prefix: str,
    label: str = "ED Stay",
    show_label: bool = False,
):
    """Backend-driven case selector. There is exactly one live dataset
    (full MIMIC-IV-ED, credentialed), so there is no dataset filter and no local
    case loading: the cases come from the FastAPI backend (GET /cases), which
    returns pseudonymous, redacted case summaries (case_uid only; no raw stay_id).

    Returns a dict with at least {'case_uid', 'source_dataset'} (and a 'triage'
    sub-dict for users with clinical-content access), or None if no cases are
    available (e.g. this sandbox, which has no credentialed MIMIC data). The
    caller must perform any assessment via the backend using the returned
    case_uid (no local workflow execution)."""
    from frontend import api_client
    search = st.text_input(
        "Search cases",
        value="",
        key=f"{widget_key_prefix}_case_search",
        placeholder="Search by case ID, subject/stay ID, chief complaint, acuity, status, transport, or source",
        label_visibility="collapsed",
    )
    with st.expander("Case search filters", expanded=False):
        filter_cols = st.columns(3)
        subject_filter = filter_cols[0].text_input(
            "Subject ID",
            key=f"{widget_key_prefix}_case_subject_filter",
        ).strip()
        stay_filter = filter_cols[1].text_input(
            "Stay ID",
            key=f"{widget_key_prefix}_case_stay_filter",
        ).strip()
        acuity_filter = filter_cols[2].selectbox(
            "Acuity",
            ["", "1", "2", "3", "4", "5"],
            key=f"{widget_key_prefix}_case_acuity_filter",
        )
        status_cols = st.columns(3)
        workflow_filter = status_cols[0].selectbox(
            "Workflow status",
            ["", "information_requested", "escalation_requested", "escalation_confirmed", "reassessment_complete"],
            key=f"{widget_key_prefix}_case_workflow_filter",
        )
        case_status_filter = status_cols[1].selectbox(
            "Case status",
            ["", "accepted", "request_more_info", "escalation_requested", "escalated", "discharged"],
            key=f"{widget_key_prefix}_case_status_filter",
        )
        active_state_filter = status_cols[2].selectbox(
            "Active state",
            ["", "active", "escalated", "discharged"],
            key=f"{widget_key_prefix}_case_active_state_filter",
        )
    page_size = st.selectbox(
        "Page size",
        [20, 50, 100, 200],
        index=1,
        key=f"{widget_key_prefix}_case_page_size",
        label_visibility="collapsed",
    )
    offset_key = f"{widget_key_prefix}_case_offset"
    query_key = f"{widget_key_prefix}_case_query_seen"
    current_query = search.strip()
    if st.session_state.get(query_key) != current_query:
        st.session_state[offset_key] = 0
        st.session_state[query_key] = current_query
    offset = int(st.session_state.get(offset_key, 0) or 0)
    try:
        resp = api_client.list_cases(
            limit=page_size,
            offset=offset,
            search=current_query or None,
            subject_id=subject_filter or None,
            stay_id=stay_filter or None,
            acuity_level=acuity_filter or None,
            workflow_status=workflow_filter or None,
            case_status=case_status_filter or None,
            active_state=active_state_filter or None,
        )
        cases = resp.get("cases", []) if isinstance(resp, dict) else []
        pagination = resp.get("pagination", {}) if isinstance(resp, dict) else {}
    except api_client.BackendError as exc:
        st.error(f"Locked: Could not load cases from the backend (HTTP {exc.status_code}).")
        return None

    if not cases:
        st.info(
            "No cases are available. The only live dataset is full MIMIC-IV-ED "
            "(credentialed), which is served only on an approved environment with "
            "MIMIC_FULL_ED_DIR configured. This environment has no patient data."
        )
        return None

    if pagination.get("search_truncated"):
        st.caption(
            "Search scanned a bounded case window. Refine the query if the "
            "case is not listed."
        )
    else:
        page_start = int(pagination.get("offset") or offset) + 1
        page_end = int(pagination.get("offset") or offset) + len(cases)
        total = pagination.get("total")
        total_label = str(total) if pagination.get("total_is_exact", True) else f"{total}+"
        nav_prev, nav_meta, nav_next = st.columns([1, 3, 1])
        if nav_prev.button(
            "Previous",
            key=f"{widget_key_prefix}_case_prev",
            disabled=offset <= 0,
            use_container_width=True,
        ):
            st.session_state[offset_key] = max(0, offset - int(page_size))
            st.rerun()
        nav_meta.caption(f"Showing {page_start}-{page_end} of {total_label} cases")
        if nav_next.button(
            "Next",
            key=f"{widget_key_prefix}_case_next",
            disabled=not bool(pagination.get("has_more")),
            use_container_width=True,
        ):
            st.session_state[offset_key] = int(pagination.get("next_offset") or offset + int(page_size))
            st.rerun()
    if debug_ui_enabled():
        with st.expander("Developer/debug: case-query metadata", expanded=False):
            st.json(pagination, expanded=False)

    def _label(c):
        cc = (c.get("triage") or {}).get("chiefcomplaint") or "case"
        return f"{c['case_uid']} - {cc}"

    options = {_label(c): c for c in cases}
    selected = st.selectbox(
        label, list(options.keys()),
        label_visibility="visible" if show_label else "collapsed",
        key=f"{widget_key_prefix}_case_select",
    )
    return options[selected]


def debug_ui_enabled() -> bool:
    return os.environ.get("DEBUG_UI", "").lower() == "true"


def azure_openai_configured() -> bool:
    """Single source of truth for whether the LLM explanation layer can
    run. Uses the backend's non-secret status endpoint; the frontend process is
    not authoritative in a two-service deployment."""
    try:
        from frontend import api_client
        return bool(api_client.llm_status().get("azure_config_present"))
    except Exception:
        return False


def fmt_pct(value) -> str:
    return "N/A" if value is None else f"{value:.1%}"


def _status_badge(status: str) -> str:
    if status in ("PASS", "TRIAGE_INPUT_DATA_COMPLETE"):
        return f"OK {status}"
    if status in ("NOT_CONFIGURED", "NOT_REQUESTED", "NOT_RUN"):
        return f" {status}"
    if "FAIL" in status or "MISSING" in status or "NEEDS" in status or "ERROR" in status:
        return f"Warning: {status}"
    return f"Info: {status}"


REASON_CODE_SEVERITY_PREFIXES = ("CRITICAL", "FORBIDDEN")


def _flag_icon(flag: str) -> str:
    upper = flag.upper()
    if any(p in upper for p in REASON_CODE_SEVERITY_PREFIXES):
        return "Red"
    if "MISSING" in upper or "CONCERN" in upper:
        return "Amber"
    return "Info:"

def _display_value(value, unit: str | None = None) -> str:
    if value is None or value == "":
        return "Not recorded"
    return f"{value} {unit}".strip() if unit else str(value)


def _mts_display_for_acuity(acuity) -> dict | None:
    mapped = map_acuity_to_mts(acuity)
    if not mapped:
        return None
    colour = str(mapped.get("colour") or "").lower()
    colours = MTS_DISPLAY_HEX.get(colour, {"bg": "#6b7280", "fg": "#ffffff"})
    return {**mapped, **colours}


def _mts_label(acuity, mapped: dict | None = None) -> str:
    mapped = mapped or _mts_display_for_acuity(acuity)
    if not mapped:
        return f"Acuity {_display_value(acuity)} - no mapped display category"
    return f"Acuity {acuity} - {mapped.get('category')}"


def _render_mts_colour_box(label: str, acuity, mapped: dict | None = None) -> None:
    mapped = mapped or _mts_display_for_acuity(acuity)
    if not mapped:
        st.info(f"{label}: no Manchester-style equivalent is available.")
        return
    bg = html.escape(str(mapped.get("bg") or "#6b7280"))
    fg = html.escape(str(mapped.get("fg") or "#ffffff"))
    category = html.escape(str(mapped.get("category") or "Unknown"))
    colour = html.escape(str(mapped.get("colour") or "unknown").title())
    wait = html.escape(str(mapped.get("max_wait_minutes") or "0"))
    safe_label = html.escape(label)
    safe_acuity = html.escape(str(acuity))
    st.markdown(
        f"""
        <div style="background:{bg}; color:{fg}; padding:0.75rem 0.9rem; border-radius:6px; margin:0.25rem 0 0.5rem 0;">
          <div style="font-size:0.78rem; font-weight:600; opacity:0.92;">{safe_label}</div>
          <div style="font-size:1.05rem; font-weight:700;">Acuity {safe_acuity} - {category}</div>
          <div style="font-size:0.86rem;">Manchester-style display colour: {colour}; max wait convention: {wait} min</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_workflow_state_banner(record: dict) -> None:
    state = record.get("workflow_state") or {}
    if not isinstance(state, dict) or not state:
        return
    if _case_is_discharged(record):
        st.success("Case discharged / closed. Removed from active clinical queue.")
        return
    if state.get("escalation_required"):
        st.error(
            "Escalation pending: "
            f"{state.get('escalation_reason') or 'supervisor/doctor review required.'}"
        )
        st.caption(
            "Target role: "
            f"{state.get('escalation_target_role') or 'clinical_supervisor'}; "
            f"status: {state.get('escalation_status') or 'pending'}"
        )
        return
    status = str(state.get("review_status") or "").lower()
    if status == "information_requested":
        fields = state.get("requested_fields") or []
        st.warning(
            "Information requested"
            + (f": {', '.join(fields)}" if fields else ".")
        )
    elif status == "accepted_as_presented":
        st.success("This individual case review was accepted as presented.")
    elif status:
        st.info(f"Current review state: {status.replace('_', ' ')}")


def _parse_dt(value):
    if not value:
        return None
    try:
        from datetime import datetime, timezone

        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _workflow_state(record: dict) -> dict:
    state = record.get("workflow_state") or {}
    return state if isinstance(state, dict) else {}


def _case_status(record: dict) -> str:
    state = _workflow_state(record)
    status = (
        state.get("case_status")
        or state.get("review_status")
        or "new"
    )
    return str(status or "new").lower()


def _case_is_discharged(record: dict) -> bool:
    state = _workflow_state(record)
    status = _case_status(record)
    return status in {"discharged", "closed", "case_closed"} or bool(state.get("discharged_at"))


def _case_has_active_escalation(record: dict) -> bool:
    if _case_is_discharged(record):
        return False
    state = _workflow_state(record)
    return str(state.get("escalation_state") or state.get("escalation_status") or "").lower() in {
        "requested",
        "pending",
        "confirmed",
    } or bool(state.get("escalation_required"))


def _queue_metadata(record: dict) -> dict:
    meta = record.get("queue_metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _vitals_clock_dt(record: dict):
    state = _workflow_state(record)
    meta = _queue_metadata(record)
    for key in (
        "last_vitals_updated_at",
        "last_vitals_checked_at",
        "last_vitals_updated_utc",
        "last_vitals_checked_utc",
        "submitted_at_utc",
        "arrival_time_utc",
        "arrival_time",
        "submitted_at",
        "intime",
    ):
        dt = _parse_dt(state.get(key) or meta.get(key))
        if dt is not None:
            return dt
    return None


def _vitals_overdue_info(record: dict) -> dict:
    from datetime import datetime, timezone

    if _case_is_discharged(record):
        return {"overdue": False, "minutes": None, "clock": None}
    state = _workflow_state(record)
    clock = _vitals_clock_dt(record)
    if clock is None:
        return {"overdue": False, "minutes": None, "clock": None}
    now = datetime.now(timezone.utc)
    delta_minutes = (now - clock).total_seconds() / 60.0
    if delta_minutes < 0:
        return {"overdue": False, "minutes": delta_minutes, "clock": clock}
    ack = _parse_dt(state.get("overdue_vitals_acknowledged_at"))
    if ack is not None and ack >= clock:
        return {"overdue": False, "minutes": delta_minutes, "clock": clock, "acknowledged": True}
    return {
        "overdue": delta_minutes >= 210.0,
        "minutes": delta_minutes,
        "clock": clock,
        "acknowledged": False,
    }


def _notification_summary(records: list[dict]) -> dict:
    overdue = []
    escalations = []
    info_requests = []
    for record in records or []:
        if _case_is_discharged(record):
            continue
        if _vitals_overdue_info(record).get("overdue"):
            overdue.append(record)
        if _case_has_active_escalation(record):
            escalations.append(record)
        status = _case_status(record)
        if "information_requested" in status or "request_more_info" in status:
            info_requests.append(record)
    return {
        "overdue_vitals": overdue,
        "active_escalations": escalations,
        "information_requests": info_requests,
        "count": len(overdue) + len(escalations) + len(info_requests),
    }


def _render_notification_bell(records: list[dict]) -> None:
    summary = _notification_summary(records)
    count = summary.get("count", 0)
    with st.expander(f"Notifications ({count})", expanded=False):
        if count == 0:
            st.caption("No overdue-vitals, active-escalation, or information-request notifications in the visible case window.")
            return
        if summary["overdue_vitals"]:
            st.markdown("**Overdue vitals**")
            for record in summary["overdue_vitals"][:10]:
                info = _vitals_overdue_info(record)
                mins = info.get("minutes")
                st.write(f"{record.get('case_uid')}: vitals not updated for {mins:.0f} min")
        if summary["active_escalations"]:
            st.markdown("**Active escalations**")
            for record in summary["active_escalations"][:10]:
                state = _workflow_state(record)
                st.write(f"{record.get('case_uid')}: {state.get('escalation_reason') or 'clinical escalation'}")
        if summary["information_requests"]:
            st.markdown("**Information requested**")
            for record in summary["information_requests"][:10]:
                state = _workflow_state(record)
                fields = ", ".join(state.get("requested_fields") or [])
                st.write(f"{record.get('case_uid')}: {fields or 'additional information requested'}")


def render_triage_input_summary(record: dict, *, key_prefix: str) -> None:
    """Render the redacted backend case DTO as user-facing triage-time fields."""
    triage = record.get("triage") or {}
    demographics = record.get("demographics") or {}
    if not triage and not demographics:
        st.caption("You do not have clinical-content access for this case.")
        return

    st.markdown("**Triage-time inputs**")
    cc = triage.get("chiefcomplaint")
    if cc:
        st.write(f"Chief complaint: {cc}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Heart rate", _display_value(triage.get("heartrate")))
    c2.metric("Resp rate", _display_value(triage.get("resprate")))
    c3.metric("SpO2", _display_value(triage.get("o2sat"), "%"))
    c4.metric("Pain", _display_value(triage.get("pain")))
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("SBP", _display_value(triage.get("sbp")))
    v2.metric("DBP", _display_value(triage.get("dbp")))
    v3.metric(
        "Temperature",
        _display_value(triage.get("temperature"), triage.get("temperature_unit")),
    )
    v4.metric("Arrival", _display_value(demographics.get("arrival_transport")))
    if demographics.get("gender"):
        st.caption(f"Recorded demographic: gender={demographics.get('gender')}")
    render_workflow_state_banner(record)
    if debug_ui_enabled():
        with st.expander("Developer/debug: redacted backend case DTO", expanded=False):
            st.json(record, expanded=False)


def render_assessment_summary(dto: dict, source_dataset: str) -> None:
    ml_acuity = dto.get("ml_predicted_acuity")
    st.markdown("**ML model estimate**")
    if ml_acuity is None:
        st.metric("ML model estimate", "Not recorded")
        st.info(
            "No Manchester-style colour equivalent is available because the "
            "backend returned no ML acuity level for this case."
        )
    else:
        st.metric("ML model estimate", f"Acuity {ml_acuity}")
        _render_mts_colour_box("Manchester-style triage equivalent", ml_acuity)
    confidence = dto.get("top_class_confidence") or dto.get("confidence")
    high_prob = dto.get("high_acuity_research_estimate")
    probability_rows = []
    class_probs = (
        dto.get("mimic_acuity_probabilities")
        or dto.get("class_probabilities")
        or {}
    )
    for label in ("1", "2", "3", "4", "5"):
        if label in class_probs:
            probability_rows.append({
                "acuity": label,
                "probability": class_probs[label],
            })
    detail_cols = st.columns(3)
    detail_cols[0].metric(
        "Top-class confidence",
        "-" if confidence is None else f"{float(confidence):.3f}",
    )
    detail_cols[1].metric(
        "High-acuity probability",
        "-" if high_prob is None else f"{float(high_prob):.3f}",
    )
    detail_cols[2].metric(
        "Review required",
        "Yes" if dto.get("clinician_review_required", True) else "No",
    )
    if probability_rows:
        with st.expander("Class probabilities", expanded=False):
            st.dataframe(probability_rows, width="stretch", hide_index=True)
    missing = dto.get("missing_fields") or []
    critical_missing = dto.get("critical_missing_vitals") or []
    flags = dto.get("safety_flags") or []
    reason_codes = dto.get("reason_codes") or []
    caution_rows = []
    for label, values in (
        ("Missing required data", missing),
        ("Critical missing vitals", critical_missing),
        ("Safety/caution flags", flags if isinstance(flags, list) else [flags]),
        ("Rules reason codes", reason_codes),
    ):
        if values:
            caution_rows.append({"signal": label, "value": ", ".join(map(str, values))})
    if dto.get("high_risk_complaint_detected"):
        caution_rows.append({"signal": "High-risk complaint", "value": "detected"})
    if caution_rows:
        st.markdown("**Decision and reliability signals**")
        st.dataframe(caution_rows, width="stretch", hide_index=True)


def _is_high_acuity_label(label) -> bool:
    try:
        return float(str(label)) in {1.0, 2.0}
    except (TypeError, ValueError):
        return str(label).strip() in {"1", "2"}


def _high_acuity_precision_from_confusion(candidate: dict) -> float | None:
    labels = [str(x) for x in candidate.get("confusion_matrix_labels") or []]
    matrix = candidate.get("confusion_matrix") or []
    if not labels or not matrix:
        return None
    high = {i for i, label in enumerate(labels) if _is_high_acuity_label(label)}
    if not high:
        return None
    predicted_high = 0
    true_and_predicted_high = 0
    for true_idx, row in enumerate(matrix):
        for pred_idx, count in enumerate(row or []):
            if pred_idx in high:
                predicted_high += int(count or 0)
                if true_idx in high:
                    true_and_predicted_high += int(count or 0)
    if predicted_high == 0:
        return None
    return true_and_predicted_high / predicted_high


def _as_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("precision", "recall", "value", "score"):
            parsed = _as_float(value.get(key))
            if parsed is not None:
                return parsed
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candidate_high_acuity_recall(candidate: dict) -> float | None:
    har = candidate.get("high_acuity_recall")
    recall = har.get("recall") if isinstance(har, dict) else har
    return _as_float(recall)


def _candidate_precision_for_plot(candidate: dict) -> tuple[float | None, str]:
    for key in (
        "high_acuity_precision",
        "precision_high_acuity",
        "high_acuity_positive_predictive_value",
    ):
        precision = _as_float(candidate.get(key))
        if precision is not None:
            return precision, "reported high-acuity precision"

    precision = _high_acuity_precision_from_confusion(candidate)
    if precision is not None:
        return precision, "computed from confusion matrix"

    per_class = candidate.get("per_class") or {}
    precision_values = []
    for label in ("1", "2"):
        metrics = (
            per_class.get(label)
            or per_class.get(f"{label}.0")
            or per_class.get(int(label))
            or {}
        )
        precision = _as_float(metrics.get("precision") if isinstance(metrics, dict) else None)
        if precision is not None:
            precision_values.append(precision)
    if precision_values:
        return sum(precision_values) / len(precision_values), "mean acuity 1-2 class precision"

    return None, ""


def _candidate_over_triage_view(candidate: dict) -> tuple[float | None, float | None]:
    over_triage = candidate.get("over_triage_specificity") or {}
    specificity = _as_float(over_triage.get("specificity"))
    urgent_rate = _as_float(over_triage.get("predicted_urgent_rate"))
    if specificity is None:
        specificity = _as_float(candidate.get("specificity"))
    if urgent_rate is None:
        urgent_rate = _as_float(candidate.get("predicted_urgent_rate"))
    if urgent_rate is None:
        urgent_rate = _as_float(candidate.get("urgent_rate"))
    return specificity, urgent_rate


def _per_class_metric_rows(candidates: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for cand in candidates or []:
        per_class = cand.get("per_class") or {}
        model_name = cand.get("model_name") or cand.get("name") or "model"
        for label in ("1", "2", "3", "4", "5"):
            metrics = (
                per_class.get(label)
                or per_class.get(f"{label}.0")
                or per_class.get(int(label))
                or {}
            )
            if not isinstance(metrics, dict) or not metrics:
                continue
            rows.append({
                "model": model_name,
                "acuity": f"Acuity {label}",
                "acuity_key": f"acuity_{label}",
                "recall": _as_float(metrics.get("recall")),
                "precision": _as_float(metrics.get("precision")),
                "f1": _as_float(metrics.get("f1")),
                "support": metrics.get("support"),
            })
    return rows


def _render_recall_by_triage_category(candidates: list[dict]) -> None:
    st.markdown("### Recall by triage category")
    st.caption(
        "Per-class recall uses each candidate's `per_class` metrics for acuity 1, "
        "acuity 2, acuity 3, acuity 4, and acuity 5 individually."
    )
    rows = _per_class_metric_rows(candidates)
    if not rows:
        st.info("Recall by triage category is unavailable because candidate per-class metrics are missing.")
        return
    st.dataframe(rows, width="stretch", hide_index=True)
    chart_rows = [row for row in rows if row.get("recall") is not None]
    if chart_rows:
        st.vega_lite_chart(
            {"values": chart_rows},
            {
                "mark": {"type": "bar", "tooltip": True},
                "encoding": {
                    "x": {
                        "field": "model",
                        "type": "nominal",
                        "title": "Model",
                        "axis": {"labelAngle": -25, "labelLimit": 180},
                    },
                    "xOffset": {"field": "acuity", "type": "nominal"},
                    "y": {
                        "field": "recall",
                        "type": "quantitative",
                        "title": "Recall",
                        "scale": {"domain": [0.0, 1.0]},
                        "axis": {"format": ".0%"},
                    },
                    "color": {
                        "field": "acuity",
                        "type": "nominal",
                        "title": "Triage category",
                    },
                    "tooltip": [
                        {"field": "model", "type": "nominal"},
                        {"field": "acuity", "type": "nominal"},
                        {"field": "recall", "type": "quantitative", "format": ".3f"},
                        {"field": "precision", "type": "quantitative", "format": ".3f"},
                        {"field": "f1", "type": "quantitative", "format": ".3f"},
                        {"field": "support", "type": "quantitative"},
                    ],
                },
                "height": 360,
            },
            use_container_width=True,
        )


def _model_plot_label(model_name: str | None) -> str:
    name = str(model_name or "unknown")
    labels = {
        "raw_tfidf_word_char_logistic": "TF-IDF + Logistic",
        "raw_tfidf_word_char_sgd_logistic": "SGD TF-IDF + Logistic",
        "raw_tfidf_word_char_linear_svm": "TF-IDF + Linear SVM",
        "raw_tfidf_svd_xgboost": "TF-IDF SVD + XGBoost",
        "raw_tfidf_svd_lightgbm": "TF-IDF SVD + LightGBM",
        "raw_tfidf_svd_catboost": "TF-IDF SVD + CatBoost",
        "hist_gradient_boosting": "HistGradientBoosting",
        "xgboost_gpu": "XGBoost GPU",
        "lightgbm_gpu": "LightGBM GPU",
        "catboost_gpu": "CatBoost GPU",
        "logistic_regression": "Logistic regression",
        "random_forest": "Random forest",
        "extra_trees": "Extra trees",
    }
    if name in labels:
        return labels[name]
    return name.replace("raw_tfidf", "TF-IDF").replace("_", " ")


def _zoom_domain(values: list[float], *, min_span: float = 0.04) -> list[float]:
    valid = [float(v) for v in values if v is not None]
    if not valid:
        return [0.0, 1.0]
    lo = min(valid)
    hi = max(valid)
    span = max(hi - lo, min_span)
    pad = span * 0.30
    center = (lo + hi) / 2
    domain_lo = max(0.0, min(lo - pad, center - span / 2 - pad / 2))
    domain_hi = min(1.0, max(hi + pad, center + span / 2 + pad / 2))
    if domain_hi - domain_lo < min_span:
        shortfall = min_span - (domain_hi - domain_lo)
        domain_lo = max(0.0, domain_lo - shortfall / 2)
        domain_hi = min(1.0, domain_hi + shortfall / 2)
    return [round(domain_lo, 4), round(domain_hi, 4)]


def _stagger_scatter_labels(
    rows: list[dict],
    *,
    x_field: str,
    y_field: str,
    x_domain: list[float],
    y_domain: list[float],
) -> None:
    y_span = max(float(y_domain[1]) - float(y_domain[0]), 0.01)
    x_span = max(float(x_domain[1]) - float(x_domain[0]), 0.01)
    offsets = [0.00, 0.035, -0.035, 0.07, -0.07, 0.105, -0.105]
    sorted_rows = sorted(rows, key=lambda row: (row.get(x_field, 0), row.get(y_field, 0)))
    for idx, row in enumerate(sorted_rows):
        row["model_label"] = _model_plot_label(row.get("model"))
        label_x_min = float(x_domain[0]) + x_span * 0.01
        label_x_max = float(x_domain[1]) - x_span * 0.08
        label_y_min = float(y_domain[0]) + y_span * 0.03
        label_y_max = float(y_domain[1]) - y_span * 0.03
        row["label_x"] = min(
            label_x_max,
            max(label_x_min, float(row[x_field]) + x_span * 0.012),
        )
        row["label_y"] = min(
            label_y_max,
            max(label_y_min, float(row[y_field]) + offsets[idx % len(offsets)] * y_span),
        )


def _scatter_metric_table(rows: list[dict], *, y_title: str, x_title: str) -> list[dict]:
    table = []
    for row in rows:
        table.append({
            "Model": row.get("model_label") or _model_plot_label(row.get("model")),
            y_title: fmt_pct(row.get("high_acuity_recall")),
            x_title: fmt_pct(row.get("high_acuity_precision") or row.get("specificity")),
            "Predicted urgent rate": fmt_pct(row.get("predicted_urgent_rate")),
            "Severe under-triage": fmt_pct(row.get("severe_under_triage_rate")),
            "Macro F1": (
                "N/A" if row.get("macro_f1") is None else f"{float(row.get('macro_f1')):.3f}"
            ),
        })
    return table


def _render_candidate_safety_tradeoff(candidates: list[dict]) -> None:
    rows = []
    for cand in candidates:
        recall = _candidate_high_acuity_recall(cand)
        specificity, urgent_rate = _candidate_over_triage_view(cand)
        under_over = cand.get("under_over_triage") or {}
        severe_under = _as_float(under_over.get("severe_under_triage_rate"))
        if recall is None or specificity is None:
            continue
        rows.append({
            "model": cand.get("model_name") or "unknown",
            "high_acuity_recall": recall,
            "specificity": specificity,
            "predicted_urgent_rate": urgent_rate,
            "severe_under_triage_rate": severe_under,
            "macro_f1": cand.get("macro_f1"),
            "accuracy": cand.get("accuracy"),
        })
    if not rows:
        return
    recall_domain = _zoom_domain([row["high_acuity_recall"] for row in rows])
    specificity_domain = _zoom_domain([row["specificity"] for row in rows], min_span=0.08)
    _stagger_scatter_labels(
        rows,
        x_field="high_acuity_recall",
        y_field="specificity",
        x_domain=recall_domain,
        y_domain=specificity_domain,
    )
    st.markdown("#### Candidate Safety Trade-Off")
    st.vega_lite_chart(
        {
            "data": {"values": rows},
            "height": 340,
            "layer": [
                {
                    "mark": {
                        "type": "circle",
                        "filled": True,
                        "size": 150,
                        "opacity": 0.92,
                        "stroke": "#0f172a",
                        "strokeWidth": 0.8,
                    },
                    "encoding": {
                        "x": {
                            "field": "high_acuity_recall",
                            "type": "quantitative",
                            "title": "High-acuity recall",
                            "scale": {"domain": recall_domain, "nice": False},
                            "axis": {
                                "format": ".1%",
                                "grid": True,
                                "labelFlush": False,
                                "tickCount": 6,
                                "labelOverlap": True,
                            },
                        },
                        "y": {
                            "field": "specificity",
                            "type": "quantitative",
                            "title": "Specificity",
                            "scale": {"domain": specificity_domain, "nice": False},
                            "axis": {"format": ".1%", "grid": True, "tickCount": 6},
                        },
                        "color": {
                            "field": "predicted_urgent_rate",
                            "type": "quantitative",
                            "title": "Predicted urgent rate",
                            "scale": {"scheme": "blues"},
                        },
                        "tooltip": [
                            {"field": "model", "type": "nominal"},
                            {"field": "high_acuity_recall", "type": "quantitative", "format": ".3f"},
                            {"field": "specificity", "type": "quantitative", "format": ".3f"},
                            {"field": "predicted_urgent_rate", "type": "quantitative", "format": ".3f"},
                            {"field": "severe_under_triage_rate", "type": "quantitative", "format": ".4f"},
                            {"field": "macro_f1", "type": "quantitative", "format": ".3f"},
                            {"field": "accuracy", "type": "quantitative", "format": ".3f"},
                        ],
                    },
                },
                {
                    "mark": {
                        "type": "text",
                        "align": "left",
                        "baseline": "middle",
                        "dx": 6,
                        "fontSize": 12,
                        "fontWeight": "normal",
                        "limit": 170,
                    },
                    "encoding": {
                        "x": {
                            "field": "label_x",
                            "type": "quantitative",
                            "scale": {"domain": recall_domain, "nice": False},
                        },
                        "y": {
                            "field": "label_y",
                            "type": "quantitative",
                            "scale": {"domain": specificity_domain, "nice": False},
                        },
                        "text": {"field": "model_label", "type": "nominal"},
                    },
                },
            ],
            "config": {
                "view": {"stroke": "#dbe3ef"},
                "axis": {"gridColor": "#e6edf5", "labelColor": "#64748b", "titleColor": "#64748b"},
            },
        },
        use_container_width=True,
    )
    st.dataframe(
        _scatter_metric_table(
            sorted(rows, key=lambda row: row["high_acuity_recall"], reverse=True),
            y_title="High-acuity recall",
            x_title="Specificity",
        ),
        hide_index=True,
        width="stretch",
    )


def _render_precision_recall_scatter(candidates: list[dict]) -> None:
    rows = []
    for cand in candidates:
        recall = _candidate_high_acuity_recall(cand)
        precision, precision_source = _candidate_precision_for_plot(cand)
        if recall is None or precision is None:
            continue
        rows.append({
            "model": cand.get("model_name") or "unknown",
            "high_acuity_recall": recall,
            "high_acuity_precision": precision,
            "precision_source": precision_source,
            "specificity": _candidate_over_triage_view(cand)[0],
            "predicted_urgent_rate": _candidate_over_triage_view(cand)[1],
            "severe_under_triage_rate": (cand.get("under_over_triage") or {}).get(
                "severe_under_triage_rate"
            ),
            "macro_f1": cand.get("macro_f1"),
            "accuracy": cand.get("accuracy"),
        })
    if not rows:
        _render_candidate_safety_tradeoff(candidates)
        return
    recall_domain = _zoom_domain([row["high_acuity_recall"] for row in rows])
    precision_domain = _zoom_domain([row["high_acuity_precision"] for row in rows])
    _stagger_scatter_labels(
        rows,
        x_field="high_acuity_precision",
        y_field="high_acuity_recall",
        x_domain=precision_domain,
        y_domain=recall_domain,
    )
    st.markdown("#### Recall vs Precision - all models")
    st.vega_lite_chart(
        {
            "data": {"values": rows},
            "height": 340,
            "layer": [
                {
                    "mark": {
                        "type": "circle",
                        "filled": True,
                        "size": 150,
                        "opacity": 0.92,
                        "stroke": "#0f172a",
                        "strokeWidth": 0.8,
                    },
                    "encoding": {
                        "x": {
                            "field": "high_acuity_precision",
                            "type": "quantitative",
                            "title": "High-acuity precision",
                            "scale": {"domain": precision_domain, "nice": False},
                            "axis": {
                                "format": ".1%",
                                "grid": True,
                                "labelFlush": False,
                                "tickCount": 6,
                                "labelOverlap": True,
                            },
                        },
                        "y": {
                            "field": "high_acuity_recall",
                            "type": "quantitative",
                            "title": "High-acuity recall",
                            "scale": {"domain": recall_domain, "nice": False},
                            "axis": {"format": ".1%", "grid": True, "tickCount": 6},
                        },
                        "color": {
                            "field": "predicted_urgent_rate",
                            "type": "quantitative",
                            "title": "Predicted urgent rate",
                            "scale": {"scheme": "blues"},
                        },
                        "tooltip": [
                            {"field": "model", "type": "nominal"},
                            {"field": "high_acuity_recall", "type": "quantitative", "format": ".3f"},
                            {"field": "high_acuity_precision", "type": "quantitative", "format": ".3f"},
                            {"field": "precision_source", "type": "nominal"},
                            {"field": "macro_f1", "type": "quantitative", "format": ".3f"},
                            {"field": "accuracy", "type": "quantitative", "format": ".3f"},
                        ],
                    },
                },
                {
                    "mark": {
                        "type": "text",
                        "align": "left",
                        "baseline": "middle",
                        "dx": 6,
                        "fontSize": 12,
                        "limit": 170,
                    },
                    "encoding": {
                        "x": {
                            "field": "label_x",
                            "type": "quantitative",
                            "scale": {"domain": precision_domain, "nice": False},
                        },
                        "y": {
                            "field": "label_y",
                            "type": "quantitative",
                            "scale": {"domain": recall_domain, "nice": False},
                        },
                        "text": {"field": "model_label", "type": "nominal"},
                    },
                },
            ],
            "config": {
                "view": {"stroke": "#dbe3ef"},
                "axis": {"gridColor": "#e6edf5", "labelColor": "#64748b", "titleColor": "#64748b"},
            },
        },
        use_container_width=True,
    )
    st.dataframe(
        _scatter_metric_table(
            sorted(rows, key=lambda row: row["high_acuity_recall"], reverse=True),
            y_title="High-acuity recall",
            x_title="High-acuity precision",
        ),
        hide_index=True,
        width="stretch",
    )


def _render_confusion_matrix(labels: list[str], matrix: list[list[int]]) -> None:
    if not labels or not matrix:
        return
    display_labels = []
    for label in labels:
        try:
            numeric = float(label)
            display_labels.append(str(int(numeric)) if numeric.is_integer() else str(label))
        except (TypeError, ValueError):
            display_labels.append(str(label))

    rows = []
    total_count = 0
    exact_count = 0
    under_count = 0
    severe_under_count = 0
    over_count = 0
    for i, true_label in enumerate(labels):
        row = matrix[i] if i < len(matrix) else []
        row_total = sum(int(v or 0) for v in row)
        for j, predicted_label in enumerate(labels):
            count = int(row[j] if j < len(row) and row[j] is not None else 0)
            row_percent = count / row_total if row_total else 0.0
            total_count += count
            try:
                true_num = float(true_label)
                predicted_num = float(predicted_label)
                acuity_delta = predicted_num - true_num
            except (TypeError, ValueError):
                acuity_delta = 0.0 if i == j else None
            if acuity_delta == 0:
                exact_count += count
                pattern = "Exact"
            elif acuity_delta is not None and acuity_delta > 0:
                under_count += count
                if acuity_delta >= 2:
                    severe_under_count += count
                pattern = "Under-triage"
            elif acuity_delta is not None and acuity_delta < 0:
                over_count += count
                pattern = "Over-triage"
            else:
                pattern = "Off diagonal"
            rows.append({
                "true_label": display_labels[i],
                "predicted_label": display_labels[j],
                "count": count,
                "row_total": row_total,
                "row_percent": row_percent,
                "percent_label": f"{row_percent:.1%}",
                "count_label": f"{count:,}",
                "pattern": pattern,
            })

    def _rate(count: int) -> str:
        return f"{(count / total_count):.1%}" if total_count else "0.0%"

    st.markdown("#### Selected Model Confusion Matrix")
    exact_col, under_col, severe_col, over_col = st.columns(4)
    exact_col.metric("Exact", _rate(exact_count), f"{exact_count:,} cases")
    under_col.metric("Under-triage", _rate(under_count), f"{under_count:,} cases")
    severe_col.metric(
        "Severe under-triage",
        _rate(severe_under_count),
        f"{severe_under_count:,} cases",
    )
    over_col.metric("Over-triage", _rate(over_count), f"{over_count:,} cases")
    st.vega_lite_chart(
        {
            "data": {"values": rows},
            "height": 360,
            "layer": [
                {
                    "mark": {"type": "rect", "stroke": "#ffffff", "strokeWidth": 1.5},
                    "encoding": {
                        "x": {
                            "field": "predicted_label",
                            "type": "nominal",
                            "title": "Predicted acuity",
                            "sort": display_labels,
                            "axis": {"labelAngle": 0, "labelFontSize": 12, "titleFontSize": 13},
                        },
                        "y": {
                            "field": "true_label",
                            "type": "nominal",
                            "title": "True acuity",
                            "sort": display_labels,
                            "axis": {"labelFontSize": 12, "titleFontSize": 13},
                        },
                        "color": {
                            "field": "row_percent",
                            "type": "quantitative",
                            "title": "% of true acuity row",
                            "scale": {
                                "domain": [0, 1],
                                "range": [
                                    "#f8fafc",
                                    "#dbeafe",
                                    "#93c5fd",
                                    "#2563eb",
                                    "#172554",
                                ],
                            },
                        },
                        "tooltip": [
                            {"field": "true_label", "type": "nominal", "title": "True acuity"},
                            {"field": "predicted_label", "type": "nominal", "title": "Predicted acuity"},
                            {"field": "pattern", "type": "nominal", "title": "Pattern"},
                            {"field": "count", "type": "quantitative", "title": "Cases", "format": ","},
                            {"field": "row_total", "type": "quantitative", "title": "True-row total", "format": ","},
                            {
                                "field": "row_percent",
                                "type": "quantitative",
                                "title": "Row percent",
                                "format": ".1%",
                            },
                        ],
                    },
                },
                {
                    "mark": {"type": "text", "fontSize": 13, "fontWeight": "bold", "dy": -7},
                    "encoding": {
                        "x": {"field": "predicted_label", "type": "nominal", "sort": display_labels},
                        "y": {"field": "true_label", "type": "nominal", "sort": display_labels},
                        "text": {"field": "percent_label", "type": "nominal"},
                        "color": {
                            "condition": {"test": "datum.row_percent >= 0.45", "value": "#ffffff"},
                            "value": "#0f172a",
                        },
                    },
                },
                {
                    "mark": {"type": "text", "fontSize": 11, "dy": 10},
                    "encoding": {
                        "x": {"field": "predicted_label", "type": "nominal", "sort": display_labels},
                        "y": {"field": "true_label", "type": "nominal", "sort": display_labels},
                        "text": {"field": "count_label", "type": "nominal"},
                        "color": {
                            "condition": {"test": "datum.row_percent >= 0.45", "value": "#ffffff"},
                            "value": "#334155",
                        },
                    },
                },
            ],
        },
        use_container_width=True,
    )
    with st.expander("Confusion matrix counts", expanded=False):
        st.dataframe(
            [
                {
                    "true_acuity": row["true_label"],
                    "predicted_acuity": row["predicted_label"],
                    "cases": row["count"],
                    "true_row_percent": row["row_percent"],
                    "pattern": row["pattern"],
                }
                for row in rows
            ],
            hide_index=True,
        )


def _render_binary_curves(roc_curve: dict | None, pr_curve: dict | None) -> None:
    roc_points = (
        roc_curve.get("points")
        if isinstance(roc_curve, dict) and isinstance(roc_curve.get("points"), list)
        else []
    )
    pr_points = (
        pr_curve.get("points")
        if isinstance(pr_curve, dict) and isinstance(pr_curve.get("points"), list)
        else []
    )
    if not roc_points and not pr_points:
        return

    st.markdown("#### Selected Model Binary ROC and Precision-Recall Curves")
    curve_cols = st.columns(2)

    if roc_points:
        with curve_cols[0]:
            st.markdown("**ROC curve: high acuity 1-2 vs 3-5**")
            st.vega_lite_chart(
                {
                    "data": {"values": roc_points},
                    "height": 300,
                    "layer": [
                        {
                            "mark": {"type": "line", "color": "#0b5cab", "strokeWidth": 2.5},
                            "encoding": {
                                "x": {
                                    "field": "false_positive_rate",
                                    "type": "quantitative",
                                    "title": "False positive rate",
                                    "scale": {"domain": [0.0, 1.0], "nice": False},
                                    "axis": {"format": ".0%", "grid": True, "tickCount": 6},
                                },
                                "y": {
                                    "field": "true_positive_rate",
                                    "type": "quantitative",
                                    "title": "True positive rate / recall",
                                    "scale": {"domain": [0.0, 1.0], "nice": False},
                                    "axis": {"format": ".0%", "grid": True, "tickCount": 6},
                                },
                                "tooltip": [
                                    {"field": "false_positive_rate", "type": "quantitative", "format": ".3f"},
                                    {"field": "true_positive_rate", "type": "quantitative", "format": ".3f"},
                                    {"field": "threshold", "type": "quantitative", "format": ".3f"},
                                ],
                            },
                        },
                        {
                            "data": {
                                "values": [
                                    {"false_positive_rate": 0.0, "true_positive_rate": 0.0},
                                    {"false_positive_rate": 1.0, "true_positive_rate": 1.0},
                                ]
                            },
                            "mark": {
                                "type": "line",
                                "color": "#94a3b8",
                                "strokeDash": [5, 5],
                                "strokeWidth": 1,
                            },
                            "encoding": {
                                "x": {"field": "false_positive_rate", "type": "quantitative"},
                                "y": {"field": "true_positive_rate", "type": "quantitative"},
                            },
                        },
                    ],
                    "config": {
                        "view": {"stroke": "#dbe3ef"},
                        "axis": {
                            "gridColor": "#e6edf5",
                            "labelColor": "#64748b",
                            "titleColor": "#64748b",
                        },
                    },
                },
                use_container_width=True,
            )
            if roc_curve.get("downsampled"):
                st.caption(
                    f"Showing {roc_curve.get('display_point_count')} of "
                    f"{roc_curve.get('point_count')} aggregate threshold points."
                )

    if pr_points:
        with curve_cols[1]:
            st.markdown("**Precision-recall curve: high acuity 1-2**")
            st.vega_lite_chart(
                {
                    "data": {"values": pr_points},
                    "height": 300,
                    "mark": {"type": "line", "color": "#087f5b", "strokeWidth": 2.5},
                    "encoding": {
                        "x": {
                            "field": "recall",
                            "type": "quantitative",
                            "title": "Recall",
                            "scale": {"domain": [0.0, 1.0], "nice": False},
                            "axis": {"format": ".0%", "grid": True, "tickCount": 6},
                        },
                        "y": {
                            "field": "precision",
                            "type": "quantitative",
                            "title": "Precision",
                            "scale": {"domain": [0.0, 1.0], "nice": False},
                            "axis": {"format": ".0%", "grid": True, "tickCount": 6},
                        },
                        "tooltip": [
                            {"field": "recall", "type": "quantitative", "format": ".3f"},
                            {"field": "precision", "type": "quantitative", "format": ".3f"},
                            {"field": "threshold", "type": "quantitative", "format": ".3f"},
                        ],
                    },
                    "config": {
                        "view": {"stroke": "#dbe3ef"},
                        "axis": {
                            "gridColor": "#e6edf5",
                            "labelColor": "#64748b",
                            "titleColor": "#64748b",
                        },
                    },
                },
                use_container_width=True,
            )
            if pr_curve.get("downsampled"):
                st.caption(
                    f"Showing {pr_curve.get('display_point_count')} of "
                    f"{pr_curve.get('point_count')} aggregate threshold points."
                )


st.title("AI Triage Agentic Workflow")
st.caption("Backend-driven ED acuity workflow.")

# ---------------------------------------------------------------------------
# SECURITY: establish the authenticated identity (Phase 1) BEFORE the tabs, so
# pages/actions can be gated by role. Real authentication (Entra SSO / MFA /
# conditional access) and network controls (hospital-managed device, VPN /
# private network, Azure Container Apps internal ingress) are provided by the
# HOSPITAL in front of this app; the app reads the verified identity and
# enforces app-level RBAC on top. Fails closed in patient-data mode.
# ---------------------------------------------------------------------------
from frontend import api_client as _session_api_client

PERM_VIEW_CASE = "can_view_case"
PERM_RUN_ASSESSMENT = "can_run_assessment"
PERM_SUBMIT_REVIEW = "can_submit_review"
PERM_VIEW_WORKFLOW_QUEUE = "can_view_workflow_queue"
PERM_ASK_CHATBOT = "can_ask_chatbot"
PERM_VIEW_AUDIT_LOG = "can_view_audit_log"
PERM_VIEW_MODEL_PERFORMANCE = "can_view_model_performance"
PERM_VIEW_SECURITY_STATUS = "can_view_security_status"


def _load_auth_session() -> dict:
    try:
        payload = _session_api_client.auth_session()
        return payload if isinstance(payload, dict) else {}
    except _session_api_client.BackendError as exc:
        st.error(f"Backend identity/session unavailable (HTTP {exc.status_code}).")
        st.stop()
    except Exception as exc:
        st.error(f"Backend identity/session unavailable: {exc}")
        st.stop()


auth_session = _load_auth_session()
_demo_mode = bool(auth_session.get("demo_role_switcher_available"))

with st.sidebar:
    st.markdown("### Identity & Access")
    if _demo_mode:
        _role_choice = st.selectbox(
            "Review role",
            auth_session.get("all_roles") or ["triage_nurse"],
            index=(auth_session.get("all_roles") or ["triage_nurse"]).index("triage_nurse")
            if "triage_nurse" in (auth_session.get("all_roles") or ["triage_nurse"])
            else 0,
            format_func=lambda role: (auth_session.get("role_display_names") or {}).get(role, role),
            key="demo_role",
        )
        # Refresh only when the selected role differs from the backend's
        # current session view. This keeps the role switcher accurate without
        # making a duplicate session call on every rerun.
        _current_demo_role = (auth_session.get("roles") or [None])[0]
        if _role_choice != _current_demo_role:
            auth_session = _load_auth_session()
        _session_api_client.ui_access(
            None,
            "demo_identity_session",
            "sidebar_identity",
            detail=f"Selected review role: {_role_choice}",
        )
    else:
        if not auth_session.get("authenticated"):
            st.error(
                "Locked: **Access denied - no verified identity.** This instance "
                "requires authentication via the hospital identity provider "
                "(Entra SSO) behind a trusted proxy."
            )
            try:
                _session_api_client.ui_access(
                    None,
                    "access_denied_no_identity",
                    "sidebar_identity",
                    detail="auth-required mode, no trusted identity",
                )
            except Exception:
                pass
            st.stop()  # fail closed: render nothing further
        else:
            st.success(f"Authenticated: {auth_session.get('display_name') or auth_session.get('user_id')}")
            st.caption(
                f"Roles: {', '.join(auth_session.get('display_roles') or auth_session.get('roles') or []) or 'none'} "
                f"source: {auth_session.get('source')}"
            )
            if auth_session.get("current_mode") == "local_credentialed_research":
                st.info(
                    "Role switching disabled in local credentialed research mode. "
                    "To change role, set LOCAL_RESEARCH_ROLE and restart the backend."
                )
                if auth_session.get("local_role_change_instruction"):
                    st.caption(auth_session["local_role_change_instruction"])

    # Show the current effective permissions.
    with st.expander("Your permissions", expanded=False):
        perms = sorted(auth_session.get("permissions") or [])
        st.write(perms if perms else "No permissions (unauthenticated or unmapped role).")

    if auth_session.get("authenticated") and PERM_VIEW_CASE in set(auth_session.get("permissions") or []):
        _render_notification_bell(load_cases())


def _has_perm(permission: str) -> bool:
    return permission in set(auth_session.get("permissions") or [])


def _role_text() -> str:
    return ", ".join(auth_session.get("display_roles") or auth_session.get("roles") or []) or "none"


def _local_role_restart_hint(*, target: str) -> str:
    legacy_phrase = (
        "Your current role cannot view full model performance. "
        if target == "model performance"
        else ""
    )
    if auth_session.get("current_mode") != "local_credentialed_research":
        return (
            f"Your current role is {_role_text()}. {legacy_phrase}"
            f"This role cannot view {target}. "
            "Switch to an authorised role, or sign in with a role "
            "that has the required permission."
        )
    if target == "model performance":
        wanted = "researcher or governance_auditor"
    elif target == "security status":
        wanted = "security_admin"
    else:
        wanted = "an authorised LOCAL_RESEARCH_ROLE"
    return (
        f"Your current role is {_role_text()}. {legacy_phrase}"
        f"This role cannot view {target}. "
        f"Set LOCAL_RESEARCH_ROLE={wanted} and restart the backend."
    )


def _gate_tab(permission: str, action: str, page_name: str) -> bool:
    """Gate a whole tab/page by permission. Returns True if allowed (audited
    by the backend); otherwise shows a clear denial and returns False."""
    try:
        decision = _session_api_client.ui_access(permission, action, page_name)
    except Exception as exc:
        st.error(f"Backend access check failed for **{page_name}**: {exc}")
        return False
    if decision.get("allowed"):
        return True
    st.error(
        f"Locked: **Access denied.** Your role(s) "
        f"({_role_text()}) cannot access **{page_name}**. "
        f"Requires the `{permission}` permission."
    )
    return False



def _workflow_action_badge(action: str) -> str:
    if action == "ESCALATION_REQUIRED":
        return f"Red {action}"
    if action == "CLINICIAN_INTERVENTION_REQUIRED":
        return f"Amber {action}"
    return f"Green {action}"



TAB_DEFS = [
    ("triage_review", "Triage Review"),
    ("explainability", "Explainability"),
    ("review_queue", "Review Queue"),
    ("audit_dashboard", "Audit Dashboard"),
    ("governance", "Governance & System Health"),
    ("model_performance", "Model Performance"),
    ("maintainability", "Maintainability"),
    ("itd_ask_tools", "ITD System Assistant"),
]
_visible_tab_keys = set(auth_session.get("visible_tabs") or [])
if not _visible_tab_keys:
    _visible_tab_keys = {"triage_review", "review_queue", "explainability", "maintainability"}
    if _has_perm(PERM_VIEW_AUDIT_LOG):
        _visible_tab_keys.update({"audit_dashboard", "governance"})
    if _has_perm(PERM_VIEW_MODEL_PERFORMANCE):
        _visible_tab_keys.update({"model_performance", "explainability"})
    if _has_perm(PERM_VIEW_SECURITY_STATUS):
        _visible_tab_keys.update({"governance", "maintainability", "itd_ask_tools"})
_visible_tab_keys -= {"followup_comparison", "cost_runtime", "system_status"}
_visible_tab_defs = [(key, label) for key, label in TAB_DEFS if key in _visible_tab_keys]
if not _visible_tab_defs:
    st.error("No application sections are available for this role.")
    st.stop()
_tab_handles = st.tabs([label for _, label in _visible_tab_defs])
_tab_map = {key: handle for (key, _label), handle in zip(_visible_tab_defs, _tab_handles)}
tab_triage = _tab_map.get("triage_review")
tab_explainability = _tab_map.get("explainability")
tab_queue = _tab_map.get("review_queue")
tab_audit = _tab_map.get("audit_dashboard")
tab_governance = _tab_map.get("governance")
tab_models = _tab_map.get("model_performance")
tab_cost = None
tab_system = None
tab_maintainability = _tab_map.get("maintainability")
tab_followup = tab_maintainability
tab_itd_ask = _tab_map.get("itd_ask_tools")


# ---------------------------------------------------------------------------
# TAB 1 - TRIAGE REVIEW
# ---------------------------------------------------------------------------
def _render_triage_review_submission(
    case_uid: str,
    source_dataset: str,
    key_prefix: str = "triage",
    workflow_state: dict | None = None,
):
    """Backend-driven clinician review submission for a case_uid. Identity comes
    from the authenticated context (no role dropdown in patient-data mode); the
    write goes through POST /cases/{case_uid}/reviews (RBAC + guarded, fail-closed).
    """
    from frontend import api_client
    if not _has_perm(PERM_SUBMIT_REVIEW):
        st.caption("Locked: You do not have permission to submit a clinician review.")
        return
    workflow_state = workflow_state or {}
    if str(workflow_state.get("case_status") or "").lower() in {"discharged", "closed", "case_closed"}:
        st.caption("Case is discharged/closed; no further clinical workflow action is available.")
        return
    _roles = set(auth_session.get("roles") or [])
    _can_resolve_escalation = bool(
        _roles.intersection({"ed_doctor", "clinical_supervisor", "security_admin"})
    )
    _escalation_state = str(
        workflow_state.get("escalation_state") or workflow_state.get("escalation_status") or ""
    ).lower()
    _review_options = [
        "REQUEST_MORE_INFORMATION",
        "NOT_REVIEWED",
        "ACCEPTED_AS_PRESENTED",
        "OVERRIDE_REQUIRED",
        "ESCALATION_REQUIRED",
        "REJECTED_DATA_QUALITY",
    ]
    if _can_resolve_escalation and _escalation_state in {"requested", "pending"}:
        _review_options.append("ESCALATION_CONFIRMED")
    if _can_resolve_escalation and _escalation_state in {"requested", "pending", "confirmed"}:
        _review_options.extend(["ESCALATION_REJECTED", "ESCALATION_CLOSED"])
    if _can_resolve_escalation:
        _review_options.append("DISCHARGED")
    with st.form(f"review_form_{key_prefix}_{case_uid}"):
        st.markdown("**Submit clinician review**")
        c1, c2 = st.columns(2)
        if _session_api_client.reads_must_use_backend():
            _roles = _role_text() or "(no mapped role)"
            c1.text_input("Reviewer (from sign-in)", value=_roles, disabled=True,
                          key=f"role_ro_{key_prefix}_{case_uid}")
        else:
            c1.selectbox("Reviewer role",
                         ["triage_nurse", "ed_doctor", "clinical_supervisor", "researcher"],
                         key=f"role_{key_prefix}_{case_uid}")
        review_status = c2.selectbox(
            "Review decision",
            _review_options,
            key=f"status_{key_prefix}_{case_uid}")
        review_comment = st.text_area(
            "Review notes", value="Clinician review required before any action.",
            key=f"comment_{key_prefix}_{case_uid}")
        requested_fields_text = st.text_input(
            "Information needed",
            value="",
            placeholder="For request-more-information: repeat vitals, clarify pain score",
            key=f"requested_fields_{key_prefix}_{case_uid}",
        )
        target_role = st.selectbox(
            "Escalation target",
            ["clinical_supervisor", "ed_doctor"],
            key=f"target_role_{key_prefix}_{case_uid}",
        )
        if st.form_submit_button(" Save Review State"):
            try:
                requested_fields = [
                    item.strip()
                    for item in requested_fields_text.split(",")
                    if item.strip()
                ]
                payload = {
                    "review_status": review_status,
                    "review_comment": review_comment,
                    "requested_fields": requested_fields,
                    "escalation_target_role": target_role,
                }
                if review_status in {"OVERRIDE_REQUIRED", "ESCALATION_REQUIRED"}:
                    payload["override_reason"] = review_comment
                res = api_client.submit_review(case_uid, payload)
                st.success("OK Review state saved and audit record written by the backend.")
                state = res.get("workflow_state") or {}
                if state:
                    render_workflow_state_banner({"workflow_state": state})
            except api_client.BackendError as exc:
                if exc.status_code in (401, 403):
                    st.error("Locked: The backend denied this review (insufficient permission).")
                elif exc.status_code == 422:
                    st.warning("A reason is required for an override / uncertain decision.")
                else:
                    st.warning(f"Warning: Backend could not save the review (HTTP {exc.status_code}).")


if tab_triage is not None:
    with tab_triage:
        st.subheader("Select ED Stay for Review")
    
        selected_record = render_backend_case_selector("triage_review")
    
        if selected_record is None:
            # No cases available (no credentialed data in this environment). Render
            # nothing further in THIS tab, but do NOT st.stop() - that would also
            # prevent the other tabs (Governance, Queue, Audit, Models)
            # from rendering, since st.tabs() runs all tab bodies in one script pass.
            st.caption("No full-MIMIC cases available. Configure MIMIC_FULL_ED_DIR on an "
                       "approved environment to load cases (the app fails closed otherwise).")
        else:
            _case_uid = selected_record["case_uid"]
            _src = selected_record.get("source_dataset", "")
            from frontend import api_client
    
            # Show the triage-time clinical view the backend returned (already
            # redacted: no raw identifiers). Clinical fields are present only if the
            # caller holds clinical-content permission.
            render_triage_input_summary(selected_record, key_prefix=f"triage_{_case_uid}")
    
            # The assessment runs SERVER-SIDE via the backend (the sole enforcement
            # boundary). The card is rendered from the safe assessment DTO. There is
            # no local run_workflow on any protected path.
            _can_run = _has_perm(PERM_RUN_ASSESSMENT)
            if not _can_run:
                st.info("Locked: You do not have permission to run an assessment.")
            else:
                try:
                    _cache_payload = api_client.assessment_cache_key()
                    _backend_cache_key = _cache_payload.get("assessment_cache_key", "unknown")
                except Exception:
                    _backend_cache_key = "unknown"
                _assessment_key = f"assessment_dto::{_case_uid}::{_backend_cache_key}"
                _force_rerun = st.button(
                    "Re-run assessment (server-side, audited)",
                    key=f"rerun_assess_{_case_uid}",
                    type="primary",
                    use_container_width=True,
                )
                if _force_rerun and _assessment_key in st.session_state:
                    del st.session_state[_assessment_key]
                if _assessment_key not in st.session_state:
                    try:
                        with st.spinner("Running backend assessment..."):
                            st.session_state[_assessment_key] = api_client.run_assessment(_case_uid)
                        st.success("Assessment computed and audited by the backend.")
                    except api_client.BackendError as exc:
                        if exc.status_code in (401, 403):
                            st.error("Locked: The backend denied this action (insufficient permission).")
                        else:
                            st.warning(f"Backend could not run the assessment (HTTP {exc.status_code}).")
                if _assessment_key in st.session_state:
                    render_assessment_summary(st.session_state[_assessment_key], _src)
    
            # Review submission - server-side; identity from auth (no role dropdown in
            # patient-data mode). Handled by the shared review form below.
            _render_triage_review_submission(
                _case_uid,
                _src,
                key_prefix="triage",
                workflow_state=_workflow_state(selected_record),
            )
    
    # The reassessment card below is rendered inside Maintainability.
if tab_followup is not None:
    with tab_followup:
        st.subheader("Additional Information & Reassessment")
        if not _has_perm(PERM_RUN_ASSESSMENT):
            st.info("Locked: You do not have permission to run a reassessment.")
        else:
            _fu_case = render_backend_case_selector("followup", label="Case", show_label=True)
            if _fu_case is None:
                st.caption("No full-MIMIC cases available. Configure MIMIC_FULL_ED_DIR on an "
                       "approved environment to load cases (the app fails closed otherwise).")
            else:
                _fu_uid = _fu_case["case_uid"]
                st.markdown("**Original triage-time values**")
                render_triage_input_summary(_fu_case, key_prefix=f"followup_original_{_fu_uid}")
                st.markdown("**Updated triage-time vitals** (enter only what changed)")
                fc1, fc2, fc3 = st.columns(3)
                updated = {}
                _hr = fc1.text_input("Heart rate", key=f"fu_hr_{_fu_uid}")
                _o2 = fc2.text_input("SpO2 %", key=f"fu_o2_{_fu_uid}")
                _sbp = fc3.text_input("Systolic BP", key=f"fu_sbp_{_fu_uid}")
                _rr = fc1.text_input("Resp rate", key=f"fu_rr_{_fu_uid}")
                _temp = fc2.text_input("Temperature", key=f"fu_temp_{_fu_uid}")
                _updated_complaint = st.text_input(
                    "Updated complaint/context at triage",
                    key=f"fu_complaint_{_fu_uid}",
                    placeholder="Optional: clarify or update the chief complaint",
                )
                _updated_context = st.text_area(
                    "Clinician-supplied additional information",
                    key=f"fu_context_{_fu_uid}",
                    height=80,
                    placeholder="Optional: brief triage-time context for reassessment evidence",
                )
                _scan_files = st.file_uploader(
                    "Optional scan/image metadata",
                    accept_multiple_files=True,
                    type=["png", "jpg", "jpeg", "pdf"],
                    key=f"fu_scan_upload_{_fu_uid}",
                )
                _fu_result_key = f"followup_result::{_fu_uid}"
                _fu_updated_key = f"followup_updated::{_fu_uid}"
                _fu_complaint_key = f"followup_complaint::{_fu_uid}"
                if st.button(
                    "Run reassessment (server-side)",
                    key=f"fu_btn_{_fu_uid}",
                    type="primary",
                    use_container_width=True,
                ):
                    _invalid_vitals = []
                    for _k, _v in (("heartrate", _hr), ("o2sat", _o2), ("sbp", _sbp),
                                   ("resprate", _rr), ("temperature", _temp)):
                        if str(_v).strip():
                            try:
                                updated[_k] = float(_v)
                            except ValueError:
                                _invalid_vitals.append(_k)
                    import hashlib
                    _scan_metadata = []
                    for _file in _scan_files or []:
                        _bytes = _file.getvalue()
                        _scan_metadata.append({
                            "filename": _file.name,
                            "content_type": _file.type,
                            "size_bytes": len(_bytes),
                            "sha256": hashlib.sha256(_bytes).hexdigest(),
                                "analysis_status": "metadata_recorded_pending_multimodal_analysis",
                        })
                    _complaint_clean = _updated_complaint.strip() or None
                    _context_clean = _updated_context.strip() or None
                    if _invalid_vitals:
                        st.warning(
                            "These updated vitals must be numeric: "
                            + ", ".join(_invalid_vitals)
                        )
                    elif not updated and not _complaint_clean and not _context_clean and not _scan_metadata:
                        st.warning("Enter updated vitals, complaint/context, or scan metadata.")
                    else:
                        from frontend import api_client
                        try:
                            _r = api_client.followup_case(
                                _fu_uid,
                                updated,
                                updated_complaint=_complaint_clean,
                                updated_context=_context_clean,
                                scan_uploads=_scan_metadata,
                            )
                            st.session_state[_fu_result_key] = _r
                            st.session_state[_fu_updated_key] = dict(updated)
                            st.session_state[_fu_complaint_key] = _complaint_clean
                            st.success("Reassessment computed and stored for comparison.")
                        except api_client.BackendError as exc:
                            if exc.status_code in (401, 403):
                                st.error("Locked: The backend denied this reassessment.")
                            else:
                                st.warning(f"Warning: Reassessment unavailable (HTTP {exc.status_code}).")
    
                _fu_result = st.session_state.get(_fu_result_key)
                if _fu_result:
                    st.markdown("**Reassessment comparison**")
                    cprev, cnew = st.columns(2)
                    with cprev:
                        _render_mts_colour_box(
                            "Previous result",
                            _fu_result.get("previous_acuity"),
                            _fu_result.get("previous_manchester_equivalent"),
                        )
                    with cnew:
                        _render_mts_colour_box(
                            "New result",
                            _fu_result.get("new_acuity"),
                            _fu_result.get("new_manchester_equivalent"),
                        )
                    _chg = _fu_result.get("change")
                    _summary = _fu_result.get("change_summary")
                    if _chg == "escalation":
                        st.error(_summary or "Escalation detected.")
                        render_workflow_state_banner({
                            "workflow_state": _fu_result.get("workflow_state") or _fu_result
                        })
                    elif _chg == "de-escalation":
                        st.success(_summary or "De-escalation detected.")
                    else:
                        st.info(_summary or "No change in predicted acuity.")
                    st.caption(f"Changed fields: {_fu_result.get('changed_fields')}.")
    
if tab_governance is not None:
    with tab_governance:
        _governance_allowed = _gate_tab(PERM_VIEW_AUDIT_LOG, "governance_report", "Governance")
        if _governance_allowed:
            st.subheader("Governance & System Health")
    
            from frontend import api_client as _governance_api_client
            try:
                _governance_report = _governance_api_client.governance_report()
                if not isinstance(_governance_report, dict):
                    _governance_report = {}
    
                def _governance_label(value) -> str:
                    return str(value or "").replace("_", " ").strip().title()
    
                def _governance_brief(value, *, limit: int = 180) -> str:
                    if value is None:
                        return ""
                    if isinstance(value, dict):
                        parts = [
                            f"{_governance_label(k)}: {_governance_brief(v, limit=70)}"
                            for k, v in list(value.items())[:4]
                        ]
                        text = "; ".join(parts)
                    elif isinstance(value, list):
                        text = "; ".join(_governance_brief(v, limit=70) for v in value[:4])
                    else:
                        text = str(value)
                    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."
    
                g1, g2, g3 = st.columns(3)
                g1.metric("App version", _governance_report.get("app_version") or "unknown")
                g2.metric(
                    "Package checkpoint",
                    _governance_report.get("package_checkpoint") or "unknown",
                )
                g3.metric(
                    "Evidence dataset",
                    _governance_report.get("governance_evidence_dataset")
                    or _governance_report.get("default_dataset")
                    or "unknown",
                )
                st.markdown("**System health**")
                try:
                    _runtime_health = _governance_api_client.runtime_status()
                except Exception:
                    _runtime_health = {}
                _fm_health = _runtime_health.get("mimic_full") or {}
                _model_health = _runtime_health.get("model") or {}
                _reports_health = _runtime_health.get("reports") or {}
                h1, h2, h3, h4 = st.columns(4)
                h1.metric("Case source", "loadable" if _fm_health.get("loadable") else "not loadable")
                h2.metric("Model artefact", _model_health.get("state", "unknown"))
                h3.metric("Model reports", "configured" if _reports_health.get("env_present") else "not configured")
                h4.metric("Auth mode", _runtime_health.get("current_mode") or _runtime_health.get("active_profile") or "unknown")
                if _has_perm(PERM_VIEW_SECURITY_STATUS):
                    try:
                        _security_health = _governance_api_client.security_status()
                    except Exception:
                        _security_health = {}
                    if _security_health:
                        with st.expander("ITD advanced system diagnostics", expanded=False):
                            st.dataframe(
                                [
                                    {"check": "security_mode", "status": _security_health.get("current_mode")},
                                    {"check": "audit_sink", "status": _security_health.get("audit_sink")},
                                    {"check": "secrets_provider", "status": _security_health.get("secrets_provider")},
                                    {"check": "cors", "status": _security_health.get("cors_status")},
                                    {"check": "safe_configuration", "status": _security_health.get("is_safe")},
                                ],
                                width="stretch",
                                hide_index=True,
                            )
    
                controls = _governance_report.get("controls") or {}
                control_rows = []
                for key, item in controls.items():
                    item = item if isinstance(item, dict) else {"status": item}
                    control_rows.append({
                        "Control": _governance_label(key),
                        "Status": _governance_label(item.get("status")),
                        "Evidence": _governance_brief(item.get("evidence")),
                    })
                st.markdown("**Governance controls**")
                if control_rows:
                    st.dataframe(control_rows, width="stretch", hide_index=True)
                else:
                    st.caption("No governance controls were returned by the backend.")
    
                boundaries = _governance_report.get("approval_boundaries") or {}
                boundary_rows = [
                    {"Boundary": _governance_label(key), "Status": _governance_brief(value)}
                    for key, value in boundaries.items()
                ]
                if boundary_rows:
                    with st.expander("Approval boundaries", expanded=False):
                        st.dataframe(boundary_rows, width="stretch", hide_index=True)
    
                review_gate = _governance_report.get("responsible_ai_review_gate") or {}
                gate_rows = [
                    {"Stage": _governance_label(key), "Evidence": _governance_brief(value, limit=320)}
                    for key, value in review_gate.items()
                ]
                if gate_rows:
                    with st.expander("Review gate evidence", expanded=True):
                        st.dataframe(gate_rows, width="stretch", hide_index=True)
    
                review_items = _governance_report.get("blocking_issues") or []
                if review_items:
                    with st.expander("Items needing review", expanded=False):
                        for _idx, _item in enumerate(review_items, start=1):
                            st.write(f"{_idx}. {_item}")
    
                st.markdown("**Policy and WandB toolkit**")
                _policy_key = "governance_policy_check_results"
                _wandb_status = {}
                try:
                    _wandb_status = _governance_api_client.governance_wandb_status()
                    if not isinstance(_wandb_status, dict):
                        _wandb_status = {}
                except _governance_api_client.BackendError as exc:
                    if exc.status_code in (401, 403):
                        st.caption("Your role cannot view the governance logging toolkit.")
                    else:
                        st.caption("Governance logging toolkit status is unavailable.")
    
                _wandb_usable = bool(_wandb_status.get("available"))
                _wandb_reason = str(_wandb_status.get("reason") or "").strip()
                toolkit_cols = st.columns(3)
                toolkit_cols[0].metric(
                    "WandB package",
                    "available" if _wandb_usable else "unavailable",
                )
                toolkit_cols[1].metric(
                    "Online logging",
                    "ready" if _wandb_status.get("configured_for_online") else "not configured",
                )
                toolkit_cols[2].metric(
                    "Cloud egress",
                    "allowed" if _wandb_status.get("cloud_egress_allowed") else "blocked",
                )
                if not _wandb_usable and _wandb_reason and debug_ui_enabled():
                    with st.expander("Developer/debug: governance logging detail", expanded=False):
                        st.caption(_wandb_reason)
    
                if st.button(
                    "Run governance policy checks",
                    key="governance_policy_checks_btn",
                    type="secondary",
                ):
                    try:
                        st.session_state[_policy_key] = (
                            _governance_api_client.governance_policy_checks()
                        )
                    except _governance_api_client.BackendError as exc:
                        st.warning(f"Policy checks unavailable (HTTP {exc.status_code}).")
    
                _policy_results = st.session_state.get(_policy_key)
                if isinstance(_policy_results, dict):
                    _checks = (
                        (_policy_results.get("policy_results") or {}).get("checks")
                        or []
                    )
                    if _checks:
                        st.dataframe(_checks, width="stretch", hide_index=True)
                    _red_team = (
                        (_policy_results.get("red_team_results") or {}).get("probes")
                        or []
                    )
                    if _red_team:
                        with st.expander("Policy probe results", expanded=False):
                            st.dataframe(_red_team, width="stretch", hide_index=True)
    
                    if st.button(
                        (
                            "Send latest policy results to WandB"
                            if _wandb_status.get("configured_for_online")
                            else "Create offline WandB run"
                        ),
                        key="governance_log_wandb_btn",
                        type="secondary",
                        disabled=not _wandb_usable,
                    ):
                        try:
                            _wandb_mode = (
                                "online"
                                if _wandb_status.get("configured_for_online")
                                else "offline"
                            )
                            _logged = _governance_api_client.governance_log_wandb({
                                "policy_results": _policy_results.get("policy_results") or {},
                                "red_team_results": _policy_results.get("red_team_results"),
                                "mode": _wandb_mode,
                            })
                            _status = (
                                str(_logged.get("status") or "UNKNOWN").upper()
                                if isinstance(_logged, dict)
                                else "UNKNOWN"
                            )
                            _reason = (
                                _logged.get("reason")
                                if isinstance(_logged, dict)
                                else ""
                            )
                            _run_url = (
                                _logged.get("run_url")
                                if isinstance(_logged, dict)
                                else None
                            )
                            if _status == "LOGGED":
                                st.success("WandB log request completed.")
                                if _run_url:
                                    st.markdown(f"[Open WandB run]({_run_url})")
                            elif _status == "SKIPPED":
                                st.info(
                                    "WandB log request was skipped"
                                    + (f": {_reason}" if _reason else ".")
                                )
                            elif _status == "ERROR":
                                st.error(
                                    "WandB log request failed"
                                    + (f": {_reason}" if _reason else ".")
                                )
                            else:
                                st.warning(f"WandB log request returned status: {_status}.")
                        except _governance_api_client.BackendError as exc:
                            st.warning(f"WandB log request failed (HTTP {exc.status_code}).")
            except _governance_api_client.BackendError as exc:
                if exc.status_code in (401, 403):
                    st.error("The backend denied access to governance evidence.")
                else:
                    st.caption(
                        "Use the API governance report and audit exports for detailed evidence."
                    )
            except Exception:
                st.info("Governance evidence is available through the backend service.")
    
    # ---------------------------------------------------------------------------
    # TAB 5 - REVIEW QUEUE
    # ---------------------------------------------------------------------------
if tab_queue is not None:
    with tab_queue:
        st.subheader(" Human Review Queue")
        st.caption(
            "Backend-driven and full-MIMIC-only. Cases come from the FastAPI backend "
            "(GET /cases); reviews are submitted via the backend (POST "
            "/cases/{case_uid}/reviews). There is no dataset selector and no local "
            "queue from any other data source."
        )
        if not _has_perm(PERM_VIEW_WORKFLOW_QUEUE):
            st.info("Locked: You do not have permission to view or action the review queue.")
        else:
            from frontend import api_client
            if st.button(
                "Run server-side overdue-vitals sweep",
                key="run_overdue_vitals_sweep",
                use_container_width=True,
            ):
                try:
                    _sweep = api_client.sweep_overdue_vitals()
                    st.success(
                        "Overdue-vitals sweep complete: "
                        f"{_sweep.get('created', 0)} notification(s) created."
                    )
                except api_client.BackendError as exc:
                    st.warning(f"Could not run overdue-vitals sweep (HTTP {exc.status_code}).")
            _queue_filter = st.radio(
                "Queue filter",
                ["Active", "Escalated", "Overdue vitals", "Request info", "Discharged", "All"],
                horizontal=True,
                key="review_queue_status_filter",
            )
            _queue_status_map = {
                "Escalated": "escalated",
                "Request info": "request_more_info",
                "Discharged": "discharged",
            }
            try:
                _worklist = api_client.workflow_queue(
                    status=_queue_status_map.get(_queue_filter),
                    limit=250,
                    offset=0,
                )
                _queue_rows = _worklist.get("rows", []) if isinstance(_worklist, dict) else []
                _queue_summary = _worklist.get("summary", {}) if isinstance(_worklist, dict) else {}
            except api_client.BackendError:
                _queue_rows = []
                _queue_summary = {}
            if _queue_filter == "Overdue vitals":
                _queue_rows = [
                    row for row in _queue_rows
                    if bool(row.get("overdue_vitals_alert_active"))
                ]
            elif _queue_filter == "Active":
                _queue_rows = [
                    row for row in _queue_rows
                    if str(row.get("workflow_status") or "") not in {"discharged", "closed"}
                ]
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("New / unreviewed", _queue_summary.get("new_unreviewed", 0))
            q2.metric("Escalation requested", _queue_summary.get("escalation_requested", 0))
            q3.metric("Escalated", _queue_summary.get("escalated", 0))
            q4.metric("Discharged / closed", _queue_summary.get("discharged", 0))
            if _queue_rows:
                st.dataframe(
                    [
                        {
                            "case_uid": row.get("case_uid"),
                            "workflow_status": row.get("workflow_status"),
                            "case_status": row.get("case_status"),
                            "escalation": row.get("escalation_status") or "",
                            "target_role": row.get("escalation_target_role") or "",
                            "overdue_vitals": bool(row.get("overdue_vitals_alert_active")),
                            "source": row.get("source_dataset"),
                        }
                        for row in _queue_rows[:50]
                    ],
                    width="stretch",
                    hide_index=True,
                )
            _chosen = render_backend_case_selector("queue", label="Case", show_label=True)
            _cases = [_chosen] if _chosen is not None else []
    
            if not _cases:
                st.info(
                    "No full-MIMIC cases are available, so the review queue is empty. "
                    "Configure MIMIC_FULL_ED_DIR on an approved environment to populate "
                    "it (the app fails closed otherwise - it never falls back to any other "
                    "data source)."
                )
            else:
                render_triage_input_summary(_chosen, key_prefix=f"queue_{_chosen['case_uid']}")
                _overdue = _vitals_overdue_info(_chosen)
                if _overdue.get("overdue"):
                    st.warning(
                        f"Vitals notification: last vitals check/update was {_overdue.get('minutes'):.0f} minutes ago."
                    )
                    if (
                        not _workflow_state(_chosen).get("overdue_vitals_alert_active")
                        and _has_perm(PERM_SUBMIT_REVIEW)
                        and st.button(
                            "Create overdue-vitals notification",
                            key=f"create_overdue_vitals_{_chosen['case_uid']}",
                            use_container_width=True,
                        )
                    ):
                        try:
                            api_client.mark_overdue_vitals_alert(_chosen["case_uid"])
                            st.success("Overdue-vitals notification created.")
                            st.rerun()
                        except api_client.BackendError as exc:
                            st.warning(f"Could not create notification (HTTP {exc.status_code}).")
                    if _has_perm(PERM_SUBMIT_REVIEW) and st.button(
                        "Acknowledge overdue-vitals notification",
                        key=f"ack_overdue_vitals_{_chosen['case_uid']}",
                        disabled=not bool(_workflow_state(_chosen).get("overdue_vitals_alert_active")),
                        use_container_width=True,
                    ):
                        try:
                            api_client.acknowledge_overdue_vitals(_chosen["case_uid"])
                            st.success("Overdue-vitals notification acknowledged.")
                            st.rerun()
                        except api_client.BackendError as exc:
                            st.warning(f"Could not acknowledge notification (HTTP {exc.status_code}).")
                # Reuse the backend-driven review submission form.
                _render_triage_review_submission(_chosen["case_uid"],
                                                 _chosen.get("source_dataset", ""),
                                                 key_prefix="queue",
                                                 workflow_state=_workflow_state(_chosen))
    
    
if tab_audit is not None:
    with tab_audit:
        if not _gate_tab(PERM_VIEW_AUDIT_LOG, 'view_audit_log', 'Audit Log'):
            st.stop()
        if True:
            st.subheader(" Clinician Review Audit Log")
        st.caption(
            "Complete history of all clinician reviews, across every dataset. "
            "This log is append-only."
        )
        from frontend import api_client
        st.markdown("### Audit Log Dashboard")
        with st.form("audit_dashboard_filters"):
            f1, f2, f3, f4 = st.columns(4)
            _start_date = f1.date_input("Start date", value=None, key="audit_start_date")
            _end_date = f2.date_input("End date", value=None, key="audit_end_date")
            _case_filter = f3.text_input("Case UID / subject filter", key="audit_case_filter")
            _triage_filter = f4.selectbox(
                "Acuity / triage level",
                ["", "1", "2", "3", "4", "5"],
                key="audit_triage_filter",
            )
            f5, f6, f7, f8 = st.columns(4)
            _role_filter = f5.text_input("Reviewer role", key="audit_role_filter")
            _decision_filter = f6.text_input("Decision/action type", key="audit_decision_filter")
            _escalation_filter = f7.selectbox(
                "Escalation status",
                ["", "requested", "pending", "confirmed", "rejected", "closed", "resolved"],
                key="audit_escalation_filter",
            )
            _override_filter = f8.selectbox(
                "Override status",
                ["", "yes", "no"],
                key="audit_override_filter",
            )
            _source_filter = st.text_input("Source dataset / case source", key="audit_source_filter")
            _audit_submit = st.form_submit_button("Apply audit filters")
        _audit_params = {
            "limit": 1000,
            "start_utc": f"{_start_date.isoformat()}T00:00:00+00:00" if _start_date else None,
            "end_utc": f"{_end_date.isoformat()}T23:59:59+00:00" if _end_date else None,
            "patient_or_case": _case_filter,
            "triage_level": _triage_filter,
            "reviewer_role": _role_filter,
            "decision_type": _decision_filter,
            "action_type": _decision_filter,
            "escalation_status": _escalation_filter,
            "override_status": _override_filter,
            "source_dataset": _source_filter,
        }
        try:
            _dash = api_client.audit_dashboard(**_audit_params)
        except api_client.BackendError as exc:
            _dash = {}
            if exc.status_code in (401, 403):
                st.error("The backend denied access to the audit dashboard.")
            else:
                st.warning(f"Audit dashboard unavailable (HTTP {exc.status_code}).")
        if isinstance(_dash, dict) and _dash:
            _agg = _dash.get("aggregations") or {}
            _summary = _agg.get("summary") or {}
            a1, a2, a3, a4, a5 = st.columns(5)
            a1.metric("Total entries", _summary.get("total_entries", 0))
            a2.metric("Reviews", _summary.get("total_reviews", 0))
            a3.metric("Escalations", _summary.get("escalations", 0))
            a4.metric("Overrides", _summary.get("overrides", 0))
            a5.metric("Request info", _summary.get("request_more_info_actions", 0))
            w1, w2, w3, w4, w5 = st.columns(5)
            w1.metric("Open escalations", _summary.get("open_escalations", 0))
            w2.metric("Confirmed escalations", _summary.get("confirmed_escalations", 0))
            w3.metric("Closed escalations", _summary.get("closed_escalations", 0))
            w4.metric("Discharged cases", _summary.get("discharged_cases", 0))
            w5.metric("Overdue vitals alerts", _summary.get("overdue_vitals_alerts", 0))
            avg_confirm = _summary.get("average_escalation_request_to_confirmation_minutes")
            st.metric(
                "Avg escalation request to confirmation",
                "-" if avg_confirm is None else f"{avg_confirm} min",
            )
            _timeline = _agg.get("timeline") or []
            if _timeline:
                st.vega_lite_chart(
                    {"values": _timeline},
                    {
                        "mark": {"type": "line", "point": True},
                        "encoding": {
                            "x": {"field": "date", "type": "temporal", "title": "Date"},
                            "y": {"field": "count", "type": "quantitative", "title": "Audit events"},
                        },
                    },
                    use_container_width=True,
                )
            b1, b2, b3, b4 = st.columns(4)
            for _col, _title, _field in (
                (b1, "By acuity level", "by_triage_level"),
                (b2, "By reviewer role", "by_reviewer_role"),
                (b3, "By decision/action", "by_decision_type"),
                (b4, "Top cases by audit events", "by_case_uid"),
            ):
                _rows = _agg.get(_field) or []
                if _rows:
                    _col.vega_lite_chart(
                        {"values": _rows},
                        {
                            "mark": "bar",
                            "encoding": {
                                "x": {"field": "count", "type": "quantitative"},
                                "y": {"field": "label", "type": "nominal", "sort": "-x", "title": _title},
                            },
                        },
                        use_container_width=True,
                    )
            _escalation_worklist = _agg.get("escalation_worklist") or []
            if _escalation_worklist:
                st.markdown("### Escalation dashboard")
                st.dataframe(
                    _escalation_worklist,
                    width="stretch",
                    hide_index=True,
                )
            _entries = _dash.get("entries") or []
            st.markdown("#### Filtered audit entries")
            if _entries:
                st.dataframe(_entries, width="stretch", hide_index=True)
                _csv_cols = sorted({key for row in _entries for key in row.keys()})
                _csv_lines = [",".join(_csv_cols)]
                for _row in _entries:
                    _csv_lines.append(",".join(
                        json.dumps(_row.get(col, ""), ensure_ascii=False)
                        for col in _csv_cols
                    ))
                st.download_button(
                    "Export filtered audit results to CSV",
                    data="\n".join(_csv_lines),
                    file_name="filtered_audit_dashboard.csv",
                    mime="text/csv",
                )
            else:
                st.info("No audit entries match the selected filters.")

        audit_log_path = settings.processed_dir / "human_reviews.jsonl"
        if api_client.reads_must_use_backend():
            # Patient-data mode: the audit display is sourced FROM the backend
            # (durable sink, RBAC-enforced), not local JSONL.
            try:
                _events = api_client.audit_events(limit=1000)
                _ev_list = _events.get("events", []) if isinstance(_events, dict) else []
                st.caption(f"Audit source: backend durable sink "
                           f"({_events.get('source','durable') if isinstance(_events,dict) else 'durable'}).")
                st.metric("Audit events (from backend)", len(_ev_list))
                if _ev_list:
                    st.dataframe(_ev_list[-100:], width="stretch")
                if debug_ui_enabled():
                    with st.expander(f"Developer/debug: audit event payloads ({len(_ev_list)})", expanded=False):
                        st.json(_ev_list, expanded=False)
                _records = api_client.audit_records(limit=1000)
                if isinstance(_records, dict):
                    _runs_list = _records.get("workflow_runs", []) or []
                    _reviews_list = _records.get("human_reviews", []) or []
                    _reruns_list = _records.get("workflow_reruns", []) or []
                    r1, r2, r3 = st.columns(3)
                    r1.metric("Workflow-run records", len(_runs_list))
                    r2.metric("Review records", len(_reviews_list))
                    r3.metric("Edit-rerun records", len(_reruns_list))
                    if _runs_list:
                        with st.expander(f"Backend workflow-run records ({len(_runs_list)})", expanded=False):
                            st.dataframe(_runs_list[-50:], width="stretch")
                    if _reviews_list:
                        with st.expander(f"Backend review records ({len(_reviews_list)})", expanded=False):
                            st.dataframe(_reviews_list[-50:], width="stretch")
                    if _reruns_list:
                        with st.expander(f"Backend edit-rerun records ({len(_reruns_list)})", expanded=False):
                            st.dataframe(_reruns_list[-50:], width="stretch")
            except api_client.BackendError as exc:
                if exc.status_code in (401, 403):
                    st.error("Locked: The backend denied access to the audit log.")
                else:
                    st.error(f"Warning: Backend audit read failed (HTTP {exc.status_code}). "
                             "Audit is unavailable (local files are not an acceptable "
                             "patient-data audit source).")
            # Do NOT read local JSONL in patient-data mode.
            all_reviews = []
            runs = []
            reruns = []
        else:
            # Local JSONL is acceptable for display in non-sensitive mode.
            from app.storage.human_review_repository import read_human_reviews
            all_reviews = read_human_reviews(audit_log_path)
            from app.storage.workflow_run_repository import read_workflow_runs
            from app.storage.rerun_repository import read_reruns
            runs = read_workflow_runs(settings.processed_dir / "workflow_runs.jsonl")
            reruns = read_reruns(settings.processed_dir / "workflow_reruns.jsonl")
    
            wr1, wr2 = st.columns(2)
            wr1.metric("Workflow-run audit records", len(runs))
            wr2.metric("Edit-rerun audit records", len(reruns))
    
            if runs:
                with st.expander(f"Workflow-run audit log ({len(runs)} records)", expanded=False):
                    st.dataframe(
                        [
                            {
                                "Case UID": r.case_uid,
                                "Timestamp": r.timestamp_utc[:19],
                                "Scale": r.prediction_scale,
                                "Final category": r.final_category or "-",
                                "Override": r.override_tier or "no",
                                "Review status": r.human_review_status,
                            }
                            for r in runs[-50:]
                        ],
                        width="stretch",
                    )
            if reruns:
                with st.expander(f"Edit-rerun audit log ({len(reruns)} records)", expanded=False):
                    st.caption(
                        "Same patient re-run with edited vitals: previous -> new assignment."
                    )
                    st.dataframe(
                        [
                            {
                                "Case UID": r.case_uid,
                                "Timestamp": r.timestamp_utc[:19],
                                "Prev acuity": r.previous_final_acuity,
                                "New acuity": r.new_final_acuity,
                                "Movement": r.movement or "-",
                                "Changed vitals": ", ".join(v.field for v in r.changed_vitals) or "-",
                                "Reason": r.reason,
                            }
                            for r in reruns[-50:]
                        ],
                        width="stretch",
                    )
    
            st.markdown("---")
    
            # Readable full-MIMIC dataset summary from the backend.
            st.markdown("#### Dataset summary")
            with st.container(border=True):
                from frontend import api_client as _audit_api_client
                try:
                    _fm = _audit_api_client.full_mimic_status()
                except Exception:
                    _fm = {}
                st.markdown("**MIMIC-IV-ED Full** (credentialed - the only dataset)")
                st.write(f"MIMIC_FULL_ED_DIR: **{'configured' if _fm.get('mimic_full_dir_configured') else 'not configured'}**")
                st.write(f"MIMIC_FULL_MODEL_PATH: **{'configured' if _fm.get('mimic_full_model_configured') else 'not configured'}**")
                st.write("Tables: edstays, triage, vitalsign, diagnosis, medrecon, pyxis")
                st.write("Target: **acuity**")
                st.caption(
                    "Excluded leakage/outcome fields: acuity-as-input, disposition, "
                    "outtime, hadm_id, diagnoses, medication tables, full-stay vitals. "
                    "Full MIMIC is read only from MIMIC_FULL_ED_DIR on an approved "
                    "environment; it is never copied into this app."
                )
            st.caption(
                "Only full MIMIC-IV-ED is a prediction source; no other dataset is "
                "summarised here."
            )
    
            # The two reports below are sample logs regenerated by
            # scripts/run_synthetic_walkthrough.py
            # and scripts/run_triage_indicator_matrix.py respectively (see
            # KTAS_CHANGELOG.md for when each was last regenerated). This is a
            # deliberately small "make the existing output log visible" addition,
            # not the larger, separately-scoped interactive Scenario Walkthrough
            # tab a more thorough fix would eventually build (which would let a
            # user re-run a scenario live, edit its inputs, and see backend
            # endpoints for each -- that remains intentionally deferred).
            if api_client.reads_must_use_backend():
                walkthrough_log = None
                matrix_log = None
            else:
                walkthrough_log = load_json_file(settings.processed_dir / "synthetic_walkthrough_log.json")
                matrix_log = load_json_file(settings.processed_dir / "triage_indicator_matrix_log.json")
    
            if walkthrough_log:
                scenarios = walkthrough_log.get("scenarios", [])
                with st.expander(f"Walkthrough log ({len(scenarios)} scenarios)"):
                    st.caption(
                        "Generated by scripts/run_synthetic_walkthrough.py."
                    )
                    walk_rows = []
                    for s in scenarios:
                        outcome = (s.get("result") or s.get("comparison") or {})
                        workflow_action = outcome.get("workflow_action", "N/A")
                        status = outcome.get("classification_status") or outcome.get(
                            "new_classification_status", ""
                        )
                        walk_rows.append({
                            "Scenario": s.get("scenario", "-"),
                            "Engine status": status or "-",
                            "Workflow action": workflow_action,
                            "Clinician review required": "Yes",
                            "Note": s.get("note", outcome.get("escalation_note", "") or "-"),
                        })
                    st.dataframe(walk_rows, width="stretch")
                    if debug_ui_enabled():
                        with st.expander("Developer/debug: raw synthetic walkthrough JSON", expanded=False):
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
                    f"Triage Indicator Matrix - {mode_label} "
                    f"({len(results)} indicators)",
                    expanded=True,
                ):
                    st.caption(
                        "Generated by scripts/run_triage_indicator_matrix.py. One row "
                        "per Manchester-engine pathway/vital-threshold indicator."
                    )
                    if all_pass is True or (not failed and passed):
                        st.success(
                            f"Status: PASS - {len(passed)} of {len(results)} indicators "
                            f"matched expected {mode_label.split(' ')[0]} behaviour. Failed: {len(failed)}."
                        )
                    elif failed:
                        st.error(f"Status: FAIL - {len(failed)} of {len(results)} indicators did not match.")
                    else:
                        st.info(
                            f"Descriptive log ({len(results)} indicators) - this mode records "
                            "actual engine output rather than a pass/fail comparison."
                        )
                    # Readable table: Indicator | Expected | Actual | Result
                    table = [
                        {
                            "Indicator": r.get("indicator", "-"),
                            "Expected": r.get("expected_status_gated") or r.get("expected_status_provisional") or "-",
                            "Actual": r.get("actual_status", "-"),
                            "Result": "match match" if r.get(row_field) is True
                                      else ("mismatch mismatch" if r.get(row_field) is False else "-"),
                        }
                        for r in results
                    ]
                    st.dataframe(table, width="stretch")
                    if debug_ui_enabled():
                        with st.expander("Developer/debug: raw indicator matrix JSON", expanded=False):
                            st.json(matrix_log)
    
            st.markdown("---")
            if not all_reviews:
                st.info("No reviews logged yet. Submit a review from the Triage Review tab.")
            else:
                st.markdown(f"**Total reviews logged: {len(all_reviews)}**")
    
                audit_table = [
                    {
                        "Case UID": r.case_uid,
                        # source_dataset is Optional and may be None for any
                        # review record saved before this field existed (see
                        # app/schemas/review.py) -- rendered explicitly as
                        # "Unknown (pre-dataset-tracking)" rather than a blank
                        # cell, so it reads as a deliberate fact about that
                        # record's age, not a rendering bug.
                        "Source Dataset": r.source_dataset or "Unknown (pre-dataset-tracking)",
                        "Reviewer Role": r.reviewer_role or (r.reviewer_roles[0] if r.reviewer_roles else "reviewer"),
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
                        f"Case {review.case_uid} "
                        f"({review.source_dataset or 'Unknown (pre-dataset-tracking)'}) - "
                        f"{review.review_status} - "
                        f"{review.reviewer_role or (review.reviewer_roles[0] if review.reviewer_roles else 'reviewer')} - {review.created_at_utc[:19]}"
                    ):
                        st.write(f"Decision: {review.review_status}")
                        st.write(f"Reviewer: {review.reviewer_role or (review.reviewer_roles[0] if review.reviewer_roles else 'reviewer')}")
                        if review.review_comment:
                            st.write(f"Notes: {review.review_comment}")
                        if review.clinician_override:
                            st.write(f"Override: {review.clinician_override}")
                        if debug_ui_enabled():
                            st.json(review.model_dump(mode="json"))
    
    
        # ---------------------------------------------------------------------------
        # TAB 7 - MODEL PERFORMANCE
        # ---------------------------------------------------------------------------
if tab_models is not None:
    with tab_models:
        st.subheader(" ML Model Performance - Full MIMIC-IV-ED only")
    
        from frontend import api_client
        try:
            _perf = api_client.model_performance()
        except api_client.BackendError as exc:
            if exc.status_code in (401, 403):
                st.info(_local_role_restart_hint(target="model performance"))
            else:
                st.warning(
                    f"Model performance unavailable from backend (HTTP {exc.status_code})."
                )
            _perf = {}
    
        c1, c2, c3 = st.columns(3)
        c1.metric("Full MIMIC data", "loadable" if _perf.get("data_loadable") else "not loadable")
        c2.metric("Model artefact", "present" if _perf.get("model_file_exists") else "not present")
        c3.metric("Report artefacts", _perf.get("status", "unknown"))
        st.table({
            "Component": [
                "Credentialed data directory",
                "Runtime model artefact",
                "Aggregate report artefacts",
                "Model/report provenance",
            ],
            "Status": [
                "loadable" if _perf.get("data_loadable") else "not loadable",
                "present and hash-pinned" if _perf.get("model_file_exists") and _perf.get("model_hash_configured")
                else "present but hash not configured" if _perf.get("model_file_exists")
                else "not configured",
                _perf.get("status", "unknown"),
                _perf.get("model_provenance_status", "unknown"),
            ],
        })
        if _perf.get("stale_report_detected"):
            st.error("Model/report provenance is stale, incomplete, or unpinned.")
            for issue in _perf.get("model_provenance_issues", []):
                st.warning(issue)
        if not _perf.get("model_file_exists"):
            st.info(
                "No full-MIMIC model is configured in the backend environment, so live "
                "model status remains fail-closed. Train/compare via "
                "`ml_training/full_mimic/compare_models.py` and set "
                "MIMIC_FULL_MODEL_PATH after review."
            )
    
        _artefacts = _perf.get("artefacts") or {}
        _comparison = _artefacts.get("model_comparison")
        if isinstance(_comparison, dict) and _comparison:
            st.markdown("### Full-MIMIC model comparison (safety-first selection)")
            st.caption(_comparison.get("selection_criterion", ""))
            st.markdown(f"**Selected model:** `{_comparison.get('selected_model')}` "
                        f"(by triage-safety metrics, not accuracy)")
            class_distribution = _comparison.get("class_distribution") or {}
            if isinstance(class_distribution, dict) and class_distribution:
                dist_rows = []
                for split_name in ("overall", "train", "validation", "test"):
                    split_payload = class_distribution.get(split_name) or {}
                    dist_rows.append({
                        "split": split_name,
                        "total": split_payload.get("total"),
                        "acuity_1": (split_payload.get("class_counts") or {}).get("1"),
                        "acuity_2": (split_payload.get("class_counts") or {}).get("2"),
                        "acuity_3": (split_payload.get("class_counts") or {}).get("3"),
                        "acuity_4": (split_payload.get("class_counts") or {}).get("4"),
                        "acuity_5": (split_payload.get("class_counts") or {}).get("5"),
                        "high_acuity_1_2_pct": split_payload.get(
                            "high_acuity_1_2_percentage"
                        ),
                        "minority_to_majority_ratio": split_payload.get(
                            "minority_to_majority_ratio"
                        ),
                    })
                with st.expander("Class distribution by split", expanded=False):
                    st.dataframe(dist_rows, hide_index=True)
            comparison_candidates = _comparison.get("candidates", []) or []
            rows = []
            for cand in comparison_candidates:
                har = (cand.get("high_acuity_recall") or {}).get("recall")
                uot_payload = cand.get("under_over_triage") or {}
                ordinal_payload = cand.get("ordinal_metrics") or {}
                over_payload = cand.get("over_triage_specificity") or {}
                auc_payload = cand.get("auroc_pr_auc") or {}
                rows.append({
                    "model": cand.get("model_name"),
                    "imbalance_strategy": cand.get("imbalance_strategy"),
                    "high_acuity_recall": har,
                    "high_acuity_precision": over_payload.get("high_acuity_precision"),
                    "specificity": over_payload.get("specificity"),
                    "predicted_urgent_rate": over_payload.get("predicted_urgent_rate"),
                    "severe_under_triage_rate": uot_payload.get("severe_under_triage_rate"),
                    "under_triage_rate": uot_payload.get("under_triage_rate"),
                    "over_triage_rate": uot_payload.get("over_triage_rate"),
                    "AUROC": auc_payload.get("auroc"),
                    "PR_AUC": auc_payload.get("pr_auc"),
                    "MAE": ordinal_payload.get("mae"),
                    "quadratic_weighted_kappa": ordinal_payload.get("quadratic_weighted_kappa"),
                    "within_1_acuity_level_accuracy": ordinal_payload.get(
                        "within_1_acuity_level_accuracy"
                    ),
                    "accuracy": cand.get("accuracy"),
                    "macro_f1": cand.get("macro_f1"),
                    "weighted_f1": cand.get("weighted_f1"),
                })
            if rows:
                st.dataframe(rows, hide_index=True)
                _render_recall_by_triage_category(comparison_candidates)
                _render_precision_recall_scatter(comparison_candidates)
            test_metrics = _comparison.get("untouched_test_metrics") or {}
            selected_name = (
                test_metrics.get("model")
                or _comparison.get("selected_model")
                or _comparison.get("model")
            )
            selected_candidate = next(
                (
                    cand for cand in comparison_candidates
                    if cand.get("model_name") == selected_name
                ),
                {},
            )
            cm_labels = (
                test_metrics.get("confusion_matrix_labels")
                or (_artefacts.get("confusion_matrix") or {}).get("labels")
                or selected_candidate.get("confusion_matrix_labels")
                or _comparison.get("labels")
                or []
            )
            cm_matrix = (
                test_metrics.get("confusion_matrix")
                or (_artefacts.get("confusion_matrix") or {}).get("confusion_matrix")
                or selected_candidate.get("confusion_matrix")
                or []
            )
            _render_confusion_matrix([str(x) for x in cm_labels], cm_matrix)
            _render_binary_curves(
                _artefacts.get("roc_curve"),
                _artefacts.get("pr_curve"),
            )
        else:
            st.markdown("### Full-MIMIC model comparison")
            st.info(
                "Model visualisations unavailable because full-MIMIC model comparison "
                "has not been generated yet."
            )
            st.caption(_perf.get("reason") or "No comparison report is available from the backend.")
    
        for _label, _key in (("Full-MIMIC model card", "model_card"),
                             ("Full-MIMIC dataset card", "dataset_card")):
            if _key in _artefacts:
                with st.expander(_label, expanded=False):
                    _card = _artefacts[_key]
                    if isinstance(_card, dict):
                        st.table({
                            "Field": list(_card.keys()),
                            "Value": [str(v) for v in _card.values()],
                        })
                    else:
                        st.write(_card)
                    if debug_ui_enabled():
                        with st.expander("Developer/debug: raw artefact JSON", expanded=False):
                            st.json(_card, expanded=False)
    
        presence = _perf.get("report_artefacts_present") or {}
        if presence:
            st.table({"Artefact": list(presence.keys()),
                      "Present": [str(v) for v in presence.values()]})
    
        st.markdown("---")
    
    
if tab_explainability is not None:
    with tab_explainability:
        st.subheader("Explainability")
        from frontend import api_client
        if _has_perm(PERM_VIEW_MODEL_PERFORMANCE):
            try:
                _perf = api_client.model_performance()
            except api_client.BackendError:
                _perf = {}
        else:
            _perf = {}
        _artefacts = _perf.get("artefacts") or {}
        _selected_fi = _artefacts.get("selected_feature_importance") or {}
        _all_fi = _artefacts.get("feature_importance") or {}
        if isinstance(_selected_fi, dict) and _selected_fi:
            st.markdown("### Selected model decision drivers")
            st.write(_selected_fi.get("reason") or _selected_fi.get("status") or "available")
            _top = _selected_fi.get("top_features") or []
            _logical = _selected_fi.get("top_logical_fields") or []
            if _top:
                st.dataframe(_top[:25], width="stretch", hide_index=True)
            elif _selected_fi.get("status") in {"not_directly_interpretable", "unavailable"}:
                st.info(_selected_fi.get("reason") or "Feature importance is not available for this model.")
            if _logical:
                with st.expander("Top logical clinical fields", expanded=True):
                    st.dataframe(_logical[:25], width="stretch", hide_index=True)
        else:
            st.info("Selected-model feature importance has not been generated yet.")
        if isinstance(_all_fi, dict) and (_all_fi.get("reports") or {}):
            st.markdown("### Per-model feature importance availability")
            _rows = []
            for _name, _report in (_all_fi.get("reports") or {}).items():
                _rows.append({
                    "model": _name,
                    "status": _report.get("status"),
                    "method": _report.get("method"),
                    "reason": _report.get("reason"),
                })
            st.dataframe(_rows, width="stretch", hide_index=True)
        st.markdown("### Case explanation")
        if _has_perm(PERM_VIEW_CASE):
            _ex_case = render_backend_case_selector("explainability", label="Case", show_label=True)
            if _ex_case is not None:
                render_triage_input_summary(_ex_case, key_prefix=f"explainability_{_ex_case['case_uid']}")
                if _has_perm(PERM_RUN_ASSESSMENT):
                    if st.button("Run assessment for explainability", key=f"explainability_assess_{_ex_case['case_uid']}"):
                        try:
                            st.session_state[f"explainability_assessment::{_ex_case['case_uid']}"] = api_client.run_assessment(_ex_case["case_uid"])
                        except api_client.BackendError as exc:
                            st.warning(f"Assessment unavailable (HTTP {exc.status_code}).")
                    _assessment = st.session_state.get(f"explainability_assessment::{_ex_case['case_uid']}")
                    if _assessment:
                        render_assessment_summary(_assessment, _ex_case.get("source_dataset", ""))
        st.caption(
            "Explanation layer is bounded to already-computed evidence. It must not assign triage, diagnose, or recommend treatment."
        )



if tab_maintainability is not None:
    with tab_maintainability:
        st.subheader("Maintainability")
        from frontend import api_client
        if _has_perm(PERM_VIEW_MODEL_PERFORMANCE) or _has_perm(PERM_VIEW_SECURITY_STATUS):
            st.markdown("### Operational Evidence")
            try:
                _perf = api_client.model_performance()
            except api_client.BackendError:
                _perf = {}
            _presence = _perf.get("report_artefacts_present") or {}
            if _presence:
                st.dataframe(
                    [{"artefact": k, "present": v} for k, v in _presence.items()],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("Model artefact/report status is not available.")
        else:
            st.caption("Clinical reassessment tools are shown above. Model/retraining artefact evidence is hidden for this role.")
        st.markdown("### Future capability holding area")
        st.write("Additional-information workflow: available above in this Maintainability section.")
        if _has_perm(PERM_RUN_ASSESSMENT):
            st.write("Scan/image upload: metadata stored only; analysis remains pending unless an approved multimodal model is configured.")
        if _has_perm(PERM_VIEW_MODEL_PERFORMANCE):
            st.write("Retraining evidence: use `sbatch train_full_mimic.slurm` for the final imbalance/SMOTE run.")
            st.write("Feature importance generation: written during model comparison when supported.")
        if _has_perm(PERM_VIEW_SECURITY_STATUS):
            st.write("Optional plugins/tooling and advanced system diagnostics are ITD-controlled.")
        if _has_perm(PERM_VIEW_AUDIT_LOG):
            st.caption("Audit dashboard is available from the Audit Dashboard tab.")


if tab_itd_ask is not None:
    with tab_itd_ask:
        st.subheader("ITD System Assistant")
        if not _has_perm(PERM_ASK_CHATBOT):
            st.info("This role cannot access Ask tools.")
        else:
            from frontend import api_client
            st.caption(
                "Read-only system/security/governance assistant. It does not "
                "inspect individual patients or answer triage questions."
            )
            _itd_q = st.text_area(
                "Ask about system status, security, audit, governance, or model artefacts",
                key="itd_system_assistant_q",
                height=110,
                placeholder="Example: Is the model artefact configured and are governance reports available-",
            )
            if st.button("Ask ITD assistant", key="itd_system_assistant_btn", type="primary"):
                try:
                    _answer = api_client.system_assistant(_itd_q)
                    st.write(_answer.get("answer") or "(no answer returned)")
                    if debug_ui_enabled() and _answer.get("evidence"):
                        with st.expander("Developer/debug: system evidence", expanded=False):
                            st.json(_answer.get("evidence"), expanded=False)
                except api_client.BackendError as exc:
                    st.warning(f"ITD assistant unavailable (HTTP {exc.status_code}).")
        st.caption("ITD assistant is read-only and cannot assign, explain, or alter patient triage.")


with st.sidebar:
    if _has_perm(PERM_VIEW_SECURITY_STATUS):
        st.markdown("### System Status")
        from frontend import api_client

        _backend_status = True
        _runtime = {}
        try:
            _runtime = api_client.runtime_status()
            _fm = _runtime.get("mimic_full") or {}
            _model_runtime = _runtime.get("model") or {}
            _reports_runtime = _runtime.get("reports") or {}
        except Exception:
            _backend_status = False
            _fm = {
                "state": "backend unavailable",
                "loadable": False,
                "reason": (
                    "Backend unavailable. Full-MIMIC status cannot be displayed "
                    "from the frontend in a sensitive mode."
                ),
            }
            _model_runtime = {"state": "backend unavailable"}
            _reports_runtime = {"env_present": False}

        st.markdown(f"**MIMIC-IV-ED Full:** {_fm.get('state', 'unknown')}")
        st.markdown(f"**Full MIMIC model:** {_model_runtime.get('state', 'unknown')}")
        _reports_state = "configured" if _reports_runtime.get("env_present") else "not configured"
        st.markdown(f"**Model reports:** {_reports_state}")
        if not presentation_ui_mode():
            _active_profile = _runtime.get("active_profile") or _fm.get("active_profile", "-")
            st.caption(
                f"Active profile: `{_active_profile}`"
                + ("" if _backend_status else " (backend unreachable)")
            )

        st.markdown("---")
        with st.expander("Security status", expanded=False):
            try:
                _ss = api_client.security_status()
                _mode_label = {
                    "secured_research": "Secured research",
                    "local_credentialed_research": "Local credentialed research",
                    "azure_supervisor_demo": "Supervisor review",
                    "public_demo": "Public review",
                }.get(_ss.get("current_mode"), str(_ss.get("current_mode", "unknown")))
                st.markdown(f"**Mode:** {_mode_label}")
                _rows = {
                    "Patient-data mode": _ss["patient_data_mode"],
                    "Auth required": _ss["auth_required"],
                    "Auth provider": _ss["auth_provider"],
                    "Trusted auth proxy": _ss["trusted_auth_proxy"],
                    "Secrets provider": _ss["secrets_provider"],
                    "Audit sink": _ss["audit_sink"],
                    "Key Vault configured": _ss["key_vault_configured"],
                    "Durable audit configured": _ss["durable_audit_configured"],
                    "Role switcher": _ss["demo_role_switcher_enabled"],
                    "Full MIMIC configured": _ss["full_mimic_configured"],
                    "CORS wildcard": _ss["cors_is_wildcard"],
                }
                st.table({"Setting": list(_rows.keys()),
                          "Value": [str(v) for v in _rows.values()]})
                if _ss["unsafe_combinations"]:
                    st.error("Warning: Unsafe configuration:\n" +
                             "\n".join(f"- {p}" for p in _ss["unsafe_combinations"]))
                else:
                    st.success("OK No unsafe configuration detected for the current mode.")
            except api_client.BackendError as _exc:
                if _exc.status_code in (401, 403):
                    st.caption(_local_role_restart_hint(target="security status"))
                else:
                    st.caption(f"Security status unavailable from backend (HTTP {_exc.status_code}).")
            except Exception:
                st.caption("Security status unavailable.")

        st.markdown("---")
        with st.expander("Quick Start (ITD only)", expanded=False):
            st.code(
                """# 1. Point at the credentialed full MIMIC-IV-ED 'ed' directory (outside the repo)
export MIMIC_FULL_ED_DIR=/path/to/mimic-iv-ed/ed
export MIMIC_FULL_MODEL_PATH=/path/to/mimic_full_acuity_model.joblib

# 2. Run tests
pytest

# 3. Run the API (separate terminal)
uvicorn app.main:app --reload

# 4. Run this UI (separate terminal), pointing at the backend
export FASTAPI_BASE_URL=http://127.0.0.1:8000
streamlit run frontend/app.py
""",
                language="bash",
            )
