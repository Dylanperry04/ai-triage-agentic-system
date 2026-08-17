from pathlib import Path
import re

import pytest
from packaging.requirements import InvalidRequirement, Requirement


REPO = Path(__file__).resolve().parents[1]


def test_dockerfile_is_a_complete_backend_only_runtime_image():
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements.txt requirements-runtime.txt ./" in text
    assert "pip install --no-cache-dir -r requirements.txt" in text
    assert "ARG SKIP_AZURE=0" in text
    assert 'if [ "$SKIP_AZURE" != "1" ]' in text
    assert 'CMD ["sh", "startup-backend.sh"]' in text
    assert "startup-frontend.sh" not in text
    assert "SERVICE_ROLE" not in text


def test_default_requirements_include_autogen_for_azure_oryx_deployments():
    default = (REPO / "requirements.txt").read_text(encoding="utf-8")
    assert "-r requirements-runtime.txt" in default
    text = (REPO / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert "autogen-agentchat==0.7.5" in text
    assert "autogen-core==0.7.5" in text
    assert "autogen-ext[openai]==0.7.5" in text


def test_default_requirements_include_wandb_sdk_for_governance_toolkit():
    text = (REPO / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert re.search(r"^wandb[<>=]", text, re.MULTILINE)


def test_default_requirements_can_load_selected_smote_artifacts():
    runtime = (REPO / "requirements-runtime.txt").read_text(encoding="utf-8")
    training = (REPO / "requirements-ml.txt").read_text(encoding="utf-8")
    assert re.search(r"^catboost[<>=]", runtime, re.MULTILINE)
    assert not re.search(r"^(streamlit|xgboost|lightgbm|imbalanced-learn)[<>=]", runtime, re.MULTILINE)
    assert re.search(r"^imbalanced-learn[<>=]", training, re.MULTILINE)


def test_active_runtime_pins_security_fixed_web_dependencies():
    runtime = (REPO / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert "fastapi==0.140.0" in runtime
    assert "starlette==1.3.1" in runtime
    assert "python-multipart==0.0.32" in runtime
    assert "python-dotenv==1.2.2" in runtime
    assert "orjson==3.11.6" in runtime
    assert "requests==2.33.0" in runtime


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


def test_uhl_runtime_cache_is_ignored_for_clean_packaging():
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^data/cache/$", text, re.MULTILINE)


def test_azure_deploy_workflow_excludes_runtime_data_and_cleans_generated_caches():
    text = (REPO / ".github" / "workflows" / "deploy-azure.yml").read_text(
        encoding="utf-8"
    )

    assert "--exclude 'data/processed/'" in text
    assert "--exclude 'data/cache/'" in text
    assert "UHL_CASE_CACHE_PATH=$RUNNER_TEMP/alter-uhl-cache/uhl_cases.sqlite3" in text
    assert "-r requirements-legacy-ui.txt" in text
    assert "python -m pytest --collect-only -q tests/" in text
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
    assert "rm -rf deployment/data/processed deployment/data/cache" in text
    assert '-r "deployment/${RUNTIME_REQUIREMENTS}"' in text
    assert "max_zip_bytes=900000000" in text
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
        "requirements-runtime.txt",
        "requirements-legacy-ui.txt",
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


def test_azure_preflight_checks_complete_container_runtime_manifest():
    text = (REPO / "scripts" / "azure_preflight_check.py").read_text(encoding="utf-8")
    assert "dockerfile_installs_complete_runtime_manifest" in text
    assert "COPY requirements.txt requirements-runtime.txt" in text
    assert "uhl_model_serving_contract_valid" in text
    assert "uhl_dataset_sha256_matches" in text
    assert "uhl_model_sha256_matches" in text


def test_package_hygiene_allows_only_pinned_deployment_model_artifact(tmp_path):
    import zipfile

    from scripts.check_package_hygiene import forbidden_entries

    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "artifacts/model/uhl_synthetic_acuity_selected.joblib",
            b"allowed pinned deployment artifact",
        )
        zf.writestr(
            "19.91/artifacts/model/uhl_synthetic_acuity_selected.joblib",
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
        zf.writestr("19.91/data/cache/uhl_cases.sqlite3", b"forbidden runtime cache")
        zf.writestr(
            "19.91/data/cache/.uhl_cases.sqlite3.9.building",
            b"forbidden in-progress runtime cache",
        )
        zf.writestr(
            "19.91/validation/function-package-deadbeef/function_app.py",
            b"forbidden generated staging package",
        )
        zf.writestr("19.91/.git/config", b"forbidden")
        zf.writestr("19.91/.venv/pyvenv.cfg", b"forbidden")
        zf.writestr("19.91/.ruff_cache/cache-entry", b"forbidden")
        zf.writestr("19.91/frontend-react/node_modules/pkg/index.js", b"forbidden")
        zf.writestr("19.91/wandb/run.txt", b"forbidden")
        zf.writestr("19.91/wandb-offline/run.txt", b"forbidden")
        zf.writestr("19.91/wandb_logs/run.txt", b"forbidden")
        zf.writestr(
            "19.91/.python_packages/lib/site-packages/wandb/__init__.py",
            b"allowed dependency package",
        )

    bad = forbidden_entries(archive)
    assert "artifacts/model/uhl_synthetic_acuity_selected.joblib" not in bad
    assert "19.91/artifacts/model/uhl_synthetic_acuity_selected.joblib" not in bad
    assert "19.91/.env.example" not in bad
    assert "19.91/.env" in bad
    assert "19.91/.env.production" in bad
    assert "model_outputs/other.joblib" in bad
    assert "19.91/model_outputs/other.joblib" in bad
    assert "model_outputs/last_training/other.pkl" in bad
    assert "19.91/app/main.pyc" in bad
    assert "data/processed/supporting_uploads/case/file.png" in bad
    assert "19.91/data/cache/uhl_cases.sqlite3" in bad
    assert "19.91/data/cache/.uhl_cases.sqlite3.9.building" in bad
    assert "19.91/validation/function-package-deadbeef/function_app.py" in bad
    assert "19.91/.git/config" in bad
    assert "19.91/.venv/pyvenv.cfg" in bad
    assert "19.91/.ruff_cache/cache-entry" in bad
    assert "19.91/frontend-react/node_modules/pkg/index.js" in bad
    assert "19.91/wandb/run.txt" in bad
    assert "19.91/wandb-offline/run.txt" in bad
    assert "19.91/wandb_logs/run.txt" in bad
    assert "19.91/.python_packages/lib/site-packages/wandb/__init__.py" not in bad


def test_uhl_dataset_and_selected_model_are_packaged_sources():
    import hashlib

    dataset = REPO / "data" / "uhl_dataset_final.csv.gz"
    model = REPO / "artifacts" / "model" / "uhl_synthetic_acuity_selected.joblib"
    assert dataset.is_file()
    assert model.is_file()
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == (
        "f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c"
    )
    assert hashlib.sha256(model.read_bytes()).hexdigest() == (
        "7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b"
    )


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
