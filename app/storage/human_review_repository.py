from pathlib import Path
from typing import List
import orjson

from app.schemas.review import HumanReviewRecord


def append_human_review(path: Path, record: HumanReviewRecord) -> None:
    """Persist a clinician-review record via the guarded writer (redacts
    identifiers incl. stay_id; keeps pseudonymous case_uid; refuses local JSONL /
    fails closed in patient-data mode)."""
    from app.storage.guarded_writer import write_record
    write_record(path, record.model_dump(mode="json"), record_kind="human_review")


def read_human_reviews(path: Path) -> List[HumanReviewRecord]:
    if not path.exists():
        return []

    records: List[HumanReviewRecord] = []

    with path.open("rb") as f:
        for line in f:
            if line.strip():
                records.append(HumanReviewRecord(**orjson.loads(line)))

    return records


def get_reviews_for_stay(path: Path, stay_id: int,
                         source_dataset: str | None = None) -> List[HumanReviewRecord]:
    """Deprecated-style lookup kept for back-compat. stay_id is no longer stored
    on disk (it is redacted), so this derives the pseudonymous case_uid and
    matches on that. Prefer get_reviews_for_case_uid."""
    from app.security.redaction import pseudonymous_case_uid
    target = pseudonymous_case_uid(source_dataset, stay_id)
    return [r for r in read_human_reviews(path) if r.case_uid == target]


def get_reviews_for_case_uid(path: Path, case_uid: str) -> List[HumanReviewRecord]:
    """Dataset-safe review lookup. Matches on the dataset-qualified case_uid
    (source_dataset:stay_id) so two cases with the same stay_id from different
    datasets are never confused. Records that predate dataset tracking
    (source_dataset=None) only match when the requested case_uid is itself
    UNKNOWN-qualified."""
    return [
        record
        for record in read_human_reviews(path)
        if record.case_uid == case_uid
    ]