import json

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas.review import HumanReviewRecord
from app.storage.human_review_repository import append_human_review


def test_governance_report_returns_evidence_package(tmp_path, monkeypatch):
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir(parents=True)

    monkeypatch.setattr(settings, "processed_dir", processed_dir)

    dataset_audit = {
        "sample_size": 2,
        "triage_input_fields": [
            "subject_id",
            "stay_id",
            "chiefcomplaint",
            "temperature",
            "heartrate",
            "resprate",
            "o2sat",
            "sbp",
            "dbp",
            "pain",
        ],
        "retrospective_label_fields": [
            "original_acuity",
            "disposition",
            "diagnosis_count",
        ],
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
                "missing_fields": [
                    "temperature",
                    "heartrate",
                    "resprate",
                    "o2sat",
                    "sbp",
                    "dbp",
                    "pain",
                ],
            }
        ],
    }

    schema_report = {
        "tables": {
            "edstays": {
                "row_count": 2,
                "columns": ["subject_id", "stay_id"],
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

    client = TestClient(app)
    response = client.get("/governance/report")

    assert response.status_code == 200

    body = response.json()

    assert body["system_name"] == "AI Triage Agentic System"
    assert body["dataset"] == "MIMIC-IV-ED Demo v2.2"
    assert body["clinical_use_status"] == "not_for_clinical_use"

    assert body["controls"]["dataset_loaded"]["status"] == "PASS"
    assert body["controls"]["schema_report_available"]["status"] == "PASS"
    assert body["controls"]["triage_input_separation"]["status"] == "PASS"
    assert body["controls"]["missing_data_visibility"]["status"] == "PASS"
    assert body["controls"]["human_review_for_missing_data"]["status"] == "PASS"
    assert body["controls"]["leakage_guard"]["status"] == "PASS"
    assert body["controls"]["human_review_audit_log"]["status"] == "PASS"
    assert body["controls"]["manchester_rules"]["status"] == "NOT_CONFIGURED"

    assert body["governance_verdict"] == "NOT_READY_FOR_CLINICAL_USE"
    assert "No clinician-approved Manchester triage ruleset configured." in body["blocking_issues"]