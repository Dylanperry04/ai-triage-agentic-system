from pathlib import Path

from app.governance.agent_audit import (
    AgentAuditRecord,
    append_agent_audit_record,
    load_agent_audit_records,
    new_run_id,
    normalise_safety_flags,
    utc_now_iso,
)


def test_normalise_safety_flags_handles_none():
    assert normalise_safety_flags(None) == []


def test_normalise_safety_flags_handles_list():
    assert normalise_safety_flags(["missing_vitals", "human_review"]) == [
        "missing_vitals",
        "human_review",
    ]


def test_append_and_load_agent_audit_record(tmp_path: Path):
    output_path = tmp_path / "agent_audit_log.jsonl"

    record = AgentAuditRecord(
        run_id=new_run_id(),
        timestamp_utc=utc_now_iso(),
        environment="test",
        case_id=30115213,
        source_dataset="MIMIC-IV-ED Demo v2.2",
        agent_name="Safety Review Agent",
        agent_status="HUMAN_REVIEW_REQUIRED",
        evidence_source="test",
        input_reference={"stay_id": 30115213},
        output_summary={"validation_status": "NEEDS_HUMAN_DATA_REVIEW"},
        safety_flags=["missing_vitals"],
        human_review_required=True,
        clinical_use_allowed=False,
        automated_manchester_triage_allowed=False,
        governance_verdict="NOT_READY_FOR_CLINICAL_USE",
        notes="Test record.",
    )

    append_agent_audit_record(output_path, record)

    records = load_agent_audit_records(output_path)

    assert len(records) == 1
    assert records[0]["agent_name"] == "Safety Review Agent"
    assert records[0]["clinical_use_allowed"] is False
    assert records[0]["automated_manchester_triage_allowed"] is False
    assert records[0]["governance_verdict"] == "NOT_READY_FOR_CLINICAL_USE"