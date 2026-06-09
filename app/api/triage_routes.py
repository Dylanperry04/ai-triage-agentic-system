from fastapi import APIRouter, HTTPException

from app.config import settings
from app.storage.jsonl_repository import read_jsonl
from app.schemas.internal import EDTriageCase
from app.agents.orchestrator import run_workflow

router = APIRouter()


@router.get("/triage/cases")
def list_cases():
    path = settings.processed_dir / "triage_cases_sample.jsonl"
    records = read_jsonl(path)
    return [
        {
            "stay_id": r["stay_id"],
            "subject_id": r["subject_id"],
            "chiefcomplaint": r.get("triage", {}).get("chiefcomplaint") if r.get("triage") else None,
        }
        for r in records
    ]


@router.get("/triage/run/{stay_id}")
def run_case(stay_id: int):
    path = settings.processed_dir / "triage_cases_sample.jsonl"
    records = read_jsonl(path)

    for r in records:
        if int(r["stay_id"]) == stay_id:
            case = EDTriageCase(**r)
            return run_workflow(case).model_dump(mode="json")

    raise HTTPException(status_code=404, detail=f"stay_id not found: {stay_id}")
