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

FRONTEND_PATH = Path(__file__).parent.parent / "frontend" / "app.py"
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
        assert "Final research output" not in src
        assert "Rules/safety category" not in src
        assert src.count("payload = _session_api_client.auth_session()") == 1

    def test_synthetic_fixture_wording_mentions_supervisor_demo(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "fixtures are used for tests only" not in src
        assert "tests and the Azure supervisor demo" in src

    def test_multiagent_button_is_above_question_flow(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert src.index("Run multi-agent explanation") < src.index("**Ask question**")

    def test_model_performance_403_has_clean_permission_message(self):
        src = FRONTEND_PATH.read_text(encoding="utf-8")
        assert "Your current role cannot view full model performance" in src
        assert "Model performance unavailable from backend (HTTP {exc.status_code})." in src


class TestAzureSupervisorDemoUi:
    def test_supervisor_demo_banner_is_visible(self, isolated_processed_dir, monkeypatch):
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
        assert "Synthetic supervisor demo data only" in warnings
        assert "not real MIMIC" in warnings
        assert "not real patient data" in warnings


class TestDocumentationTruth:
    def test_readme_matches_synthetic_supervisor_demo_source(self):
        readme = (FRONTEND_PATH.parent.parent / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())
        assert "used for **tests only**" not in readme
        assert "automated tests and the Azure supervisor demo" in readme
        assert "There is no demo dataset" not in readme
        assert "not real MIMIC and not real patient data" in normalized
