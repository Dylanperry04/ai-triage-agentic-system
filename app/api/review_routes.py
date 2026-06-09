import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas.review import HumanReviewRequest, HumanReviewRecord
from app.storage.jsonl_repository import read_jsonl
from app.storage.human_review_repository import (
    append_human_review,
    get_reviews_for_stay,
    read_human_reviews,
)

router = APIRouter()


def review_log_path():
    return settings.processed_dir / "human_reviews.jsonl"


def valid_stay_ids() -> set[int]:
    path = settings.processed_dir / "triage_cases_sample.jsonl"

    if not path.exists():
        raise HTTPException(
            status_code=500,
            detail="Processed cases not found. Run: python scripts\\build_sample_cases.py --n 100",
        )

    records = read_jsonl(path)
    return {int(record["stay_id"]) for record in records}


def read_missing_triage_inputs_report() -> dict:
    path = settings.processed_dir / "missing_triage_inputs_report.json"

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Missing triage inputs report not found. Run: python scripts\\inspect_missing_triage_inputs.py",
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@router.post("/review/submit")
def submit_human_review(request: HumanReviewRequest):
    if request.stay_id not in valid_stay_ids():
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stay_id: {request.stay_id}. Review can only be saved for a processed ED stay.",
        )

    record = HumanReviewRecord(
        review_id=str(uuid4()),
        stay_id=request.stay_id,
        reviewer_role=request.reviewer_role,
        review_status=request.review_status,
        review_comment=request.review_comment,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    append_human_review(review_log_path(), record)

    return {
        "status": "saved",
        "record": record.model_dump(mode="json"),
    }


@router.get("/review/by-stay/{stay_id}")
def get_human_reviews_for_stay(stay_id: int):
    records = get_reviews_for_stay(review_log_path(), stay_id)

    return {
        "stay_id": stay_id,
        "review_count": len(records),
        "reviews": [record.model_dump(mode="json") for record in records],
    }


@router.get("/review/queue")
def get_human_review_queue():
    missing_report = read_missing_triage_inputs_report()
    human_reviews = read_human_reviews(review_log_path())

    reviewed_stay_ids = {int(record.stay_id) for record in human_reviews}

    missing_cases = missing_report.get("missing_cases", [])

    queue = []

    for case in missing_cases:
        stay_id = int(case["stay_id"])
        case_reviews = [
            record.model_dump(mode="json")
            for record in human_reviews
            if int(record.stay_id) == stay_id
        ]

        queue.append(
            {
                "stay_id": stay_id,
                "subject_id": case.get("subject_id"),
                "chiefcomplaint": case.get("chiefcomplaint"),
                "missing_fields": case.get("missing_fields", []),
                "review_status": "reviewed" if stay_id in reviewed_stay_ids else "needs_review",
                "review_count": len(case_reviews),
                "reviews": case_reviews,
            }
        )

    reviewed_count = sum(1 for item in queue if item["review_status"] == "reviewed")
    needs_review_count = sum(1 for item in queue if item["review_status"] == "needs_review")

    return {
        "queue_name": "Missing triage input human review queue",
        "total_missing_cases": len(queue),
        "reviewed_count": reviewed_count,
        "needs_review_count": needs_review_count,
        "items": queue,
    }