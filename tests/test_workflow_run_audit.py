"""Tests for the workflow-run audit log (#8)."""
import tempfile
from pathlib import Path

from app.config import settings
from app.agents.orchestrator import run_workflow
from app.schemas.workflow_run import build_workflow_run_record, make_case_uid
from app.storage.workflow_run_repository import append_workflow_run, read_workflow_runs
from app.schemas.internal import EDTriageCase


def _synthetic_mimic_case(hr=88.0, cc="CHEST PAIN", acuity=2):
    return EDTriageCase(**{
        "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000001,
        "subject_id": 10000001,
        "edstay": {"subject_id": 10000001, "stay_id": 30000001, "gender": "F",
                   "arrival_transport": "AMBULANCE", "disposition": "HOME"},
        "triage": {"subject_id": 10000001, "stay_id": 30000001, "heartrate": hr,
                   "chiefcomplaint": cc, "acuity": acuity},
        "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
    })


def test_case_uid_format():
    # make_case_uid is now PSEUDONYMOUS (no raw stay_id); separator '~' is URL-safe.
    uid = make_case_uid("MIMIC-IV-ED-Full-v2.2", 123)
    assert uid.startswith("MIMIC-IV-ED-Full-v2.2~")   # dataset readable
    assert "123" not in uid                           # raw stay_id not exposed
    assert make_case_uid(None, 5).startswith("UNKNOWN~")
    # stable: same inputs -> same token
    assert make_case_uid("MIMIC-IV-ED-Full-v2.2", 5) == make_case_uid("MIMIC-IV-ED-Full-v2.2", 5)


def test_run_record_built_from_workflow_result():
    """A workflow-run audit record is built from a result and carries the source
    dataset and a pseudonymous case identifier. (Demo cases no longer produce an
    ML acuity, so we assert the record structure, not a predicted number.)"""
    wf = run_workflow(_synthetic_mimic_case())
    rec = build_workflow_run_record(wf, "rid-1", "2026-06-18T00:00:00Z")
    assert rec.workflow_run_id == "rid-1"
    from app.version import APP_VERSION, PACKAGE_CHECKPOINT
    assert rec.app_version == APP_VERSION
    assert rec.package_checkpoint == PACKAGE_CHECKPOINT
    # The in-memory record carries a pseudonymous case_uid; the raw stay_id is
    # redacted by the guarded writer on persistence (tested in the storage tests).
    assert rec.case_uid and "~" in rec.case_uid
    assert rec.clinician_review_required is True


def test_append_and_read_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "workflow_runs.jsonl"
        assert read_workflow_runs(path) == []  # missing file -> empty
        _case = _synthetic_mimic_case()
        wf = run_workflow(_case)
        rec = build_workflow_run_record(wf, "rid-3", "2026-06-18T00:00:00Z")
        append_workflow_run(path, rec)
        append_workflow_run(path, rec)
        out = read_workflow_runs(path)
        assert len(out) == 2
        assert out[0].workflow_run_id == "rid-3"
