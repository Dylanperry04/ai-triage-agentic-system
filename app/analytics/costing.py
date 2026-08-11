"""Configurable Azure runtime and token-cost estimates.

Pricing is intentionally data-driven. The app may show scenario estimates only
when rates are supplied in config/cost_assumptions.json; otherwise it reports
that pricing is pending confirmation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_SCENARIOS = {
    "demo_pilot": {"calls_per_month": 500, "input_tokens_per_call": 1500, "output_tokens_per_call": 500},
    "low_usage": {"calls_per_month": 2_000, "input_tokens_per_call": 1500, "output_tokens_per_call": 500},
    "medium_usage": {"calls_per_month": 20_000, "input_tokens_per_call": 1500, "output_tokens_per_call": 500},
    "high_usage": {"calls_per_month": 100_000, "input_tokens_per_call": 1500, "output_tokens_per_call": 500},
}


def monthly_llm_cost(
    *,
    calls_per_month: float,
    input_tokens_per_call: float,
    output_tokens_per_call: float,
    input_rate_per_1m: float | None,
    output_rate_per_1m: float | None,
) -> float | None:
    if input_rate_per_1m is None or output_rate_per_1m is None:
        return None
    return calls_per_month * (
        (input_tokens_per_call / 1_000_000.0 * input_rate_per_1m)
        + (output_tokens_per_call / 1_000_000.0 * output_rate_per_1m)
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    return _repo_root() / "config" / "cost_assumptions.json"


def load_cost_assumptions(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or default_config_path()
    if not cfg_path.exists():
        return {
            "model_name": "ChatGPT 5.6 / latest frontier model assumption",
            "pricing_pending_confirmation": True,
            "input_token_rate_per_1m": None,
            "output_token_rate_per_1m": None,
            "scenarios": DEFAULT_SCENARIOS,
            "source_file": str(cfg_path),
        }
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data.setdefault("scenarios", DEFAULT_SCENARIOS)
    data["source_file"] = str(cfg_path)
    data["pricing_pending_confirmation"] = (
        data.get("input_token_rate_per_1m") is None
        or data.get("output_token_rate_per_1m") is None
    )
    return data


def build_cost_estimate(assumptions: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = dict(assumptions or load_cost_assumptions())
    input_rate = data.get("input_token_rate_per_1m")
    output_rate = data.get("output_token_rate_per_1m")
    pricing_pending = bool(
        data.get("pricing_pending_confirmation")
        or input_rate is None
        or output_rate is None
    )
    scenarios = data.get("scenarios") or DEFAULT_SCENARIOS
    scenario_rows = []
    for name, scenario in scenarios.items():
        row = {
            "scenario": name,
            "calls_per_month": float(scenario.get("calls_per_month") or 0),
            "input_tokens_per_call": float(scenario.get("input_tokens_per_call") or 0),
            "output_tokens_per_call": float(scenario.get("output_tokens_per_call") or 0),
        }
        row["monthly_llm_cost"] = monthly_llm_cost(
            calls_per_month=row["calls_per_month"],
            input_tokens_per_call=row["input_tokens_per_call"],
            output_tokens_per_call=row["output_tokens_per_call"],
            input_rate_per_1m=input_rate,
            output_rate_per_1m=output_rate,
        )
        row["pricing_status"] = (
            "estimated" if row["monthly_llm_cost"] is not None else "pricing_pending_confirmation"
        )
        scenario_rows.append(row)
    hosting = data.get("hosting") or {
        "service": "Azure App Service Basic B1",
        "region": "Sweden Central",
        "instance_count": 1,
        "vcpu": 1,
        "memory_gb": 1.75,
        "monthly_rate": None,
    }
    storage = data.get("storage") or {
        "tabular_data_gb": None,
        "image_upload_gb": None,
        "monthly_storage_rate_per_gb": None,
    }
    retraining = data.get("retraining") or {
        "monthly_retraining_runs": 1,
        "gpu_hours_per_run": None,
        "gpu_hour_rate": None,
        "pricing_pending_confirmation": True,
    }
    return {
        "model_name": data.get("model_name") or "pricing assumption label not supplied",
        "pricing_pending_confirmation": pricing_pending,
        "input_token_rate_per_1m": input_rate,
        "output_token_rate_per_1m": output_rate,
        "formula": (
            "calls_per_month * ((input_tokens_per_call / 1_000_000 * input_rate_per_1m) "
            "+ (output_tokens_per_call / 1_000_000 * output_rate_per_1m))"
        ),
        "hosting": hosting,
        "storage": storage,
        "retraining": retraining,
        "scenarios": scenario_rows,
        "assumptions_source_file": data.get("source_file"),
        "note": "Research estimate only. Rates must be confirmed against current Azure/OpenAI pricing before budgeting.",
    }
