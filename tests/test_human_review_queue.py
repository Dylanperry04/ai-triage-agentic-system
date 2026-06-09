import json

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas.review import HumanReviewRecord
from app.storage.human_review_repository import append_human_review


def test_human_review_queue_tracks_reviewed_and_unreviewed_cases(tmp_path, monkeypatch):
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir(parents=True)

    monkeypatch.setattr(settings, "processed_dir", processed_dir)

    triage_cases = [
        {"stay_id": 30115213, "subject_id": 10010867},
        {"stay_id": 30272878, "subject_id": 10038999},
    ]

    with (processed_dir / "triage_cases_sample.jsonl").open("w", encoding="utf-8") as f:
        for case in triage_cases:
            f.write(json.dumps(case))
            f.write("\n")

    missing_report = {
        "sample_size": 2,
        "cases_with_missing_triage_inputs": 2,
        "missing_case_percent": 100.0,
        "missing_cases": [
            {
                "stay_id": 30115213,
                "subject_id": 10010867,
                "chiefcomplaint": "MVC/INTUBATED TRAUMA",
                "missing_fields": ["temperature", "heartrate"],
            },
            {
                "stay_id": 30272878,
                "subject_id": 10038999,
                "chiefcomplaint": "Transfer",
                "missing_fields": ["temperature"],
            },
        ],
    }

    with (processed_dir / "missing_triage_inputs_report.json").open("w", encoding="utf-8") as f:
        json.dump(missing_report, f)

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
    response = client.get("/review/queue")

    assert response.status_code == 200

    body = response.json()

    assert body["queue_name"] == "Missing triage input human review queue"
    assert body["total_missing_cases"] == 2
    assert body["reviewed_count"] == 1
    assert body["needs_review_count"] == 1

    items_by_stay = {item["stay_id"]: item for item in body["items"]}

    assert items_by_stay[30115213]["review_status"] == "reviewed"
    assert items_by_stay[30115213]["review_count"] == 1
    assert items_by_stay[30272878]["review_status"] == "needs_review"
    assert items_by_stay[30272878]["review_count"] == 0