import json
from fastapi import APIRouter, HTTPException

from app.config import settings

router = APIRouter()


@router.get("/audit/dataset-report")
def get_dataset_audit_report():
    path = settings.processed_dir / "dataset_audit_report.json"

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="dataset_audit_report.json not found. Run: python scripts\\audit_processed_sample.py",
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/audit/missing-triage-inputs")
def get_missing_triage_inputs_report():
    path = settings.processed_dir / "missing_triage_inputs_report.json"

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="missing_triage_inputs_report.json not found. Run: python scripts\\inspect_missing_triage_inputs.py",
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)