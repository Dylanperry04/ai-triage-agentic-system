"""
Summarises the agent audit log.

Input:
data/processed/agent_audit_log.jsonl

Output:
data/processed/agent_audit_summary.json
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from app.governance.audit_summary import summarise_agent_audit_file


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]

    input_path = project_root / "data" / "processed" / "agent_audit_log.jsonl"
    output_path = project_root / "data" / "processed" / "agent_audit_summary.json"

    summary = summarise_agent_audit_file(input_path)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path),
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "summary": summary,
    }

    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nAGENT AUDIT SUMMARY")
    print("=" * 40)
    print(json.dumps(report, indent=2))
    print("=" * 40)
    print(f"Audit summary written to: {output_path}")
    print("No clinical triage category was assigned.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())