"""
AutoGen-based clinician chat team for the KTAS triage workflow.

Two modes:
  --mode deterministic (default) -- runs the deterministic pipeline only,
      no LLM involved. This was the only mode before AutoGen was wired in,
      and remains useful on its own as a safe baseline / sanity check.
  --mode chat --stay-id N --question "..." -- runs the real AutoGen
      AssistantAgent (app/agents/autogen_team.py) against Azure OpenAI to
      answer one question about one case. Requires Azure OpenAI credentials
      in .env; if absent, this prints a clear NOT_CONFIGURED message rather
      than failing with a confusing stack trace.

In both modes, deterministic Python remains the source of truth for
validation, safety review, the rules engine, and ML inference. The AutoGen
layer only explains already-computed evidence; see app/agents/autogen_team.py
for the full design rationale.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.storage.jsonl_repository import read_jsonl
from app.schemas.internal import EDTriageCase
from app.agents.orchestrator import run_workflow
from app.agents.autogen_team import run_single_question


def run_deterministic_team(stay_id: int | None = None) -> dict:
    """Run the deterministic workflow as the safe baseline for an AutoGen team."""
    cases_path = settings.processed_dir / "triage_cases_sample.jsonl"
    if not cases_path.exists():
        raise FileNotFoundError("Run python scripts/run_ktas_pipeline.py first.")
    records = read_jsonl(cases_path)
    if not records:
        raise RuntimeError("No processed cases found")
    chosen = records[0] if stay_id is None else next((r for r in records if int(r["stay_id"]) == stay_id), None)
    if chosen is None:
        raise ValueError(f"stay_id not found: {stay_id}")
    result = run_workflow(EDTriageCase(**chosen), include_llm_explanation=False)
    return {
        "orchestration_mode": "deterministic_safe_baseline_for_autogen",
        "stay_id": result.stay_id,
        "data_validation": result.data_validation.model_dump(mode="json"),
        "safety_review": result.safety_review.model_dump(mode="json"),
        "rules_engine": result.decision.model_dump(mode="json"),
        "ml_research_estimate": result.ml_prediction.model_dump(mode="json"),
        "policy": "No autonomous clinical decision. No Manchester mapping. Human review required.",
    }


def run_chat_demo(question: str) -> dict:
    """Run the real AutoGen chat agent against the configured Azure OpenAI deployment."""
    cases_path = settings.processed_dir / "triage_cases_sample.jsonl"
    if not cases_path.exists():
        raise FileNotFoundError("Run python scripts/run_ktas_pipeline.py first.")
    return asyncio.run(run_single_question(question, cases_path=cases_path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["deterministic", "chat"], default="deterministic")
    parser.add_argument("--stay-id", type=int, default=None, help="Used in deterministic mode.")
    parser.add_argument(
        "--question", type=str, default="Summarise stay 1 for me.",
        help="Used in chat mode. Reference a stay_id in the question, e.g. 'Tell me about stay 1'.",
    )
    args = parser.parse_args()

    if args.mode == "deterministic":
        output = run_deterministic_team(args.stay_id)
    else:
        output = run_chat_demo(args.question)

    print(json.dumps(output, indent=2, default=str))

