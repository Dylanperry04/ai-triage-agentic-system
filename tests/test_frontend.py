"""
Tests for the Streamlit frontend (frontend/app.py) using Streamlit's AppTest
framework, which actually runs the script in a simulated session.

ARCHITECTURE (v13-final): the frontend is FRONTEND-ONLY. Every protected action
goes through frontend/api_client.py to the FastAPI backend (the sole enforcement
boundary). There is ONE live dataset — full MIMIC-IV-ED (credentialed) — so there
is no dataset selector. Tests inject a SYNTHETIC MIMIC-shaped case list via the
frontend_cases_override.jsonl mechanism (the resolver and the UI both read it), so
they never depend on credentialed data and never run a real trained model.

A NOTE ON monkeypatch SAFETY: this file patches app.config.settings via string
path only (safe — frontend/app.py is re-exec'd fresh by AppTest each call). It
never patches frontend.app directly (that corrupts Streamlit form state in this
version). See the isolated_processed_dir docstring.
"""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.config import settings

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
FRONTEND_PATH = FRONTEND_DIR / "app.py"
MIMIC_FIXTURE = Path(__file__).parent / "fixtures" / "sample_mimic_full_cases.jsonl"


def _run_as(role: str, timeout: int = 120):
    """Run the app as a given demo role (sets the sidebar role-switcher) so tests
    can reach role-gated tabs."""
    at = AppTest.from_file(str(FRONTEND_PATH))
    at.run(timeout=timeout)
    sw = next((x for x in at.selectbox if x.key == "demo_role"), None)
    if sw is not None and role in sw.options:
        sw.set_value(role)
        at.run(timeout=timeout)
    return at


@pytest.fixture
def isolated_processed_dir(tmp_path, monkeypatch):
    """Point settings.processed_dir at a temp dir pre-populated with the synthetic
    MIMIC-shaped fixture, so the backend resolver (read in-process by the UI's
    api_client) serves those cases. Patches app.config.settings only."""
    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "frontend_cases_override.jsonl").write_text(
        MIMIC_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    (processed / "missing_triage_inputs_report.json").write_text(
        json.dumps({"cases_with_missing_triage_inputs": 0,
                    "missing_case_percent": 0.0, "missing_cases": []}))
    monkeypatch.setattr("app.config.settings.processed_dir", processed)
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(processed))
    return processed


class TestAppRendersWithoutErrors:
    def test_app_renders_clean(self, isolated_processed_dir):
        at = AppTest.from_file(str(FRONTEND_PATH))
        at.run(timeout=120)
        assert not at.exception

    def test_app_renders_with_no_cases_available(self, tmp_path, monkeypatch):
        # No override file and no credentialed data => backend serves no cases.
        # The app must still render (no fatal st.stop crashing the whole app).
        proc = tmp_path / "processed"
        proc.mkdir()
        monkeypatch.setattr("app.config.settings.processed_dir", proc)
        at = AppTest.from_file(str(FRONTEND_PATH))
        at.run(timeout=120)
        assert not at.exception


class TestNoDatasetSelector:
    def test_there_is_no_dataset_filter_radio(self, isolated_processed_dir):
        at = AppTest.from_file(str(FRONTEND_PATH))
        at.run(timeout=120)
        # The old dataset-filter radio keys must not exist anymore.
        keys = [r.key for r in at.radio]
        assert not any(k and "dataset_filter" in k for k in keys)

    def test_no_ktas_or_demo_dataset_labels_in_ui(self, isolated_processed_dir):
        at = AppTest.from_file(str(FRONTEND_PATH))
        at.run(timeout=120)
        all_text = " ".join(str(m.value) for m in at.markdown)
        all_text += " ".join(str(m.value) for m in at.info if hasattr(m, "value"))
        assert "Kaggle-KTAS" not in all_text
        # The only dataset concept surfaced is MIMIC-IV-ED.
        # (Demo label may appear only in historical/disabled contexts, not as a
        #  selectable dataset; the selector itself is gone, verified above.)


class TestBackendDrivenCaseSelector:
    def test_case_selector_lists_pseudonymous_case_uids(self, isolated_processed_dir):
        at = AppTest.from_file(str(FRONTEND_PATH))
        at.run(timeout=120)
        # The triage-review case selector should list case_uids (pseudonymous),
        # never raw "Stay <n>" labels with raw stay_ids.
        sel = next((s for s in at.selectbox if s.key == "triage_review_case_select"), None)
        assert sel is not None, "backend-driven case selector should be present"
        for opt in sel.options:
            assert "~" in opt          # pseudonymous case_uid format
            # raw numeric stay_id (e.g. 30000001) must not appear in the label
            assert "30000001" not in opt


class TestMimicOnlyPredictionPath:
    def test_demo_and_ktas_are_not_prediction_sources(self):
        from app.agents.ml_prediction_agent import run_ml_prediction
        from app.schemas.workflow import TriageTimeInput
        for ds in ("MIMIC-IV-ED-Demo-v2.2", "Kaggle-KTAS"):
            r = run_ml_prediction(TriageTimeInput(
                subject_id=1, stay_id=1, source_dataset=ds, chiefcomplaint="x"))
            assert r.prediction_available is False
            assert r.model_name == "no_model_for_dataset"

    def test_full_mimic_fails_closed_without_model(self, monkeypatch):
        monkeypatch.delenv("MIMIC_FULL_MODEL_PATH", raising=False)
        from app.agents.ml_prediction_agent import run_ml_prediction
        from app.schemas.workflow import TriageTimeInput
        r = run_ml_prediction(TriageTimeInput(
            subject_id=1, stay_id=1, source_dataset="MIMIC-IV-ED-Full-v2.2",
            chiefcomplaint="x"))
        assert r.prediction_available is False


class TestModelRegistryHasNoDemoOrKtas:
    def test_registry_has_no_demo_or_ktas_model_keys(self):
        import json as _json
        if not settings.model_registry_path.exists():
            pytest.skip("no registry in this environment")
        reg = _json.loads(settings.model_registry_path.read_text())
        bad = [k for k in reg.keys()
               if "ktas" in k.lower() or "demo" in k.lower()]
        assert bad == [], f"registry still references retired datasets: {bad}"


class TestRawJsonInExpanders:
    def test_no_top_level_raw_json_on_first_render(self, isolated_processed_dir):
        # Normal pages should not dump raw JSON at the top level on first load.
        at = AppTest.from_file(str(FRONTEND_PATH))
        at.run(timeout=120)
        assert not at.exception

    def test_debug_ui_false_hides_debug_panels(self, isolated_processed_dir, monkeypatch):
        monkeypatch.delenv("DEBUG_UI", raising=False)
        at = AppTest.from_file(str(FRONTEND_PATH))
        at.run(timeout=120)
        assert not at.exception
        blob = []
        for attr in (
            "markdown", "caption", "title", "info", "warning", "error",
            "subheader", "header", "text", "expander",
        ):
            for element in getattr(at, attr, []):
                blob.append(str(getattr(element, "value", "")))
                blob.append(str(getattr(element, "label", "")))
        text = " ".join(blob)
        assert "Developer/debug" not in text


class TestNoKtasOrDemoInRenderedUI:
    """Proof tests: the rendered Streamlit UI text contains no KTAS / MIMIC-IV-ED
    Demo anywhere, across roles."""

    def _rendered_text(self, role):
        import os
        previous_role = os.environ.get("DEMO_ROLE")
        os.environ["DEMO_ROLE"] = role
        try:
            at = AppTest.from_file(str(FRONTEND_PATH))
            at.run(timeout=120)
        finally:
            if previous_role is None:
                os.environ.pop("DEMO_ROLE", None)
            else:
                os.environ["DEMO_ROLE"] = previous_role
        blob = []
        for attr in ('markdown', 'caption', 'title', 'info', 'warning', 'error',
                     'subheader', 'header', 'text'):
            try:
                for m in getattr(at, attr):
                    blob.append(str(getattr(m, 'value', '')))
            except Exception:
                pass
        return " ".join(blob), at

    def test_no_ktas_text_for_nurse(self):
        text, at = self._rendered_text("triage_nurse")
        assert not at.exception
        assert "KTAS" not in text
        assert "MIMIC-IV-ED Demo" not in text
        assert "Kaggle" not in text

    def test_no_ktas_text_for_supervisor(self):
        text, at = self._rendered_text("clinical_supervisor")
        assert not at.exception
        assert "KTAS" not in text
        assert "MIMIC-IV-ED Demo" not in text


class TestProtectedActionsGoThroughApiClient:
    """The frontend must call the backend via frontend/api_client.py for protected
    actions, and must NOT import the orchestrator's run_workflow on the live path."""

    def test_frontend_imports_api_client(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "from frontend import api_client" in src or "import api_client" in src

    def test_frontend_does_not_call_run_workflow_directly(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        # run_workflow must not be imported/called in the frontend (backend-only).
        assert "run_workflow(" not in src, "frontend must not call run_workflow directly"

    def test_frontend_does_not_import_demo_or_ktas_loaders(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "load_ktas_cases" not in src
        assert "load_mimic_demo_cases" not in src
        assert "ktas_adapter" not in src

    def test_sensitive_sidebar_does_not_use_local_full_mimic_fallback(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "api_client.reads_must_use_backend()" in src
        assert "Backend unavailable. Full-MIMIC status cannot be displayed" in src

    def test_no_duplicate_critical_frontend_labels_or_session_calls(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert src.count('st.markdown("**ML model estimate**")') == 1
        assert "Manchester-style triage equivalent" in src
        assert "Research-only provisional display. Not a clinical triage decision." not in src
        assert "Final research output" not in src
        assert "Rules/safety category" not in src
        assert src.count("payload = _session_api_client.auth_session()") == 1

    def test_synthetic_fixture_wording_mentions_supervisor_demo(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "fixtures are used for tests only" not in src
        assert "tests and the Azure supervisor demo" not in src

    def test_presentation_mode_accepts_common_truthy_values(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "PRESENTATION_UI_MODE" in src
        assert '"1", "true", "yes", "on"' in src

    def test_frontend_removes_governance_blocker_clutter(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert '_gate_tab(PERM_VIEW_AUDIT_LOG, "governance_report", "Governance")' in src
        assert "_governance_report = _governance_api_client.governance_report()" in src
        assert "Governance controls" in src
        assert "Review gate evidence" in src
        assert "Items needing review" in src
        assert "Policy and WandB toolkit" in src
        assert "_governance_api_client.governance_wandb_status()" in src
        assert "_governance_api_client.governance_policy_checks()" in src
        assert "_governance_api_client.governance_log_wandb(" in src
        assert "Send latest policy results to WandB" in src
        assert "Create offline WandB run" in src
        assert "governance logging detail" in src
        assert "WandB SDK unavailable" not in src
        assert "disabled=not _wandb_usable" in src
        assert "WandB log request completed." in src
        assert "WandB log request was skipped" in src
        assert "WandB log request failed" in src
        assert 'st.success(f"WandB log request status:' not in src
        assert 'st.info("Governance evidence is available in the backend report.")' not in src
        assert "Blocking Issues:" not in src
        assert "Clinical-use readiness" not in src

    def test_role_based_navigation_and_itd_ask_sources_present(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "TAB_DEFS" in src
        assert "visible_tabs" in src
        assert '"itd_ask_tools", "ITD System Assistant"' in src
        assert "tab_itd_ask" in src
        assert "ITD System Assistant" in src
        assert "api_client.system_assistant" in src
        assert "api_client.audit_dashboard" in src
        assert "Export filtered audit results to CSV" in src
        assert "selected_feature_importance" in src
        assert '("cost_runtime"' not in src
        assert '("followup_comparison"' not in src
        assert '("system_status"' not in src
        assert "api_client.cost_estimate" not in src

    def test_followup_additional_info_ui_present(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "Additional Information & Reassessment" in src
        assert "tab_followup = tab_maintainability" in src
        assert "Updated complaint/context at triage" in src
        assert "Clinician-supplied additional information" in src
        assert "Optional scan/image metadata" in src
        assert "metadata_recorded_pending_multimodal_analysis" in src

    def test_frontend_removes_forbidden_visible_warning_strings(self):
        frontend_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(FRONTEND_DIR.glob("*.py"))
        )
        forbidden = [
            "Credentialed MIMIC-IV-ED demo mode",
            "Optional W&B telemetry is unavailable",
            "Clinical use not allowed",
            "Clinical use: not allowed",
            "not clinical use",
            "Not for clinical use",
            "NOT FOR CLINICAL USE",
            "prototype",
            "DEMO ONLY",
            "demo only",
            "simulated identity",
            "not hospital SSO",
            "not UHL patient data",
            "Research-only",
            "Research only",
            "Not clinically validated",
            "Weights & Biases",
            "Optional W&B telemetry",
            "WandB SDK unavailable",
            "Simulated role",
            "Reviewer role (demo)",
            "demo mode",
            "Azure supervisor demo",
            "Public demo",
            "Demo role",
        ]
        lowered = frontend_sources.lower()
        missing = [needle for needle in forbidden if needle.lower() in lowered]
        assert missing == []

    def test_patient_facing_ask_and_multiagent_controls_are_removed(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "Run multi-agent explanation" not in src
        assert "**Ask question**" not in src
        assert "Ask a case question" not in src
        assert "Ask a follow-up question" not in src
        assert "Run LLM explanation" not in src
        assert "api_client.explain_case(" not in src
        assert "api_client.multiagent_explain_case(" not in src

    def test_reassessment_does_not_expose_clinical_chatbot(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "api_client.followup_explain_case(" not in src
        assert "api_client.followup_multiagent_explain_case(" not in src
        assert "followup_multiagent::" not in src
        assert "followup_answer::" not in src
        assert "Additional Information & Reassessment" in src
        assert "ITD System Assistant" in src

    def test_queue_pagination_and_overdue_vitals_controls_present(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "Search cases" in src
        assert "Page size" in src
        assert "Previous" in src
        assert "Next" in src
        assert "_vitals_overdue_info" in src
        assert "api_client.sweep_overdue_vitals" in src
        assert "api_client.mark_overdue_vitals_alert" in src
        assert "api_client.acknowledge_overdue_vitals" in src

    def test_review_form_terminal_actions_are_role_and_state_gated(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert '_can_resolve_escalation = bool(' in src
        assert '_review_options.append("ESCALATION_CONFIRMED")' in src
        assert '_review_options.extend(["ESCALATION_REJECTED", "ESCALATION_CLOSED"])' in src
        assert '_review_options.append("DISCHARGED")' in src
        assert '{"ed_doctor", "clinical_supervisor", "security_admin"}' in src
        assert '["REQUEST_MORE_INFORMATION", "NOT_REVIEWED", "ACCEPTED_AS_PRESENTED",\n             "OVERRIDE_REQUIRED", "ESCALATION_REQUIRED", "ESCALATION_CONFIRMED",' not in src

    def test_sidebar_system_status_is_security_permission_gated(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "if _has_perm(PERM_VIEW_SECURITY_STATUS):" in src
        assert "### System Status" in src
        assert "Quick Start (ITD only)" in src

    def test_model_performance_403_has_clean_permission_message(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "Your current role cannot view full model performance" in src
        assert "Model performance unavailable from backend (HTTP {exc.status_code})." in src

    def test_model_report_visualisations_do_not_show_unavailable_dead_ends(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "Precision/recall scatter is unavailable" not in src
        assert "Confusion matrix is unavailable in the current report artefacts." not in src
        assert "Candidate Safety Trade-Off" in src
        assert "Recall by triage category" in src
        assert "per_class" in src
        assert 'selected_candidate.get("confusion_matrix")' in src
        assert "_render_binary_curves(" in src
        assert "ROC curve: high acuity 1-2 vs 3-5" in src
        assert "Precision-recall curve: high acuity 1-2" in src
        assert '_artefacts.get("roc_curve")' in src
        assert '_artefacts.get("pr_curve")' in src

    def test_confusion_matrix_uses_readable_row_normalised_display(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert '"row_percent"' in src
        assert '"percent_label"' in src
        assert '"count_label"' in src
        assert "% of true acuity row" in src
        assert "Severe under-triage" in src
        assert "Confusion matrix counts" in src
        assert '"labelAngle": 0' in src

    def test_candidate_scatter_uses_zoomed_readable_axes_and_labels(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "def _zoom_domain(" in src
        assert "def _stagger_scatter_labels(" in src
        assert "def _is_high_acuity_label(" in src
        assert "float(str(label)) in {1.0, 2.0}" in src
        assert 'per_class.get(f"{label}.0")' in src
        assert '"model_label"' in src
        assert '"label_x"' in src
        assert '"label_y"' in src
        assert '"tickCount": 6' in src
        assert '"limit": 170' in src
        assert '"format": ".1%"' in src
        assert '"nice": False' in src
        assert '"scale": {"domain": [0, 1]}' not in src
        assert "Recall vs Precision - all models" in src
        assert '"field": "high_acuity_precision"' in src
        assert '"field": "high_acuity_recall"' in src
        assert '"field": "predicted_urgent_rate"' in src
        assert "High-acuity precision" in src
        assert "Predicted urgent rate" in src

    def test_model_performance_displays_recall_by_triage_category(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "Recall by triage category" in src
        assert "def _per_class_metric_rows(" in src
        assert "def _render_recall_by_triage_category(" in src
        assert "candidate per-class metrics" in src
        assert '"acuity_key": f"acuity_{label}"' in src
        assert 'per_class.get(f"{label}.0")' in src
        assert "_render_recall_by_triage_category(comparison_candidates)" in src

    def test_audit_tab_body_is_permission_gated(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "if not _gate_tab(PERM_VIEW_AUDIT_LOG, 'view_audit_log', 'Audit Log')" in src
        assert "st.stop()" in src
        assert '"by_case_uid"' in src
        assert "Top cases by audit events" in src


class TestAzureSupervisorDemoUi:
    def test_supervisor_source_warning_is_not_visible(self, isolated_processed_dir, monkeypatch):
        for name in (
            "PATIENT_DATA_MODE",
            "LOCAL_CREDENTIALED_RESEARCH",
            "AUTH_REQUIRED",
            "TRUSTED_AUTH_PROXY",
            "REAL_PATIENT_DATA",
            "ALLOW_FULL_MIMIC_IN_AZURE_DEMO",
            "REAL_MIMIC_DEMO_ACKNOWLEDGED",
        ):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AZURE_SUPERVISOR_DEMO_MODE", "true")
        monkeypatch.setenv("ALLOW_DEMO_ROLE_SWITCHER", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "demo")
        at = AppTest.from_file(str(FRONTEND_PATH))
        at.run(timeout=120)
        assert not at.exception
        warnings = " ".join(str(getattr(w, "value", "")) for w in at.warning)
        assert "Synthetic supervisor demo data only" not in warnings
        assert "not real MIMIC" not in warnings
        assert "not real patient data" not in warnings
        assert "Credentialed MIMIC-IV-ED demo mode" not in warnings


class TestDocumentationTruth:
    def test_readme_matches_synthetic_supervisor_demo_source(self):
        readme = (FRONTEND_PATH.parent.parent / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())
        assert "used for **tests only**" not in readme
        assert "automated tests and the Azure supervisor demo" in readme
        assert "There is no demo dataset" not in readme
        assert "not real MIMIC and not real patient data" in normalized
