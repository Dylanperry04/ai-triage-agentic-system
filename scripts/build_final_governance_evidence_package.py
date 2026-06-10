"""
Builds the final governance evidence package for the current research prototype.

Output:
data/processed/final_governance_evidence_package.json

This package is evidence for research governance review only.
It does not approve clinical use.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from app.governance.evidence_package import build_final_governance_evidence_package


DEFAULT_BASE_URL = "https://ai-triage-agentic-system-afcmdbdpcsana4h3.swedencentral-01.azurewebsites.net"


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL

    package = build_final_governance_evidence_package(
        project_root=project_root,
        base_url=base_url,
    )

    output_path = (
        project_root
        / "data"
        / "processed"
        / "final_governance_evidence_package.json"
    )

    output_path.write_text(json.dumps(package, indent=2), encoding="utf-8")

    status = package["evidence_status"]

    print("\nFINAL GOVERNANCE EVIDENCE PACKAGE")
    print("=" * 50)
    print(f"Output path: {output_path}")
    print(f"Overall status: {status['overall_evidence_package_status']}")
    print(f"All required controls passed: {status['all_required_controls_passed']}")
    print(f"Clinical use allowed: {package['clinical_use_allowed']}")
    print(
        "Automated Manchester triage allowed: "
        f"{package['automated_manchester_triage_allowed']}"
    )
    print(f"Manchester category assigned: {package['manchester_category_assigned']}")
    print("=" * 50)

    print("\nRequired controls:")
    for control_name, passed in status["required_controls"].items():
        result = "PASS" if passed else "FAIL"
        print(f"[{result}] {control_name}")

    print("\nNo clinical safety claim is made by this package.")
    print("No clinical triage category was assigned.\n")

    if not status["all_required_controls_passed"]:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())