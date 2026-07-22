"""#2: live edit-rerun-reassign — movement logic + audit record roundtrip."""
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from app.schemas.rerun import (
    WorkflowRerunRecord, VitalChange, compute_movement,
)
from app.storage.rerun_repository import append_rerun, read_reruns
from app.schemas.workflow_run import make_case_uid


def test_compute_movement():
    assert compute_movement(4, 2) == "ESCALATION"     # smaller = more urgent
    assert compute_movement(2, 4) == "DE_ESCALATION"
    assert compute_movement(3, 3) == "NO_CHANGE"
    assert compute_movement(None, 2) is None
    assert compute_movement(2, None) is None


def test_rerun_record_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "workflow_reruns.jsonl"
        assert read_reruns(path) == []
        rec = WorkflowRerunRecord(
            rerun_id="r1", timestamp_utc=datetime.now(timezone.utc).isoformat(),
            case_uid=make_case_uid("MIMIC-IV-ED-Demo-v2.2", 32259573),
            source_dataset="MIMIC-IV-ED-Demo-v2.2", stay_id=32259573,
            previous_final_acuity=3, new_final_acuity=1,
            previous_category="Urgent (Yellow)", new_category="Immediate (Red)",
            changed_vitals=[VitalChange(field="heartrate", previous=90.0, new=195.0)],
            movement="ESCALATION", override_applied_new=True, override_tier_new="EXTREME",
            reason="ESCALATION after 1 vital change(s).",
        )
        append_rerun(path, rec)
        out = read_reruns(path)
        assert len(out) == 1
        assert out[0].case_uid == make_case_uid("MIMIC-IV-ED-Demo-v2.2", 32259573)
        assert "32259573" not in out[0].case_uid  # raw id not present
        assert out[0].movement == "ESCALATION"
        assert out[0].changed_vitals[0].field == "heartrate"


def test_real_rerun_escalates_on_extreme_vital():
    """Editing a case's HR to an extreme value triggers the DETERMINISTIC safety
    layer (CRITICAL_PHYSIOLOGY_FLAGGED), independent of any ML model. (ML acuity
    is only produced for full MIMIC-IV-ED via a credentialed model, absent here;
    the safety layer is what must always fire.)"""
    from app.agents.orchestrator import run_workflow
    from app.schemas.internal import EDTriageCase
    edited = EDTriageCase(**{
        "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000001,
        "subject_id": 10000001,
        "edstay": {"subject_id": 10000001, "stay_id": 30000001, "gender": "F",
                   "arrival_transport": "AMBULANCE", "disposition": "HOME"},
        "triage": {"subject_id": 10000001, "stay_id": 30000001, "heartrate": 195.0,
                   "chiefcomplaint": "COLLAPSE", "acuity": None},
        "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
    })
    r = run_workflow(edited)
    # The deterministic safety cross-check flags critical physiology regardless of
    # whether an ML acuity number is available.
    status = getattr(getattr(r, "decision", None), "classification_status", None)
    flags = getattr(r, "safety_flags", None)
    assert status == "CRITICAL_PHYSIOLOGY_FLAGGED" or (flags and "CRITICAL" in str(flags))
