"""Model evidence must identify the policy and cohort actually evaluated."""
import json
import shutil

import pytest

from app.api.status_routes import _uhl_model_performance_payload
from app.config import settings


def test_evaluation_metrics_are_not_claimed_for_current_serving_pipeline():
    result = _uhl_model_performance_payload()
    assert result["status"] == "available"
    evidence = result["artefacts"]
    assert evidence["evaluation_policy"]["type"] == "ordinal_cost_sensitive"
    assert evidence["serving_policy"]["type"] == "modal_with_urgent_class_threshold"
    assert evidence["metrics_apply_to_serving_policy"] is False
    assert evidence["model_comparison"]["patient_overlap_train_test"] is None


@pytest.mark.parametrize("filename,field,value", [
    ("selection_record.json", "promoted_artifact_sha256", "different-model"),
    ("uhl_synthetic_dataset_card.json", "dataset_sha256", "different-cohort"),
    ("uhl_synthetic_class_distribution.json", "training_run_id", "different-run"),
])
def test_unrelated_reports_fail_provenance_check(monkeypatch, tmp_path, filename, field, value):
    report_dir = tmp_path / "reports"
    shutil.copytree(settings.uhl_report_dir, report_dir)
    path = report_dir / filename
    report = json.loads(path.read_text(encoding="utf-8"))
    report[field] = value
    path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(settings, "uhl_report_dir", report_dir)
    result = _uhl_model_performance_payload()
    assert result["status"] == "invalid_provenance"
    assert result["artefacts"] == {}
    assert result["model_provenance_issues"]
