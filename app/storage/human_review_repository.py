from pathlib import Path
from typing import List
import orjson

from app.schemas.review import HumanReviewRecord


def append_human_review(path: Path, record: HumanReviewRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("ab") as f:
        f.write(orjson.dumps(record.model_dump(mode="json")))
        f.write(b"\n")


def read_human_reviews(path: Path) -> List[HumanReviewRecord]:
    if not path.exists():
        return []

    records: List[HumanReviewRecord] = []

    with path.open("rb") as f:
        for line in f:
            if line.strip():
                records.append(HumanReviewRecord(**orjson.loads(line)))

    return records


def get_reviews_for_stay(path: Path, stay_id: int) -> List[HumanReviewRecord]:
    return [
        record
        for record in read_human_reviews(path)
        if record.stay_id == stay_id
    ]