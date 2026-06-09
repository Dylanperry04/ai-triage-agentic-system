from app.config import settings
from app.storage.jsonl_repository import read_jsonl
from app.schemas.internal import EDTriageCase
from app.agents.orchestrator import run_workflow


if __name__ == "__main__":
    records = read_jsonl(settings.processed_dir / "triage_cases_sample.jsonl")
    if not records:
        raise RuntimeError("No processed cases found. Run scripts/build_sample_cases.py first.")

    case = EDTriageCase(**records[0])
    result = run_workflow(case)
    print(result.model_dump_json(indent=2))
