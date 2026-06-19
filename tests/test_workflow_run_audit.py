"""Tests for the workflow-run audit log (#8)."""
import tempfile
from pathlib import Path

from app.config import settings
from app.data_pipeline.mimic_adapter import load_mimic_demo_cases
from app.data_pipeline.ktas_adapter import load_ktas_cases
from app.agents.orchestrator import run_workflow
from app.schemas.workflow_run import build_workflow_run_record, make_case_uid
from app.storage.workflow_run_repository import append_workflow_run, read_workflow_runs


def test_case_uid_format():
    assert make_case_uid("MIMIC-IV-ED-Demo-v2.2", 123) == "MIMIC-IV-ED-Demo-v2.2:123"
    assert make_case_uid("Kaggle-KTAS", 5) == "Kaggle-KTAS:5"
    assert make_case_uid(None, 5) == "UNKNOWN:5"


def test_mimic_run_record_captures_acuity_and_override():
    mimic, _ = load_mimic_demo_cases(settings.raw_demo_dir, n=1)
    wf = run_workflow(mimic[0])
    rec = build_workflow_run_record(wf, "rid-1", "2026-06-18T00:00:00Z")
    assert rec.source_dataset == "MIMIC-IV-ED-Demo-v2.2"
    assert rec.prediction_scale == "MIMIC_ACUITY_MAPPED_TO_MTS"
    assert rec.predicted_mimic_acuity is not None
    assert rec.final_category is not None
    assert rec.clinician_review_required is True
    assert rec.case_uid.startswith("MIMIC-IV-ED-Demo-v2.2:")


def test_ktas_run_record_has_no_mts_mapping():
    ktas, _ = load_ktas_cases(settings.raw_ktas_csv, n=1)
    wf = run_workflow(ktas[0])
    rec = build_workflow_run_record(wf, "rid-2", "2026-06-18T00:00:00Z")
    assert rec.prediction_scale == "KTAS"
    assert rec.predicted_ktas_class is not None
    assert rec.mapped_mts_category is None
    assert rec.predicted_mimic_acuity is None


def test_append_and_read_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "workflow_runs.jsonl"
        assert read_workflow_runs(path) == []  # missing file -> empty
        mimic, _ = load_mimic_demo_cases(settings.raw_demo_dir, n=1)
        wf = run_workflow(mimic[0])
        rec = build_workflow_run_record(wf, "rid-3", "2026-06-18T00:00:00Z")
        append_workflow_run(path, rec)
        append_workflow_run(path, rec)
        out = read_workflow_runs(path)
        assert len(out) == 2
        assert out[0].workflow_run_id == "rid-3"
