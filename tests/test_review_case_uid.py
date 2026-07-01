"""#11/#28/#41/#42: human review records carry case_uid; lookup is dataset-safe."""
import tempfile
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from app.schemas.review import HumanReviewRecord
from app.storage.human_review_repository import (
    append_human_review, get_reviews_for_case_uid, get_reviews_for_stay,
)


def _rec(stay_id, source_dataset, status="REQUEST_MORE_INFORMATION"):
    return HumanReviewRecord(
        review_id=str(uuid4()), stay_id=stay_id, source_dataset=source_dataset,
        reviewer_role="researcher", review_status=status, review_comment="x",
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def test_case_uid_property():
    r = _rec(123, "MIMIC-IV-ED-Demo-v2.2")
    assert r.case_uid.startswith("MIMIC-IV-ED-Demo-v2.2~")
    assert "123" not in r.case_uid
    assert _rec(5, "Kaggle-KTAS").case_uid.startswith("Kaggle-KTAS~")
    assert _rec(5, None).case_uid.startswith("UNKNOWN~")


def test_lookup_does_not_confuse_same_stay_id_across_datasets():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "human_reviews.jsonl"
        # Same stay_id 999 in BOTH datasets.
        append_human_review(path, _rec(999, "MIMIC-IV-ED-Demo-v2.2", "ACCEPTED_AS_PRESENTED"))
        append_human_review(path, _rec(999, "Kaggle-KTAS", "REJECTED_DATA_QUALITY"))
        # case_uid lookup keeps them separate (pseudonymous, dataset-scoped).
        from app.schemas.workflow_run import make_case_uid
        mimic = get_reviews_for_case_uid(path, make_case_uid("MIMIC-IV-ED-Demo-v2.2", 999))
        ktas = get_reviews_for_case_uid(path, make_case_uid("Kaggle-KTAS", 999))
        assert len(mimic) == 1 and mimic[0].review_status == "ACCEPTED_AS_PRESENTED"
        assert len(ktas) == 1 and ktas[0].review_status == "REJECTED_DATA_QUALITY"
        # stay-based lookup now derives the pseudonymous uid per dataset, so it is
        # also dataset-scoped (returns 1, not 2) — the safe behaviour.
        assert len(get_reviews_for_stay(path, 999, "MIMIC-IV-ED-Demo-v2.2")) == 1


def test_mimic_review_save_record_has_required_fields():
    # #41: a saved MIMIC review carries source_dataset + case_uid + stay_id etc.
    r = _rec(37887480, "MIMIC-IV-ED-Demo-v2.2")
    d = r.model_dump(mode="json")
    assert d["source_dataset"] == "MIMIC-IV-ED-Demo-v2.2"
    assert d["stay_id"] == 37887480
    assert r.case_uid.startswith("MIMIC-IV-ED-Demo-v2.2~")
    assert "37887480" not in r.case_uid
    assert d["reviewer_role"] and d["review_status"] and d["created_at_utc"]
