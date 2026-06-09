from app.schemas.review import HumanReviewRecord
from app.storage.human_review_repository import (
    append_human_review,
    get_reviews_for_stay,
)


def test_human_review_can_be_saved_and_loaded(tmp_path):
    path = tmp_path / "human_reviews.jsonl"

    record = HumanReviewRecord(
        review_id="test-review-1",
        stay_id=123,
        reviewer_role="researcher",
        review_status="request_missing_data",
        review_comment="Vitals missing.",
        created_at_utc="2026-06-09T16:20:00+00:00",
    )

    append_human_review(path, record)

    records = get_reviews_for_stay(path, 123)

    assert len(records) == 1
    assert records[0].stay_id == 123
    assert records[0].review_status == "request_missing_data"
    assert records[0].review_comment == "Vitals missing."