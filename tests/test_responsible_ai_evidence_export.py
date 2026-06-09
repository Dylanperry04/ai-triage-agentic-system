import json

from app.schemas.review import HumanReviewRecord
from app.storage.human_review_repository import append_human_review
from scripts.export_responsible_ai_evidence import build_evidence_package


def test_responsible_ai_evidence_package_builds(tmp_path):
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir(parents=True)

    dataset_audit = {
        "sample_size": 2,
        "triage_input_fields": ["stay_id", "chiefcomplaint", "temperature"],
        "retrospective_label_fields": ["original_acuity", "disposition"],
    }

    missing_inputs = {
        "sample_size": 2,
        "cases_with_missing_triage_inputs": 1,
        "missing_case_percent": 50.0,
        "missing_cases": [
            {
                "stay_id": 30115213,
                "subject_id": 10010867,
                "chiefcomplaint": "MVC/INTUBATED TRAUMA",
                "missing_fields": ["temperature"],
            }
        ],
    }

    schema_report = {
        "tables": {
            "triage": {
                "columns": ["subject_id", "stay_id", "temperature"],
            }
        }
    }

    with (processed_dir / "dataset_audit_report.json").open("w", encoding="utf-8") as f:
        json.dump(dataset_audit, f)

    with (processed_dir / "missing_triage_inputs_report.json").open("w", encoding="utf-8") as f:
        json.dump(missing_inputs, f)

    with (processed_dir / "schema_report.json").open("w", encoding="utf-8") as f:
        json.dump(schema_report, f)

    review_record = HumanReviewRecord(
        review_id="test-review-1",
        stay_id=30115213,
        reviewer_role="researcher",
        review_status="request_missing_data",
        review_comment="Vitals missing.",
        created_at_utc="2026-06-09T16:20:00+00:00",
    )

    append_human_review(processed_dir / "human_reviews.jsonl", review_record)

    package = build_evidence_package(processed_dir)

    assert package["package_name"] == "Responsible AI Evidence Package"
    assert package["system_name"] == "AI Triage Agentic System"
    assert package["dataset"] == "MIMIC-IV-ED Demo v2.2"
    assert package["clinical_use_status"] == "not_for_clinical_use"
    assert package["governance_verdict"] == "NOT_READY_FOR_CLINICAL_USE"

    assert package["workflow_status"]["automated_clinical_triage_enabled"] is False
    assert package["workflow_status"]["manchester_rules_configured"] is False

    assert package["human_review_summary"]["review_record_count"] == 1
    assert package["human_review_summary"]["reviewed_missing_stay_count"] == 1
    assert package["human_review_summary"]["unreviewed_missing_stay_ids"] == []

    assert "No clinician-approved Manchester triage ruleset configured." in package["blocking_issues"]