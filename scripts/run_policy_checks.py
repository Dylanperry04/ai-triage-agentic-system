"""
Runs local and Azure policy-as-code checks.

This script creates a governance evidence report at:
data/processed/policy_check_report.json
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from app.governance.policy_engine import (
    evaluate_azure_policy_checks,
    evaluate_local_policy_checks,
    has_failures,
    result_to_dict,
    summarise_results,
)


DEFAULT_BASE_URL = "https://ai-triage-agentic-system-afcmdbdpcsana4h3.swedencentral-01.azurewebsites.net"


def print_results(title: str, results):
    print(f"\n{title}")
    print("=" * len(title))

    for result in results:
        print(f"[{result.status}] {result.policy_id} - {result.name}")
        print(f"       {result.details}")


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL

    local_results = evaluate_local_policy_checks(project_root)
    azure_results = evaluate_azure_policy_checks(base_url)

    all_results = local_results + azure_results

    print("\nAI TRIAGE POLICY-AS-CODE CHECKS")
    print("=" * 40)
    print(f"Project root: {project_root}")
    print(f"Azure base URL: {base_url}")

    print_results("LOCAL REPOSITORY POLICY CHECKS", local_results)
    print_results("AZURE RUNTIME POLICY CHECKS", azure_results)

    summary = summarise_results(all_results)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy_check_version": "0.1",
        "clinical_safety_claim": "No clinical safety claim is made by these checks.",
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "base_url": base_url,
        "summary": summary,
        "results": [result_to_dict(result) for result in all_results],
    }

    output_path = project_root / "data" / "processed" / "policy_check_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nSUMMARY")
    print("=" * 40)
    print(json.dumps(summary, indent=2))
    print(f"\nPolicy check report written to: {output_path}")

    if has_failures(all_results):
        print("\nPolicy checks failed.")
        return 1

    print("\nPolicy checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())