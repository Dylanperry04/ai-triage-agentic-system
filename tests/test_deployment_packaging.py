from pathlib import Path
import json
import re

import pytest
from packaging.requirements import InvalidRequirement, Requirement


REPO = Path(__file__).resolve().parents[1]


def test_dockerfile_installs_autogen_runtime_dependencies():
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements-autogen.txt" in text
    assert "pip install --no-cache-dir -r requirements-autogen.txt" in text


def test_default_requirements_include_autogen_for_azure_oryx_deployments():
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    assert "autogen-agentchat==0.7.5" in text
    assert "autogen-core==0.7.5" in text
    assert "autogen-ext[openai]==0.7.5" in text


def test_default_requirements_include_wandb_sdk_for_governance_toolkit():
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^wandb[<>=]", text, re.MULTILINE)


def test_default_requirements_can_load_selected_smote_artifacts():
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^imbalanced-learn[<>=]", text, re.MULTILINE)


def test_wandb_runtime_directories_are_not_packaged_source():
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^wandb/$", text, re.MULTILINE)


def test_training_requirements_include_imbalance_and_curve_dependencies():
    text = (REPO / "requirements-ml.txt").read_text(encoding="utf-8")
    assert re.search(r"^imbalanced-learn", text, re.MULTILINE)
    assert re.search(r"^matplotlib", text, re.MULTILINE)


def test_runtime_audit_logs_are_ignored_for_clean_packaging():
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    for path in (
        "data/processed/access_audit.jsonl",
        "data/processed/case_workflow_state.jsonl",
        "data/processed/human_reviews.jsonl",
        "data/processed/workflow_runs.jsonl",
        "data/processed/workflow_reruns.jsonl",
        "data/processed/supporting_uploads/",
    ):
        assert path in text


def test_azure_deploy_workflow_excludes_runtime_data_and_cleans_generated_caches():
    text = (REPO / ".github" / "workflows" / "deploy-azure.yml").read_text(
        encoding="utf-8"
    )

    assert "--exclude 'data/processed/'" in text
    assert 'echo "WANDB_MODE=disabled"' in text
    assert "--no-compile" in text
    assert "export PYTHONDONTWRITEBYTECODE=1" in text
    assert "import traceback" in text
    assert "traceback.print_exc()" in text
    assert "Clean deployment package" in text
    assert "find deployment -type f \\( -name '*.pyc' -o -name '*.pyo' \\) -print -delete" in text
    assert "find deployment/.python_packages -depth -type d" in text
    assert "-iname 'test'" in text
    assert "-iname 'tests'" in text
    assert "-iname '*.pkl'" in text
    assert "-iname '*.joblib'" in text
    assert "Verify deployment ZIP contains runtime dependencies" in text
    assert ".python_packages/lib/site-packages/uvicorn/__init__.py" in text
    assert ".python_packages/lib/site-packages/fastapi/__init__.py" in text
    assert "rm -rf deployment/data/processed" in text
    assert (
        text.index("Verify packaged application")
        < text.index("Clean deployment package")
        < text.index("Create deployment ZIP")
        < text.index("Verify deployment ZIP contains runtime dependencies")
        < text.index("Check deployment package hygiene")
    )


@pytest.mark.parametrize(
    "filename",
    [
        "requirements.txt",
        "requirements-autogen.txt",
        "requirements-azure.txt",
        "requirements-dev.txt",
        "requirements-ml.txt",
        "requirements-ml-gpu.txt",
    ],
)
def test_requirements_files_have_parseable_requirement_lines(filename):
    path = REPO / filename
    bad_lines: list[str] = []
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = re.sub(r"\s+#.*$", "", raw_line).strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        try:
            Requirement(line)
        except InvalidRequirement as exc:
            bad_lines.append(f"{filename}:{lineno}: {line!r} ({exc})")

    assert not bad_lines


def test_azure_preflight_checks_autogen_container_dependency():
    text = (REPO / "scripts" / "azure_preflight_check.py").read_text(encoding="utf-8")
    assert "dockerfile_installs_autogen_runtime_deps" in text
    assert "requirements-autogen.txt" in text
    assert "bundled_model_artifact_present_or_not_claimed" in text
    assert "selected_model_sha256" in text or "sha256" in text


def test_package_hygiene_allows_only_pinned_deployment_model_artifact(tmp_path):
    import zipfile

    from scripts.check_package_hygiene import forbidden_entries

    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "model_outputs/last_training/mimic_full_acuity_selected.joblib",
            b"allowed pinned deployment artifact",
        )
        zf.writestr(
            "19.91/model_outputs/last_training/mimic_full_acuity_selected.joblib",
            b"allowed pinned deployment artifact in versioned archive root",
        )
        zf.writestr("19.91/.env.example", b"allowed template")
        zf.writestr("19.91/.env", b"forbidden")
        zf.writestr("19.91/.env.production", b"forbidden")
        zf.writestr("model_outputs/other.joblib", b"forbidden")
        zf.writestr("19.91/model_outputs/other.joblib", b"forbidden")
        zf.writestr("model_outputs/last_training/other.pkl", b"forbidden")
        zf.writestr("19.91/app/main.pyc", b"forbidden")
        zf.writestr("data/processed/supporting_uploads/case/file.png", b"forbidden")
        zf.writestr("19.91/.git/config", b"forbidden")
        zf.writestr("19.91/.venv/pyvenv.cfg", b"forbidden")
        zf.writestr("19.91/frontend-react/node_modules/pkg/index.js", b"forbidden")
        zf.writestr("19.91/wandb/run.txt", b"forbidden")
        zf.writestr("19.91/wandb-offline/run.txt", b"forbidden")
        zf.writestr("19.91/wandb_logs/run.txt", b"forbidden")
        zf.writestr(
            "19.91/.python_packages/lib/site-packages/wandb/__init__.py",
            b"allowed dependency package",
        )

    bad = forbidden_entries(archive)
    assert "model_outputs/last_training/mimic_full_acuity_selected.joblib" not in bad
    assert "19.91/model_outputs/last_training/mimic_full_acuity_selected.joblib" not in bad
    assert "19.91/.env.example" not in bad
    assert "19.91/.env" in bad
    assert "19.91/.env.production" in bad
    assert "model_outputs/other.joblib" in bad
    assert "19.91/model_outputs/other.joblib" in bad
    assert "model_outputs/last_training/other.pkl" in bad
    assert "19.91/app/main.pyc" in bad
    assert "data/processed/supporting_uploads/case/file.png" in bad
    assert "19.91/.git/config" in bad
    assert "19.91/.venv/pyvenv.cfg" in bad
    assert "19.91/frontend-react/node_modules/pkg/index.js" in bad
    assert "19.91/wandb/run.txt" in bad
    assert "19.91/wandb-offline/run.txt" in bad
    assert "19.91/wandb_logs/run.txt" in bad
    assert "19.91/.python_packages/lib/site-packages/wandb/__init__.py" not in bad


def test_azure_supervisor_demo_fixture_is_packaged_source():
    fixture = REPO / "data" / "demo" / "azure_supervisor_demo_cases.jsonl"
    assert fixture.exists()
    rows = [
        json.loads(line)
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # v18 cohort: 60 cases from scripts/generate_supervisor_demo_cases.py.
    assert len(rows) == 60
    assert all(row["source_dataset"] == "MIMIC-IV-ED-Synthetic-Supervisor-Demo" for row in rows)
    assert all(row.get("synthetic_demo") is True for row in rows)
    assert all("not real patient data" in row.get("demo_data_notice", "").lower() for row in rows)


def test_slurm_main_runs_use_realistic_balanced_safety_recall_floor():
    for name in ("train_full_mimic.slurm", "train_rescue_mimic.slurm"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert 'export MIMIC_MIN_HIGH_ACUITY_RECALL=0.80' in text
        assert 'export MIMIC_MIN_HIGH_ACUITY_RECALL=0.97' not in text
        assert "sensitivity run" in text


def test_slurm_final_runs_include_imbalance_experiments_and_smote_smoke_test():
    required_candidates = [
        "logistic_regression_unweighted",
        "logistic_regression",
        "structured_logistic_smote",
        "structured_xgboost_smote",
        "raw_tfidf_svd_xgboost_smote",
        "raw_tfidf_svd_lightgbm_smote",
        "raw_tfidf_svd_catboost_smote",
    ]
    for name in ("train_full_mimic.slurm", "train_rescue_mimic.slurm"):
        text = (REPO / name).read_text(encoding="utf-8")
        for candidate in required_candidates:
            assert candidate in text
        assert "SMOTE_SMOKE_CANDIDATES" in text
        assert "--quick-test" in text
        assert "--min-high-acuity-recall 0" in text
        assert "--min-specificity 0" in text
        assert "--max-predicted-urgent-rate 1" in text
        assert "smote_smoke" in text


def test_model_performance_endpoint_exposes_roc_pr_curve_points():
    text = (REPO / "app" / "api" / "status_routes.py").read_text(encoding="utf-8")
    assert "csv.DictReader" in text
    assert '"roc_curve"' in text
    assert '"pr_curve"' in text
    assert "selected_model_roc_curve.csv" in text
    assert "selected_model_pr_curve.csv" in text
    assert "display_point_count" in text
    assert "downsampled" in text


def test_imbalance_retraining_docs_pin_final_candidate_list():
    text = (REPO / "docs" / "FULL_MIMIC_IMBALANCE_RETRAINING.md").read_text(
        encoding="utf-8"
    )
    assert "sbatch train_full_mimic.slurm" in text
    assert "sbatch train_rescue_mimic.slurm" in text
    assert "FINAL_CANDIDATES=" in text
    assert "--candidates all" not in text
