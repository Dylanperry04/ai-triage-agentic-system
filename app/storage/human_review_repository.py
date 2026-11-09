from pathlib import Path
from typing import List
from pydantic import ValidationError

from app.schemas.review import HumanReviewRecord


def append_human_review(path: Path, record: HumanReviewRecord) -> None:
    """Persist a clinician-review record via the guarded writer (redacts
    identifiers incl. stay_id; keeps pseudonymous case_uid; refuses local JSONL /
    fails closed in patient-data mode)."""
    from app.storage.guarded_writer import write_record
    write_record(path, record.model_dump(mode="json"), record_kind="human_review")


def read_human_reviews(path: Path, *, strict: bool = False) -> List[HumanReviewRecord]:
    if not path.exists():
        return []

    from app.storage.jsonl_io import read_jsonl_models

    return read_jsonl_models(path, HumanReviewRecord, strict=strict)


def read_review_for_action(
    path: Path, *, review_id: str, case_uid: str, action_id: str,
) -> HumanReviewRecord | None:
    """Read a review event before replaying its transition."""
    from app.security.identity import patient_data_mode
    from app.security.local_paths import credentialed_artifact_path
    from app.storage.jsonl_io import EvidenceIntegrityError, iter_jsonl_dicts

    path = credentialed_artifact_path(path, purpose="review replay")
    if patient_data_mode():
        from app.security.audit_sink import _from_table_entity, get_audit_sink
        from app.storage.case_state_repository import _is_not_found_exception
        from app.storage.guarded_writer import PatientDataStorageError

        client = getattr(get_audit_sink(path), "client", None)
        if client is None or not callable(getattr(client, "get_entity", None)):
            raise PatientDataStorageError("Durable review replay requires point reads.")
        try:
            row = client.get_entity(partition_key="human_review", row_key=review_id)
        except Exception as exc:
            if _is_not_found_exception(exc):
                return None
            raise PatientDataStorageError("Durable review point read failed.") from exc
        return _validate_review(_from_table_entity(dict(row)))

    matches = [
        row for row in iter_jsonl_dicts(path, strict=True)
        if row.get("review_id") == review_id
        or (row.get("case_uid") == case_uid and row.get("action_id") == action_id)
    ]
    if len(matches) > 1:
        raise EvidenceIntegrityError("Duplicate review identity in review evidence.")
    return _validate_review(matches[0]) if matches else None


def _validate_review(row: dict) -> HumanReviewRecord:
    from app.storage.jsonl_io import EvidenceIntegrityError

    try:
        return HumanReviewRecord.model_validate(row)
    except ValidationError:
        raise EvidenceIntegrityError("Invalid review schema in review evidence.") from None


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
