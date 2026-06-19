"""
Triage API routes.

GET /triage/cases         — list all available cases
GET /triage/run/{stay_id} — run full workflow for one case (no LLM)
POST /triage/run/{stay_id}/explain — run full workflow + LLM explanation

DATASET PARAMETER (added during a later review pass): /triage/cases and
/triage/run/{stay_id} both accept an optional ?dataset= query parameter
("ktas", the default, or "mimic_demo"). Before this was added, MIMIC
cases were completely unreachable through this API at all -- both
routes only ever read triage_cases_sample.jsonl, which is KTAS-only by
construction (scripts/build_sample_cases.py's default scope), so a
stay_id lookup could only ever resolve a Kaggle-KTAS case, with no way
to ask for MIMIC. The default value ("ktas") preserves the exact
pre-existing behaviour and response shape for any caller that does not
pass this parameter, so this is purely additive, not a breaking change.

This is DELIBERATELY a bounded fix, not the larger case_uid =
source_dataset + ":" + stay_id redesign a more thorough pass would
eventually build across every route's URL scheme (which would be a
breaking change to the existing /triage/run/{int} convention, and a
genuinely separate, larger piece of work than this session's scope) --
within a single dataset, stay_id is still assumed unique, which holds
today for both real datasets (KTAS: 1-1267, MIMIC demo: 8-digit
30-million-range integers, confirmed non-overlapping by their current
real ranges, though not by any schema-level guarantee).
"""
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.storage.jsonl_repository import read_jsonl
from app.schemas.internal import EDTriageCase
from app.agents.orchestrator import run_workflow

router = APIRouter()

DatasetParam = Literal["ktas", "mimic_demo"]


def _load_cases_for_dataset(dataset: DatasetParam) -> list[dict]:
    """
    Returns a list of dict-shaped EDTriageCase records for the
    requested dataset. "ktas" reads the exact same
    triage_cases_sample.jsonl file the routes always read, preserving
    pre-existing behaviour exactly. "mimic_demo" loads live via the real
    adapter, the same pattern frontend/app.py's load_cases() already
    uses, rather than depending on any pre-built file existing for
    MIMIC (none does today).
    """
    if dataset == "ktas":
        path = settings.processed_dir / "triage_cases_sample.jsonl"
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail=(
                    "No processed cases found. "
                    "Run: python scripts/run_ktas_pipeline.py"
                ),
            )
        return read_jsonl(path)

    # dataset == "mimic_demo"
    from app.data_pipeline.mimic_adapter import load_mimic_demo_cases

    try:
        cases, _ = load_mimic_demo_cases(settings.raw_demo_dir)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                "MIMIC-IV-ED Demo files not found. "
                "Run: python scripts/download_mimic_ed_demo.py"
            ),
        )
    return [c.model_dump(mode="json") for c in cases]


@router.get("/triage/cases")
def list_cases(dataset: DatasetParam = Query("ktas")):
    """List all available processed triage cases for the requested dataset."""
    records = _load_cases_for_dataset(dataset)
    return [
        {
            "stay_id": r["stay_id"],
            "subject_id": r["subject_id"],
            "chiefcomplaint": (
                r.get("triage", {}).get("chiefcomplaint")
                if r.get("triage") else None
            ),
            "source_dataset": r.get("source_dataset"),
            "age": r.get("triage", {}).get("age") if r.get("triage") else None,
        }
        for r in records
    ]


def _find_case(stay_id: int, dataset: DatasetParam = "ktas") -> EDTriageCase:
    """Load a case by stay_id from the requested dataset's records."""
    records = _load_cases_for_dataset(dataset)
    for r in records:
        if int(r["stay_id"]) == stay_id:
            return EDTriageCase(**r)
    raise HTTPException(
        status_code=404,
        detail=f"stay_id not found in dataset={dataset!r}: {stay_id}",
    )


@router.get("/triage/run/{stay_id}")
def run_case(stay_id: int, dataset: DatasetParam = Query("ktas")):
    """Run the triage workflow for one case (deterministic agents only, no LLM)."""
    case = _find_case(stay_id, dataset)
    result = run_workflow(case, include_llm_explanation=False)
    return result.model_dump(mode="json")


@router.post("/triage/run/{stay_id}/explain")
def run_case_with_explanation(stay_id: int):
    """
    Run the full triage workflow including LLM Explanation Agent.
    Requires Azure OpenAI to be configured in .env.
    """
    case = _find_case(stay_id)
    result = run_workflow(case, include_llm_explanation=True)
    return result.model_dump(mode="json")
