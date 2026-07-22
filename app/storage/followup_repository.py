"""
Storage for follow-up link declarations and their comparison results.

Follows the exact same append-only JSONL pattern as
app/storage/human_review_repository.py, kept as a separate file because
a follow-up link record and a clinician review record are conceptually
different things (a structural declaration plus a deterministic
comparison, versus a clinician's own opinion) and mixing them in one
file would make the audit trail harder to read, not easier.
"""
from pathlib import Path
from typing import List

from app.schemas.followup import FollowUpComparisonResult


def append_followup_comparison(path: Path, record: FollowUpComparisonResult) -> None:
    from app.storage.jsonl_io import append_jsonl

    append_jsonl(path, record.model_dump(mode="json"))


def read_followup_comparisons(path: Path) -> List[FollowUpComparisonResult]:
    if not path.exists():
        return []
    from app.storage.jsonl_io import read_jsonl_models

    return read_jsonl_models(path, FollowUpComparisonResult)


def get_followup_history_for_stay(path: Path, stay_id: int) -> List[FollowUpComparisonResult]:
    """
    Returns every comparison record where the given stay_id appears as
    either the previous or the new stay in a declared link, so a
    clinician can see a stay's full follow-up chain in either direction.
    """
    return [
        record
        for record in read_followup_comparisons(path)
        if record.previous_stay_id == stay_id or record.new_stay_id == stay_id
    ]
