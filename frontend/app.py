"""
AI Triage Agentic Workflow — Streamlit UI (full MIMIC-IV-ED only; backend-driven).

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
  1. Triage Review     — select a case (from the backend), run the assessment
                          server-side, view the result + safety flags, submit a
                          clinician review.
  2. Follow-Up          — reassess a case after its triage-time vitals change,
                          server-side, via the backend.
  3. Governance         — responsible-AI review gate + executable policy checks.
  4. Review Queue       — backend-driven, full-MIMIC-only review queue.
  5. Audit Log          — clinician review / workflow audit history.
  6. Model Performance  — full-MIMIC model status and (if produced on the
                          approved environment) the safety-first comparison.

NOT FOR CLINICAL USE. Research prototype only. Every output requires clinician
confirmation. Provisional Manchester-style categories are research-only and are
not the official Manchester Triage System.
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


# ── Helper functions ──────────────────────────────────────────────────────────

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
        placeholder="Search by case ID or chief complaint",
        label_visibility="collapsed",
    )
    try:
        resp = api_client.list_cases(
            limit=CASE_SELECTOR_LIMIT,
            offset=0,
            search=search.strip() or None,
        )
        cases = resp.get("cases", []) if isinstance(resp, dict) else []
        pagination = resp.get("pagination", {}) if isinstance(resp, dict) else {}
    except api_client.BackendError as exc:
        st.error(f"🔒 Could not load cases from the backend (HTTP {exc.status_code}).")
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
            "Search scanned a bounded local/demo window. Refine the query if the "
            "case is not listed."
        )
    elif pagination.get("has_more"):
        st.caption("Type in the search box to narrow the matching cases.")
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


def render_triage_input_summary(record: dict, *, key_prefix: str) -> None:
    """Render the redacted backend case DTO as user-facing triage-time fields."""
    triage = record.get("triage") or {}
    demographics = record.get("demographics") or {}
    if record.get("is_synthetic_demo"):
        st.info(record.get("demo_data_notice") or "Synthetic demo case. Not real patient data.")
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


st.title("🏥 AI Triage Agentic Workflow")
st.caption(
    "Research decision-support for ED acuity. The only prediction/training "
    "dataset is full MIMIC-IV-ED (credentialed), read from MIMIC_FULL_ED_DIR on "
    "an approved environment; the trained model is read from MIMIC_FULL_MODEL_PATH. "
    "Without those, the app serves no cases and makes no predictions (it fails "
    "closed). Provisional Manchester-style display categories are research-only, "
    "not the official Manchester Triage System, not clinically approved, and "
    "require clinician review on every output. Not for clinical use."
)

# ═══════════════════════════════════════════════════════════════════════════
# SECURITY: establish the authenticated identity (Phase 1) BEFORE the tabs, so
# pages/actions can be gated by role. Real authentication (Entra SSO / MFA /
# conditional access) and network controls (hospital-managed device, VPN /
# private network, Azure Container Apps internal ingress) are provided by the
# HOSPITAL in front of this app; the app reads the verified identity and
# enforces app-level RBAC on top. Fails closed in patient-data mode.
# ═══════════════════════════════════════════════════════════════════════════
from frontend import api_client as _session_api_client

PERM_VIEW_CASE = "can_view_case"
PERM_RUN_ASSESSMENT = "can_run_assessment"
PERM_SUBMIT_REVIEW = "can_submit_review"
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
    st.markdown("### 🔐 Identity & Access")
    if _demo_mode:
        st.caption(
            auth_session.get(
                "demo_role_switcher_label",
                "Demo role selector - not real authentication",
            )
        )
        st.warning(
            "**DEMO ONLY — simulated identity for RBAC demonstration.** "
            "Real deployment uses hospital SSO / Microsoft Entra authentication. "
            "Disabled in patient-data and local credentialed research modes "
            "and behind trusted real-authentication proxies."
        )
        _role_choice = st.selectbox(
            "Simulated role (demo)",
            auth_session.get("all_roles") or ["triage_nurse"],
            index=(auth_session.get("all_roles") or ["triage_nurse"]).index("triage_nurse")
            if "triage_nurse" in (auth_session.get("all_roles") or ["triage_nurse"])
            else 0,
            key="demo_role",
        )
        # Refresh only when the selected demo role differs from the backend's
        # current session view. This keeps the role switcher accurate without
        # making a duplicate session call on every rerun.
        _current_demo_role = (auth_session.get("roles") or [None])[0]
        if _role_choice != _current_demo_role:
            auth_session = _load_auth_session()
        _session_api_client.ui_access(
            None,
            "demo_identity_session",
            "sidebar_identity",
            detail=f"Demo simulated role: {_role_choice}",
        )
        st.caption(
            f"Acting as **{_role_choice}** (simulated). Not real authentication. "
            f"{auth_session.get('demo_role_switcher_reason', '')}"
        )
    else:
        if not auth_session.get("authenticated"):
            st.error(
                "🔒 **Access denied — no verified identity.** This instance "
                "requires authentication via the hospital identity provider "
                "(Entra SSO) behind a trusted proxy. No demo identity is "
                "available in patient-data mode."
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
                f"Roles: {', '.join(auth_session.get('roles') or []) or 'none'} "
                f"source: {auth_session.get('source')}"
            )
            if auth_session.get("current_mode") == "local_credentialed_research":
                st.info(
                    "Role switching disabled in local credentialed research mode. "
                    "To change role, set LOCAL_RESEARCH_ROLE and restart the backend."
                )
                if auth_session.get("local_role_change_instruction"):
                    st.caption(auth_session["local_role_change_instruction"])

    # Show the current effective permissions (transparency for the demo).
    with st.expander("Your permissions", expanded=False):
        perms = sorted(auth_session.get("permissions") or [])
        st.write(perms if perms else "No permissions (unauthenticated or unmapped role).")

if auth_session.get("azure_supervisor_demo_mode"):
    try:
        _runtime_banner = _session_api_client.runtime_status()
        _banner_mimic = _runtime_banner.get("mimic_full") or {}
    except Exception:
        _banner_mimic = {}
    if _banner_mimic.get("full_mimic_requested_for_azure_demo"):
        if _banner_mimic.get("loadable"):
            st.warning(
                "Credentialed MIMIC-IV-ED demo mode. Not synthetic. Not UHL patient "
                "data. Not hospital SSO. Clinical use not allowed."
            )
        else:
            st.error(
                "Full MIMIC requested for Azure demo, but MIMIC_FULL_ED_DIR is not "
                "readable by the backend."
            )
    else:
        st.warning(
            "Synthetic supervisor demo data only - not real MIMIC, not real patient data."
        )


def _has_perm(permission: str) -> bool:
    return permission in set(auth_session.get("permissions") or [])


def _role_text() -> str:
    return ", ".join(auth_session.get("roles") or []) or "none"


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
            "Switch to an authorised role in demo mode, or sign in with a role "
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
        f"🔒 **Access denied.** Your role(s) "
        f"({_role_text()}) cannot access **{page_name}**. "
        f"Requires the `{permission}` permission."
    )
    return False



def _workflow_action_badge(action: str) -> str:
    if action == "ESCALATION_REQUIRED":
        return f"🔴 {action}"
    if action == "CLINICIAN_INTERVENTION_REQUIRED":
        return f"🟡 {action}"
    return f"🟢 {action}"



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
def _render_triage_review_submission(case_uid: str, source_dataset: str, key_prefix: str = "triage"):
    """Backend-driven clinician review submission for a case_uid. Identity comes
    from the authenticated context (no role dropdown in patient-data mode); the
    write goes through POST /cases/{case_uid}/reviews (RBAC + guarded, fail-closed).
    """
    from frontend import api_client
    if not _has_perm(PERM_SUBMIT_REVIEW):
        st.caption("🔒 You do not have permission to submit a clinician review.")
        return
    with st.form(f"review_form_{key_prefix}_{case_uid}"):
        st.markdown("**Submit clinician review**")
        c1, c2 = st.columns(2)
        if _session_api_client.reads_must_use_backend():
            _roles = _role_text() or "(no mapped role)"
            c1.text_input("Reviewer (from sign-in)", value=_roles, disabled=True,
                          key=f"role_ro_{key_prefix}_{case_uid}")
        else:
            c1.selectbox("Reviewer role (demo)",
                         ["triage_nurse", "emergency_physician", "researcher", "supervisor"],
                         key=f"role_{key_prefix}_{case_uid}")
        review_status = c2.selectbox(
            "Review decision",
            ["REQUEST_MORE_INFORMATION", "NOT_REVIEWED", "ACCEPTED_AS_PRESENTED",
             "OVERRIDE_REQUIRED", "ESCALATION_REQUIRED", "REJECTED_DATA_QUALITY"],
            key=f"status_{key_prefix}_{case_uid}")
        review_comment = st.text_area(
            "Review notes", value="Clinician review required before any action.",
            key=f"comment_{key_prefix}_{case_uid}")
        if st.form_submit_button("💾 Save Review to Audit Log"):
            try:
                api_client.submit_review(case_uid, {
                    "review_status": review_status,
                    "review_comment": review_comment,
                })
                st.success("✅ Review saved to audit log (via backend).")
            except api_client.BackendError as exc:
                if exc.status_code in (401, 403):
                    st.error("🔒 The backend denied this review (insufficient permission).")
                elif exc.status_code == 422:
                    st.warning("A reason is required for an override / uncertain decision.")
                else:
                    st.warning(f"⚠️ Backend could not save the review (HTTP {exc.status_code}).")


with tab_triage:
    st.subheader("Select ED Stay for Review")

    selected_record = render_backend_case_selector("triage_review")

    if selected_record is None:
        # No cases available (no credentialed data in this environment). Render
        # nothing further in THIS tab, but do NOT st.stop() — that would also
        # prevent the other tabs (Follow-up, Governance, Queue, Audit, Models)
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
            st.info("🔒 You do not have permission to run an assessment.")
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
                        st.error("🔒 The backend denied this action (insufficient permission).")
                    else:
                        st.warning(f"Backend could not run the assessment (HTTP {exc.status_code}).")
            if _assessment_key in st.session_state:
                render_assessment_summary(st.session_state[_assessment_key], _src)

        # Explanation (LLM) — also server-side, gated by permission.
        if _has_perm(PERM_ASK_CHATBOT):
            st.markdown("**Multi-agent explanation**")
            st.caption(
                "Agents read the already-computed research evidence. They do not "
                "assign triage or change the final output."
            )
            if st.button(
                "Run multi-agent explanation",
                key=f"multiagent_explain_btn_{_case_uid}",
                type="primary",
                use_container_width=True,
            ):
                try:
                    _mx = api_client.multiagent_explain_case(_case_uid, None)
                    _status = str(_mx.get("status") or "UNKNOWN")
                    with st.chat_message("assistant"):
                        st.write(
                            _mx.get("final_explanation")
                            or "(no multi-agent explanation returned)"
                        )
                    if _status != "PASS":
                        st.caption(f"Multi-agent status: {_status}")
                    if debug_ui_enabled() and _mx.get("agent_turns"):
                        with st.expander("Developer/debug: agent turns", expanded=False):
                            st.json(_mx.get("agent_turns"), expanded=False)
                    st.caption(
                        "AutoGen explanation only - agents read verified evidence "
                        "and cannot assign or alter triage."
                    )
                except api_client.BackendError as exc:
                    st.warning(
                        f"Multi-agent explanation unavailable (HTTP {exc.status_code})."
                    )

            st.markdown("**Ask question**")
            st.caption(
                "Ask for a focused explanation of the evidence. The LLM does not "
                "assign triage or change the final output."
            )
            _q_key = f"explain_q_{_case_uid}"
            _quick_questions = [
                ("Why this acuity?", "Why was this research acuity estimated?"),
                ("What is missing?", "Which missing or abnormal inputs matter most?"),
                ("Review focus", "What should a clinician review before accepting this output?"),
            ]
            _q_cols = st.columns(len(_quick_questions))
            for _idx, (_label, _question) in enumerate(_quick_questions):
                if _q_cols[_idx].button(_label, key=f"explain_quick_{_idx}_{_case_uid}"):
                    st.session_state[_q_key] = _question
            _q = st.text_area(
                "Ask a case question",
                key=_q_key,
                placeholder="Optional question for the explanation layer",
                height=90,
            )
            if st.button(
                "Run LLM explanation",
                key=f"explain_btn_{_case_uid}",
                type="primary",
                use_container_width=True,
            ):
                try:
                    _ex = api_client.explain_case(_case_uid, _q or None)
                    with st.chat_message("assistant"):
                        st.write(_ex.get("explanation") or "(no explanation returned)")
                    st.caption("Explanation only — the LLM does not assign or "
                               "alter triage. Clinician review required.")
                except api_client.BackendError as exc:
                    st.warning(f"Explanation unavailable (HTTP {exc.status_code}).")

        # Review submission — server-side; identity from auth (no role dropdown in
        # patient-data mode). Handled by the shared review form below.
        _render_triage_review_submission(_case_uid, _src, key_prefix="triage")

# The legacy rich local-workflow card and its helpers below are retained only for
# the demo follow-up/queue tabs and are not used by the backend-driven Triage tab.
with tab_followup:
    st.subheader("🔄 Follow-Up Reassessment")
    st.caption(
        "Reassess a case after its triage-time vitals change. The reassessment "
        "runs server-side via the backend (POST /cases/{case_uid}/followups); the "
        "result is research-only and requires clinician review. There is no "
        "raw-identifier two-visit linking and no local workflow execution."
    )
    if not _has_perm(PERM_RUN_ASSESSMENT):
        st.info("🔒 You do not have permission to run a reassessment.")
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
            _fu_result_key = f"followup_result::{_fu_uid}"
            _fu_updated_key = f"followup_updated::{_fu_uid}"
            _fu_explain_key = f"followup_explanation::{_fu_uid}"
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
                if _invalid_vitals:
                    st.warning(
                        "These updated vitals must be numeric: "
                        + ", ".join(_invalid_vitals)
                    )
                elif not updated:
                    st.warning("Enter at least one updated vital.")
                else:
                    from frontend import api_client
                    try:
                        _r = api_client.followup_case(_fu_uid, updated)
                        st.session_state[_fu_result_key] = _r
                        st.session_state[_fu_updated_key] = dict(updated)
                        st.session_state.pop(_fu_explain_key, None)
                        st.success("Reassessment computed and stored for comparison.")
                    except api_client.BackendError as exc:
                        if exc.status_code in (401, 403):
                            st.error("🔒 The backend denied this reassessment.")
                        else:
                            st.warning(f"⚠️ Reassessment unavailable (HTTP {exc.status_code}).")

            _fu_result = st.session_state.get(_fu_result_key)
            if _fu_result:
                st.markdown("**Follow-Up Comparison**")
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
                elif _chg == "de-escalation":
                    st.success(_summary or "De-escalation detected.")
                else:
                    st.info(_summary or "No change in predicted acuity.")
                st.caption(
                    f"Changed fields: {_fu_result.get('changed_fields')}. "
                    "Clinician review required. Research only."
                )

                _updated_for_explain = st.session_state.get(_fu_updated_key) or {}
                if _has_perm(PERM_ASK_CHATBOT):
                    from frontend import api_client
                    st.markdown("**Multi-agent explanation**")
                    st.caption(
                        "Explains the reassessment movement using previous and "
                        "updated triage-time evidence only."
                    )
                    if st.button(
                        "Run multi-agent explanation",
                        key=f"fu_multiagent_explain_btn_{_fu_uid}",
                        type="primary",
                        use_container_width=True,
                    ):
                        try:
                            _mx = api_client.followup_multiagent_explain_case(
                                _fu_uid, _updated_for_explain, None)
                            st.session_state[_fu_explain_key] = _mx
                        except api_client.BackendError as exc:
                            st.warning(
                                f"Follow-up multi-agent explanation unavailable "
                                f"(HTTP {exc.status_code})."
                            )

                    st.markdown("**Ask question**")
                    _fu_q_key = f"followup_q_{_fu_uid}"
                    _fu_q = st.text_area(
                        "Ask a follow-up question",
                        key=_fu_q_key,
                        placeholder="Why did the acuity change, or why did it stay the same?",
                        height=90,
                    )
                    if st.button(
                        "Ask question",
                        key=f"fu_ask_btn_{_fu_uid}",
                        type="primary",
                        use_container_width=True,
                    ):
                        try:
                            _mx = api_client.followup_multiagent_explain_case(
                                _fu_uid, _updated_for_explain, _fu_q or None)
                            st.session_state[_fu_explain_key] = _mx
                        except api_client.BackendError as exc:
                            st.warning(
                                f"Follow-up question unavailable "
                                f"(HTTP {exc.status_code})."
                            )
                    if st.session_state.get(_fu_explain_key):
                        _mx = st.session_state[_fu_explain_key]
                        with st.chat_message("assistant"):
                            st.write(
                                _mx.get("final_explanation")
                                or "(no follow-up explanation returned)"
                            )
                        if str(_mx.get("status") or "UNKNOWN") != "PASS":
                            st.caption(f"Multi-agent status: {_mx.get('status')}")
                else:
                    st.caption("You do not have permission to ask follow-up questions.")

with tab_governance:
    st.subheader("🔒 Responsible AI Governance Dashboard")
    st.caption(
        "Five-stage review gate. This is evidence for review — not a clinical certification."
    )

    from frontend import api_client as _governance_api_client
    try:
        gov = _governance_api_client.governance_report()
    except _governance_api_client.BackendError as exc:
        gov = {}
        st.warning(f"Governance report unavailable from backend (HTTP {exc.status_code}).")
    if not isinstance(gov, dict):
        gov = {}
        st.warning("Governance report returned an unexpected shape from the backend.")

    verdict = gov.get("governance_verdict", "UNKNOWN")
    if gov.get("clinical_use_status") == "not_for_clinical_use":
        st.error(f"Clinical-use readiness: {verdict}")
    else:
        st.warning(f"Governance verdict: {verdict}")

    st.markdown("**Blocking Issues:**")
    for issue in gov.get("blocking_issues", []) or ["Governance report not available."]:
        st.error(f"- {issue}")

    st.markdown("---")
    st.markdown("### Backend Governance Controls")
    controls = gov.get("controls", {}) if isinstance(gov, dict) else {}
    for control_name, control in controls.items():
        status = control.get("status", "UNKNOWN")
        evidence = control.get("evidence", "")
        if status == "PASS":
            st.success(f"✅ **{control_name}**")
        elif status in ("ACTIVE", "WARNING", "PARTIAL", "NOT_AVAILABLE"):
            st.warning(f"⚠️ **{control_name}** ({status})")
        else:
            st.info(f"ℹ️ **{control_name}** ({status})")
        with st.expander(f"Evidence: {control_name}", expanded=False):
            if isinstance(evidence, dict):
                st.table({"Field": list(evidence.keys()),
                          "Value": [str(v) for v in evidence.values()]})
            elif isinstance(evidence, list):
                for item in evidence:
                    st.markdown(f"- {item}")
            else:
                st.write(evidence)

    gate = gov.get("responsible_ai_review_gate") or {}
    if gate:
        st.markdown("---")
        st.markdown("### Five-Stage Review Gate")
        for key, value in gate.items():
            with st.expander(key.replace("_", " ").title(), expanded=False):
                st.write(value)

    # ── Policy-as-code checks (executable governance) ───────────────────────
    st.markdown("---")
    st.markdown("### Policy-as-code checks (executable)")
    st.caption(
        "These run real safety invariants against live cases/config — not status "
        "labels. They can fail in CI. Research/demo scope; a full external "
        "red-team remains a separate, larger effort."
    )
    if st.button("▶️ Run policy-as-code checks", key="run_policy_checks"):
        with st.spinner("Running policy checks on the backend..."):
            try:
                _policy_payload = _governance_api_client.governance_policy_checks()
                st.session_state["policy_results"] = _policy_payload.get("policy_results")
                st.session_state["red_team_results"] = _policy_payload.get("red_team_results")
            except _governance_api_client.BackendError as exc:
                st.warning(f"Policy checks unavailable from backend (HTTP {exc.status_code}).")

    pol = st.session_state.get("policy_results")
    redt = st.session_state.get("red_team_results")
    if pol:
        overall = "✅ PASS" if pol["overall_status"] == "PASS" else "🔴 FAIL"
        st.markdown(f"**Policy checks: {overall}** ({pol['passed']}/{pol['total']})")
        st.dataframe(
            [{"Policy": c["policy"], "Status": c["status"], "Detail": c["detail"]} for c in pol["checks"]],
            width="stretch",
        )
    if redt:
        overall = "✅ PASS" if redt["overall_status"] == "PASS" else "🔴 FAIL"
        st.markdown(f"**Red-team probes: {overall}** ({redt['passed']}/{redt['total']})")
        st.dataframe(
            [{"Probe": p["probe"], "Expected flagged": p["expected_flagged"],
              "Actually flagged": p["actually_flagged"], "Status": p["status"]} for p in redt["probes"]],
            width="stretch",
        )

    # ── Optional W&B / RAI logging ──────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Weights & Biases (RAI) logging — optional")
    try:
        _wandb_status = _governance_api_client.governance_wandb_status()
    except _governance_api_client.BackendError as exc:
        _wandb_status = {}
        st.info(f"W&B status unavailable from backend (HTTP {exc.status_code}).")
    if not _wandb_status.get("available"):
        st.info("W&B is not installed in the backend environment.")
    elif not _wandb_status.get("configured_for_online"):
        st.info(
            "W&B is installed in the backend but not configured for online logging. "
            "Offline logging may be available if allowed for the active profile."
        )
    else:
        st.success("W&B is configured in the backend. Online logging available.")
    if _wandb_status.get("available") and pol:
        wb_mode = st.radio("W&B mode", ["offline", "online"], horizontal=True, key="wandb_mode")
        if st.button("📤 Log governance results to W&B", key="log_wandb"):
            with st.spinner("Logging to W&B via the backend..."):
                res = _governance_api_client.governance_log_wandb({
                    "project": "triage-governance",
                    "policy_results": pol,
                    "red_team_results": redt,
                    "run_name": "governance-policy-checks",
                    "mode": wb_mode,
                })
            if res.get("status") == "LOGGED":
                st.success(f"Logged {res['metrics_logged']} metrics to W&B."
                           + (f" Run: {res['run_url']}" if res.get("run_url") else " (offline run saved locally)."))
            else:
                st.warning(f"W&B logging {res.get('status')}: {res.get('reason', '')}")

# ═══════════════════════════════════════════════════════════════════════════
# TAB 5 — REVIEW QUEUE
# ═══════════════════════════════════════════════════════════════════════════
with tab_queue:
    st.subheader("📋 Human Review Queue")
    st.caption(
        "Backend-driven and full-MIMIC-only. Cases come from the FastAPI backend "
        "(GET /cases); reviews are submitted via the backend (POST "
        "/cases/{case_uid}/reviews). There is no dataset selector and no local "
        "queue from any other data source. Research only — clinician review required."
    )
    if not _has_perm(PERM_SUBMIT_REVIEW) and not _has_perm(PERM_VIEW_AUDIT_LOG):
        st.info("🔒 You do not have permission to view or action the review queue.")
    else:
        _chosen = render_backend_case_selector("queue", label="Case", show_label=True)
        _cases = [_chosen] if _chosen is not None else []

        if not _cases:
            st.info(
                "No full-MIMIC cases are available, so the review queue is empty. "
                "Configure MIMIC_FULL_ED_DIR on an approved environment to populate "
                "it (the app fails closed otherwise — it never falls back to any other "
                "data source)."
            )
        else:
            render_triage_input_summary(_chosen, key_prefix=f"queue_{_chosen['case_uid']}")
            # Reuse the backend-driven review submission form.
            _render_triage_review_submission(_chosen["case_uid"],
                                             _chosen.get("source_dataset", ""),
                                             key_prefix="queue")


with tab_audit:
    if _gate_tab(PERM_VIEW_AUDIT_LOG, 'view_audit_log', 'Audit Log'):
        st.subheader("📜 Clinician Review Audit Log")
        st.caption(
            "Complete history of all clinician reviews, across every dataset. "
            "This log is append-only."
        )

        audit_log_path = settings.processed_dir / "human_reviews.jsonl"
        from frontend import api_client
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
                    st.error("🔒 The backend denied access to the audit log.")
                else:
                    st.error(f"⚠️ Backend audit read failed (HTTP {exc.status_code}). "
                             "Audit is unavailable (local files are not an acceptable "
                             "patient-data audit source).")
            # Do NOT read local JSONL in patient-data mode.
            all_reviews = []
            runs = []
            reruns = []
        else:
            # Demo mode: local JSONL is acceptable for display.
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
                            "Final category": r.final_category or "—",
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
                    "Same patient re-run with edited vitals: previous → new assignment."
                )
                st.dataframe(
                    [
                        {
                            "Case UID": r.case_uid,
                            "Timestamp": r.timestamp_utc[:19],
                            "Prev acuity": r.previous_final_acuity,
                            "New acuity": r.new_final_acuity,
                            "Movement": r.movement or "—",
                            "Changed vitals": ", ".join(v.field for v in r.changed_vitals) or "—",
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
            st.markdown("**MIMIC-IV-ED Full** (credentialed — the only dataset)")
            st.write(f"MIMIC_FULL_ED_DIR: **{'configured' if _fm.get('mimic_full_dir_configured') else 'not configured'}**")
            st.write(f"MIMIC_FULL_MODEL_PATH: **{'configured' if _fm.get('mimic_full_model_configured') else 'not configured'}**")
            st.write("Tables: edstays, triage, vitalsign, diagnosis, medrecon, pyxis")
            st.write("Target: **acuity**")
            st.caption(
                "Excluded leakage/outcome fields: acuity-as-input, disposition, "
                "outtime, hadm_id, diagnoses, medication tables, full-stay vitals. "
                "Full MIMIC is read only from MIMIC_FULL_ED_DIR on an approved "
                "environment; it is never copied into this app. Synthetic "
                "MIMIC-shaped fixtures are used only for tests and the Azure "
                "supervisor demo, never as a clinical or patient-data source. "
                "Clinical use: not allowed — research only."
            )
        st.caption(
            "Only full MIMIC-IV-ED is a prediction source; no other dataset is "
            "summarised here."
        )

        # The two reports below are public-demo synthetic logs regenerated by
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
            with st.expander(f"Synthetic walkthrough log ({len(scenarios)} scenarios)"):
                st.caption(
                    "Generated by scripts/run_synthetic_walkthrough.py. These are "
                    "constructed demonstration cases, not real patient data."
                )
                walk_rows = []
                for s in scenarios:
                    outcome = (s.get("result") or s.get("comparison") or {})
                    workflow_action = outcome.get("workflow_action", "N/A")
                    status = outcome.get("classification_status") or outcome.get(
                        "new_classification_status", ""
                    )
                    walk_rows.append({
                        "Scenario": s.get("scenario", "?"),
                        "Engine status": status or "—",
                        "Workflow action": workflow_action,
                        "Clinician review required": "Yes",
                        "Note": s.get("note", outcome.get("escalation_note", "") or "—"),
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
                    f"({review.source_dataset or 'Unknown (pre-dataset-tracking)'}) — "
                    f"{review.review_status} — "
                    f"{review.reviewer_role or (review.reviewer_roles[0] if review.reviewer_roles else 'reviewer')} — {review.created_at_utc[:19]}"
                ):
                    st.write(f"Decision: {review.review_status}")
                    st.write(f"Reviewer: {review.reviewer_role or (review.reviewer_roles[0] if review.reviewer_roles else 'reviewer')}")
                    if review.review_comment:
                        st.write(f"Notes: {review.review_comment}")
                    if review.clinician_override:
                        st.write(f"Override: {review.clinician_override}")
                    if debug_ui_enabled():
                        st.json(review.model_dump(mode="json"))


    # ═══════════════════════════════════════════════════════════════════════════
    # TAB 7 — MODEL PERFORMANCE
    # ═══════════════════════════════════════════════════════════════════════════
with tab_models:
    st.subheader("📊 ML Model Performance — Full MIMIC-IV-ED only")
    st.caption(
        "The only prediction model is trained on full MIMIC-IV-ED (credentialed) "
        "on an approved environment and read at runtime from MIMIC_FULL_MODEL_PATH. "
        "No model is bundled in this app. Metrics below are shown only if the "
        "approved environment has produced them. Not clinically validated; UHL "
        "validation pending governance approval. Clinician review required."
    )

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
        "Safety statement": [
            "Not clinically validated",
            "Full MIMIC only",
            "No synthetic training",
            "No leakage fields",
            "UHL validation",
            "Clinician review",
        ],
        "Status": [
            "required warning",
            "required source",
            "required in report provenance",
            "required leakage audit",
            "not done / not claimed",
            "required for every output",
        ],
    })
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
        rows = []
        for cand in _comparison.get("candidates", []):
            har = (cand.get("high_acuity_recall") or {}).get("recall")
            uot_payload = cand.get("under_over_triage") or {}
            ordinal_payload = cand.get("ordinal_metrics") or {}
            rows.append({
                "model": cand.get("model_name"),
                "high_acuity_recall": har,
                "severe_under_triage_rate": uot_payload.get("severe_under_triage_rate"),
                "under_triage_rate": uot_payload.get("under_triage_rate"),
                "over_triage_rate": uot_payload.get("over_triage_rate"),
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
        st.caption("Aggregate research metrics only. Not clinically validated.")
    else:
        st.markdown("### Full-MIMIC model comparison")
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
    st.caption("Only full MIMIC-IV-ED is a prediction source; no other dataset or "
               "model is shown here. Synthetic MIMIC-shaped fixtures are used only "
               "for tests and the Azure supervisor demo.")


with st.sidebar:
    st.markdown("### System Status")

    # Read full-MIMIC status FROM THE BACKEND (its environment is authoritative in
    # two-service mode). Fall back to local diagnostic only if the backend is
    # unreachable (single-process/demo).
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
        if api_client.reads_must_use_backend():
            _fm = {
                "env_present": False,
                "loadable": False,
                "state": "backend unavailable",
                "active_profile": "backend_unavailable",
                "reason": (
                    "Backend unavailable. Full-MIMIC status cannot be displayed "
                    "from the frontend in a sensitive mode."
                ),
            }
            _model_runtime = {"state": "backend unavailable"}
            _reports_runtime = {"env_present": False}
        else:
            from app.data_pipeline.mimic_full_loader import full_mimic_diagnostic
            _diag = full_mimic_diagnostic()
            _fm = {
                "env_present": _diag.get("mimic_full_dir_env_set"),
                "loadable": _diag.get("full_mimic_loadable"),
                "state": "loaded successfully" if _diag.get("full_mimic_loadable") else "not loadable",
                "reason": _diag.get("reason"),
                "active_profile": _diag.get("active_profile"),
                "full_mimic_requested_for_azure_demo": _diag.get("full_mimic_requested_for_azure_demo"),
            }
            _model_runtime = {
                "state": (
                    "configured"
                    if _diag.get("mimic_full_model_file_exists")
                    else "not configured"
                )
            }
            _reports_runtime = {"env_present": False}

    _dir_ok = bool(_fm.get("loadable"))
    st.markdown(f"**MIMIC-IV-ED Full:** {_fm.get('state', 'unknown')}")
    st.markdown(f"**Full MIMIC model:** {_model_runtime.get('state', 'unknown')}")
    _reports_state = "configured" if _reports_runtime.get("env_present") else "not configured"
    st.markdown(f"**Model reports:** {_reports_state}")
    _active_profile = _runtime.get("active_profile") or _fm.get("active_profile", "?")
    st.caption(f"Active profile: `{_active_profile}`"
               + ("" if _backend_status else " (frontend-local; backend unreachable)"))
    _demo_real_requested = bool(_fm.get("full_mimic_requested_for_azure_demo"))
    if auth_session.get("azure_supervisor_demo_mode") and _demo_real_requested and _dir_ok:
        st.markdown("**Current case source:** credentialed full MIMIC-IV-ED")
        st.markdown("**Demo source:** synthetic data disabled")
    elif auth_session.get("azure_supervisor_demo_mode"):
        st.markdown("**Current case source:** synthetic supervisor demo cases")
        st.markdown("**Demo source:** synthetic supervisor fixture")
    else:
        st.markdown("**Current case source:** full MIMIC-IV-ED when configured")
    st.markdown("**Model training source:** approved full MIMIC-IV-ED only")
    st.markdown("**Clinical use:** not allowed")

    try:
        _llm = api_client.llm_status()
    except Exception:
        _llm = {}
    azure_ok = bool(_llm.get("azure_config_present"))
    _azure_creds = bool(_llm.get("azure_credentials_present"))
    _llm_blocked_local = bool(_llm.get("blocked_by_local_credentialed_research"))
    _llm_blocked_mimic = bool(_llm.get("blocked_by_credentialed_mimic_cloud_policy"))
    if azure_ok:
        _llm_label = "configured"
    elif _azure_creds and _llm_blocked_local:
        _llm_label = "credentials present; blocked by local credentialed mode"
    elif _azure_creds and _llm_blocked_mimic:
        _llm_label = "credentials present; blocked for credentialed MIMIC"
    elif _azure_creds:
        _llm_label = "credentials present; not enabled"
    else:
        _llm_label = "not configured"
    st.markdown(f"**Azure OpenAI / LLM explanation:** {_llm_label}")

    if not azure_ok and _llm.get("reason_if_disabled"):
        st.caption(f"LLM status: {_llm.get('reason_if_disabled')}")
    if _llm_blocked_local:
        _flags = _llm.get("required_enable_flags") or {}
        st.caption(
            "Required LLM opt-in flags: "
            f"ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH={_flags.get('ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH')}, "
            f"APPROVED_CLOUD_LLM_DATA_PROCESSING={_flags.get('APPROVED_CLOUD_LLM_DATA_PROCESSING')}."
        )
    if _llm_blocked_mimic and not _llm_blocked_local:
        _flags = _llm.get("required_enable_flags") or {}
        st.caption(
            "Required credentialed-MIMIC LLM opt-in flags: "
            f"ALLOW_CLOUD_LLM_WITH_CREDENTIALED_MIMIC={_flags.get('ALLOW_CLOUD_LLM_WITH_CREDENTIALED_MIMIC')}, "
            f"APPROVED_CLOUD_LLM_DATA_PROCESSING={_flags.get('APPROVED_CLOUD_LLM_DATA_PROCESSING')}."
        )

    if not _dir_ok:
        st.caption(f"Full MIMIC-IV-ED not loadable. Reason: {_fm.get('reason','')}")
        st.caption("On your approved local research machine set "
                   "LOCAL_CREDENTIALED_RESEARCH=true and MIMIC_FULL_ED_DIR (the 'ed' "
                   "directory) on the BACKEND, then restart both services.")

    # ── Security status panel (item L) ──────────────────────────────────────
    st.markdown("---")
    with st.expander("🛡️ Security status", expanded=False):
        try:
            from frontend import api_client as _security_api_client
            _ss = _security_api_client.security_status()
            _mode_label = {
                "secured_research": "Secured research",
                "local_credentialed_research": "Local credentialed research",
                "azure_supervisor_demo": "Azure supervisor demo",
                "public_demo": "Public demo",
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
                "Demo role-switcher": _ss["demo_role_switcher_enabled"],
                "Full MIMIC configured": _ss["full_mimic_configured"],
                "CORS wildcard": _ss["cors_is_wildcard"],
            }
            st.table({"Setting": list(_rows.keys()),
                      "Value": [str(v) for v in _rows.values()]})
            if _ss["unsafe_combinations"]:
                st.error("⚠️ Unsafe configuration:\n" +
                         "\n".join(f"- {p}" for p in _ss["unsafe_combinations"]))
            else:
                st.success("✅ No unsafe configuration detected for the current mode.")
            st.caption("Full-MIMIC path and secrets are never shown — only whether "
                       "they are configured.")
        except api_client.BackendError as _exc:
            if _exc.status_code in (401, 403):
                st.caption(_local_role_restart_hint(target="security status"))
            else:
                st.caption(f"Security status unavailable from backend (HTTP {_exc.status_code}).")
        except Exception:
            st.caption("Security status unavailable.")

    st.markdown("---")
    st.markdown("### Quick Start (approved environment)")
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

    st.markdown("---")
    st.caption(
        "NOT FOR CLINICAL USE\n\n"
        "Research prototype. Full MIMIC-IV-ED is the only prediction dataset and is "
        "read only from MIMIC_FULL_ED_DIR on an approved environment. Provisional "
        "Manchester-style categories are research-only (no clinician-approved "
        "ruleset). The LLM explanation layer only explains already-computed "
        "evidence; it has no "
        "authority over any clinical decision."
    )
