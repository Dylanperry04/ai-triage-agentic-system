import json
from fastapi import APIRouter, Depends, HTTPException
from app.api.auth_dependencies import requires
from app.security import authz

from app.config import settings
from app.schemas.review import HumanReviewRequest

router = APIRouter()


def review_log_path():
    return settings.processed_dir / "human_reviews.jsonl"


def valid_stay_ids() -> set[int]:
    return set(valid_stay_id_to_dataset().keys())


def valid_stay_id_to_dataset() -> dict[int, str]:
    """Returns a stay_id -> source_dataset mapping for the legacy raw-stay_id
    review endpoint. Full-MIMIC-only: the mapping is built from the canonical
    full-MIMIC resolver (which itself fails closed without MIMIC_FULL_ED_DIR), not
    from any demo/KTAS sample. Returns {} when no full-MIMIC cases are available
    (the legacy endpoint then rejects the stay_id, which is correct fail-closed
    behaviour). The canonical review API is /cases/{case_uid}/reviews."""
    from app.api.case_resolver import _load_dataset_cases
    records = _load_dataset_cases("mimic_full")
    mapping: dict[int, str] = {}
    for record in records:
        sid = record.get("stay_id")
        if sid is None and record.get("edstay"):
            sid = record["edstay"].get("stay_id")
        if sid is not None:
            mapping[int(sid)] = record.get("source_dataset", "MIMIC-IV-ED-Full-v2.2")
    return mapping


def read_missing_triage_inputs_report() -> dict:
    path = settings.processed_dir / "missing_triage_inputs_report.json"

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Missing triage inputs report not found. Run: python scripts\\inspect_missing_triage_inputs.py",
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _gone():
    raise HTTPException(
        status_code=410,
        detail=(
            "Legacy raw-stay_id review routes are retired. Use the canonical "
            "case_uid API: /cases/{case_uid}/reviews."
        ),
    )


@router.post("/review/submit", dependencies=[Depends(requires(authz.PERM_SUBMIT_REVIEW, "submit_review"))])
def submit_human_review(request: HumanReviewRequest):
    _gone()


@router.get("/review/by-stay/{stay_id}", dependencies=[Depends(requires(authz.PERM_VIEW_CASE, "view_reviews"))])
def get_human_reviews_for_stay(stay_id: int):
    _gone()


@router.get("/review/queue", dependencies=[Depends(requires(authz.PERM_VIEW_CASE, "view_review_queue"))])
def get_human_review_queue():
    _gone()
