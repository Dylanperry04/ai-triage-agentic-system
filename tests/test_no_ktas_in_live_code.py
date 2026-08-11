"""
Static + behavioural proof tests that KTAS / MIMIC-IV-ED Demo do not reappear in
the LIVE code (app, frontend, scripts, preflight). Historical changelog and the
non-runtime archive/ folder are explicitly allowed to mention KTAS as history.

If any of these fail, KTAS/demo has leaked back into a live surface.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Directories whose .py files must be free of KTAS/demo live references.
LIVE_DIRS = ["app", "frontend", "scripts"]

# Files/dirs explicitly allowed to contain the word KTAS (history / archive only).
ALLOWED_SUBSTRINGS = ("/archive/", "KTAS_CHANGELOG.md")

FORBIDDEN_PATTERNS = [
    "ktas_adapter",
    "RandomForest_ktas",
    "run_ktas_pipeline",
    "build_ktas_labels",
    "load_ktas_cases",
    "load_mimic_demo_cases",
    "Kaggle-KTAS",
    "mimic_demo_acuity_rf",
]


def _live_py_files():
    for d in LIVE_DIRS:
        for p in (REPO / d).rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            if any(a in str(p) for a in ALLOWED_SUBSTRINGS):
                continue
            yield p


class TestNoKtasInLiveCode:
    @pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
    def test_pattern_absent_from_live_python(self, pattern):
        hits = []
        for p in _live_py_files():
            text = p.read_text(encoding="utf-8", errors="ignore")
            if pattern in text:
                hits.append(str(p.relative_to(REPO)))
        assert not hits, f"Forbidden live reference '{pattern}' found in: {hits}"

    def test_ktas_adapter_file_is_deleted(self):
        assert not (REPO / "app/data_pipeline/ktas_adapter.py").exists()

    def test_ktas_pipeline_scripts_are_deleted(self):
        for s in ("run_ktas_pipeline.py", "build_ktas_labels.py",
                  "build_mimic_demo_labels.py", "audit_mimic_demo.py"):
            assert not (REPO / "scripts" / s).exists(), f"{s} should be deleted"

    def test_ktas_model_artefacts_not_in_models_dir(self):
        models = REPO / "data/models"
        if models.exists():
            bad = [p.name for p in models.iterdir()
                   if "ktas" in p.name.lower() or "demo" in p.name.lower()]
            assert bad == [], f"KTAS/demo model artefacts present: {bad}"

    def test_registry_has_no_ktas_or_demo_keys(self):
        import json
        from app.config import settings
        if not settings.model_registry_path.exists():
            pytest.skip("no registry")
        reg = json.loads(settings.model_registry_path.read_text())
        bad = [k for k in reg if "ktas" in k.lower() or "demo" in k.lower()]
        assert bad == [], f"registry has KTAS/demo keys: {bad}"

    def test_registry_traceability_matches_app_version(self):
        import json
        from app.config import settings
        from app.version import APP_VERSION, PACKAGE_CHECKPOINT
        reg = json.loads(settings.model_registry_path.read_text())
        assert reg["version"] == APP_VERSION
        assert reg["package_checkpoint"] == PACKAGE_CHECKPOINT


class TestHealthAndRootAreFullMimicOnly:
    def _client(self, monkeypatch):
        for v in ("MIMIC_FULL_ED_DIR", "MIMIC_FULL_MODEL_PATH"):
            monkeypatch.delenv(v, raising=False)
        from starlette.testclient import TestClient
        import app.main
        return TestClient(app.main.app)

    def test_health_advertises_only_full_mimic(self, monkeypatch):
        import json
        from app.version import APP_VERSION, PACKAGE_CHECKPOINT
        c = self._client(monkeypatch)
        health = c.get("/health").json()
        body = json.dumps(health)
        assert "KTAS" not in body and "Demo" not in body
        assert "MIMIC-IV-ED-Full-v2.2" in body
        assert health["version"] == APP_VERSION
        assert health["package_checkpoint"] == PACKAGE_CHECKPOINT
        assert health["configured_prediction_dataset"] == "MIMIC-IV-ED-Full-v2.2"
        assert health["active_case_source"] == "not_configured"
        assert health["active_case_source_mode"] == "public_demo"

    def test_root_advertises_only_full_mimic(self, monkeypatch):
        import json
        from app.version import APP_VERSION, PACKAGE_CHECKPOINT
        c = self._client(monkeypatch)
        j = c.get("/").json()
        assert j["datasets_available"] == ["MIMIC-IV-ED-Full-v2.2"]
        assert "KTAS" not in json.dumps(j) and "Demo" not in json.dumps(j)
        assert j["patient_data_ready"] is False
        assert j["version"] == APP_VERSION
        assert j["package_checkpoint"] == PACKAGE_CHECKPOINT
        assert j["configured_prediction_dataset"] == "MIMIC-IV-ED-Full-v2.2"
        assert j["active_case_source"] == "not_configured"
        assert j["active_case_source_mode"] == "public_demo"

    def test_runtime_status_reports_active_source_metadata(self, monkeypatch):
        from starlette.testclient import TestClient
        import app.main

        c = self._client(monkeypatch)
        r = TestClient(app.main.app).get("/runtime/status")
        assert r.status_code == 200
        payload = r.json()
        assert payload["configured_prediction_dataset"] == "MIMIC-IV-ED-Full-v2.2"
        assert payload["active_case_source"] == "not_configured"
        assert payload["active_case_source_mode"] == "public_demo"

    def test_model_performance_does_not_read_demo_artefacts(self, monkeypatch, tmp_path):
        import json
        from starlette.testclient import TestClient
        import app.main
        from app.config import settings

        proc = tmp_path / "processed"
        proc.mkdir()
        (proc / "mimic_model_comparison.json").write_text('{"old": "demo"}')
        (proc / "mimic_acuity_model_evaluation.json").write_text('{"old": "demo"}')
        monkeypatch.setattr(settings, "processed_dir", proc)
        monkeypatch.setenv("DEMO_ROLE", "ed_doctor")

        r = TestClient(app.main.app).get("/model/performance")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "not_available"
        assert "demo" not in json.dumps(body).lower()

    def test_model_performance_refuses_synthetic_marked_reports(self, monkeypatch, tmp_path):
        import json
        from starlette.testclient import TestClient
        import app.main

        reports = tmp_path / "reports"
        reports.mkdir()
        (reports / "full_mimic_model_comparison.json").write_text(json.dumps({
            "training_run_id": "run-1",
            "feature_schema_hash": "a" * 64,
            "dataset_source": "MIMIC-IV-ED-Full-v2.2",
            "synthetic_data_used": True,
            "patient_level_split": True,
            "test_set_used_for_model_selection": False,
            "preprocessing_inside_pipeline": True,
            "leakage_audit_passed": True,
            "synthetic_audit_passed": False,
        }))
        monkeypatch.setenv("MIMIC_FULL_MODEL_REPORT_DIR", str(reports))
        monkeypatch.setenv("DEMO_ROLE", "researcher")

        r = TestClient(app.main.app).get("/model/performance")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "invalid_provenance"
        assert body["model_readiness_valid"] is False
        assert body["artefacts"] == {}
        assert any("synthetic" in issue.lower() for issue in body["model_provenance_issues"])

    def test_model_performance_refuses_missing_configured_model_even_with_reports(
        self, monkeypatch, tmp_path
    ):
        import hashlib
        import json
        from starlette.testclient import TestClient
        import app.main
        from ml_training.feature_engineering import FEATURE_NAMES

        feature_hash = hashlib.sha256(
            json.dumps(
                list(FEATURE_NAMES),
                separators=(",", ":"),
                sort_keys=False,
            ).encode("utf-8")
        ).hexdigest()
        sha = "a" * 64
        run_id = "run-missing-model"
        reports = tmp_path / "reports"
        reports.mkdir()

        shared = {
            "training_run_id": run_id,
            "feature_schema_hash": feature_hash,
            "dataset_source": "MIMIC-IV-ED-Full-v2.2",
            "synthetic_data_used": False,
            "demo_fixture_used": False,
            "test_fixture_used": False,
        }
        comparison = {
            **shared,
            "model_artifact_sha256": sha,
            "patient_level_split": True,
            "test_set_used_for_model_selection": False,
            "preprocessing_inside_pipeline": True,
            "leakage_audit_passed": True,
            "synthetic_audit_passed": True,
            "candidates": [
                {
                    "model_name": "candidate",
                    "high_acuity_recall": {"recall": 0.8},
                    "under_over_triage": {
                        "under_triage_rate": 0.1,
                        "severe_under_triage_rate": 0.01,
                        "over_triage_rate": 0.2,
                    },
                    "ordinal_metrics": {
                        "mae": 0.5,
                        "quadratic_weighted_kappa": 0.4,
                        "within_1_acuity_level_accuracy": 0.9,
                    },
                }
            ],
            "untouched_test_metrics": {
                "under_over_triage": {
                    "under_triage_rate": 0.1,
                    "severe_under_triage_rate": 0.01,
                },
                "ordinal_metrics": {
                    "mae": 0.5,
                    "quadratic_weighted_kappa": 0.4,
                    "within_1_acuity_level_accuracy": 0.9,
                },
            },
        }
        artefacts = {
            "full_mimic_model_comparison.json": comparison,
            "mimic_full_model_card.json": {
                **shared,
                "model_artifact_sha256": sha,
            },
            "mimic_full_dataset_card.json": shared,
            "mimic_full_training_provenance.json": {
                **shared,
                "patient_level_split": True,
                "test_set_used_for_model_selection": False,
                "preprocessing_inside_pipeline": True,
                "leakage_audit_passed": True,
                "synthetic_audit_passed": True,
            },
            "mimic_full_feature_schema.json": {
                "training_run_id": run_id,
                "feature_schema_hash": feature_hash,
            },
            "full_mimic_under_over_triage_report.json": shared,
            "full_mimic_calibration_report.json": shared,
            "full_mimic_confusion_matrix.json": shared,
            "full_mimic_subgroup_metrics.json": shared,
            "full_mimic_class_distribution.json": shared,
            "selected_model_binary_curve_report.json": shared,
            "full_mimic_feature_importance.json": shared,
            "selected_model_feature_importance.json": shared,
        }
        for name, payload in artefacts.items():
            (reports / name).write_text(json.dumps(payload), encoding="utf-8")
        for name in (
            "full_mimic_class_distribution.csv",
            "all_models_roc_auc_comparison.csv",
            "full_mimic_feature_importance.csv",
        ):
            (reports / name).write_text("metric,value\nx,1\n", encoding="utf-8")
        (reports / "selected_model_roc_curve.csv").write_text(
            "threshold,false_positive_rate,true_positive_rate\n0,0,0\n1,1,1\n",
            encoding="utf-8",
        )
        (reports / "selected_model_pr_curve.csv").write_text(
            "threshold,recall,precision\n0,0,1\n1,1,0.5\n",
            encoding="utf-8",
        )
        for name in ("selected_model_roc_curve.png", "selected_model_pr_curve.png"):
            (reports / name).write_bytes(b"not-a-real-png-needed-for-presence-test")

        monkeypatch.setenv("MIMIC_FULL_MODEL_REPORT_DIR", str(reports))
        monkeypatch.setenv("MIMIC_FULL_MODEL_PATH", str(tmp_path / "missing.joblib"))
        monkeypatch.setenv("MIMIC_FULL_MODEL_SHA256", sha)
        monkeypatch.setenv("DEMO_ROLE", "researcher")

        r = TestClient(app.main.app).get("/model/performance")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "invalid_provenance"
        assert body["model_readiness_valid"] is False
        assert body["model_file_exists"] is False
        assert body["model_artifact_status"]["model_file_exists"] is False
        assert any(
            "configured MIMIC_FULL_MODEL_PATH does not exist" in issue
            for issue in body["model_provenance_issues"]
        )
        assert body["artefacts"] == {}


class TestModelRouterNeverReturnsDemoOrKtas:
    @pytest.mark.parametrize("ds", ["Kaggle-KTAS", "MIMIC-IV-ED-Demo-v2.2", "anything"])
    def test_non_full_datasets_get_no_prediction(self, ds, monkeypatch):
        monkeypatch.delenv("MIMIC_FULL_MODEL_PATH", raising=False)
        from app.agents.ml_prediction_agent import run_ml_prediction
        from app.schemas.workflow import TriageTimeInput
        r = run_ml_prediction(TriageTimeInput(
            subject_id=1, stay_id=1, source_dataset=ds, chiefcomplaint="x"))
        assert r.prediction_available is False
        assert r.predicted_ktas_class is None


class TestCasesIsFullMimicOnly:
    def test_cases_empty_without_full_mimic_never_demo(self, tmp_path, monkeypatch):
        for v in ("MIMIC_FULL_ED_DIR", "MIMIC_FULL_MODEL_PATH"):
            monkeypatch.delenv(v, raising=False)
        proc = tmp_path / "processed"; proc.mkdir()
        monkeypatch.setattr("app.config.settings.processed_dir", proc)
        import app.api.case_resolver as cr
        monkeypatch.setattr(cr.settings, "processed_dir", proc)
        assert cr.list_cases() == []
        assert cr.KNOWN_DATASETS == ("mimic_full",)


class TestNoLiveImportOfDemoKtasLoaders:
    def test_prediction_and_serving_modules_dont_import_demo_ktas(self):
        for mod in ("app/agents/ml_prediction_agent.py", "app/api/case_routes.py",
                    "app/api/case_resolver.py", "app/api/health_routes.py",
                    "app/main.py", "app/api/review_routes.py", "app/api/triage_routes.py"):
            text = (REPO / mod).read_text(encoding="utf-8")
            assert "load_ktas_cases" not in text, f"{mod} imports KTAS loader"
            assert "load_mimic_demo_cases" not in text, f"{mod} imports demo loader"
            assert "ktas_adapter" not in text, f"{mod} imports ktas_adapter"
