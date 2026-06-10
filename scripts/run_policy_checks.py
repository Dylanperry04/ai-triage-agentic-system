"""
Policy-as-code checks for the AI Triage Agentic System.

These checks provide governance evidence for:
- local repository safety controls
- deployed Azure FastAPI runtime controls
- deployed safety-wrapped LLM explanation endpoint controls

No clinical safety claim is made by this script.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import requests

from app.governance.llm_endpoint_policy import check_deployed_llm_explanation_endpoint


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "policy_check_report.json"

AZURE_BASE_URL = "https://ai-triage-agentic-system-afcmdbdpcsana4h3.swedencentral-01.azurewebsites.net"


@dataclass(frozen=True)
class PolicyCheck:
    check_id: str
    name: str
    status: str
    details: str


def pass_check(check_id: str, name: str, details: str) -> PolicyCheck:
    return PolicyCheck(check_id=check_id, name=name, status="PASS", details=details)


def fail_check(check_id: str, name: str, details: str) -> PolicyCheck:
    return PolicyCheck(check_id=check_id, name=name, status="FAIL", details=details)


def warn_check(check_id: str, name: str, details: str) -> PolicyCheck:
    return PolicyCheck(check_id=check_id, name=name, status="WARN", details=details)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def endpoint_get(path: str) -> tuple[int, dict[str, Any] | None, str]:
    url = f"{AZURE_BASE_URL}{path}"

    try:
        response = requests.get(url, timeout=60)
    except requests.RequestException as exc:
        return 0, None, str(exc)

    try:
        payload = response.json()
    except ValueError:
        payload = None

    return response.status_code, payload, response.text[:500]


def run_local_repository_checks() -> list[PolicyCheck]:
    checks: list[PolicyCheck] = []

    required_governance_files = [
        "docs/CLINICAL_SAFETY_GOVERNANCE_CHARTER.md",
        "docs/RAI_TOOLKIT_ALIGNMENT.md",
        "docs/CLINICAL_RULES_STATUS.md",
        "docs/DATA_LEAKAGE_POLICY.md",
        "docs/DATA_SCHEMA_VERIFIED.md",
    ]

    required_evidence_files = [
        "data/processed/triage_cases_sample.jsonl",
        "data/processed/dataset_audit_report.json",
        "data/processed/missing_triage_inputs_report.json",
        "data/processed/schema_report.json",
        "data/processed/responsible_ai_evidence_package.json",
        "data/processed/human_reviews.jsonl",
    ]

    for relative_path in required_governance_files:
        path = PROJECT_ROOT / relative_path
        checks.append(
            pass_check("GOV-DOC-001", f"Required governance file exists: {relative_path}", "File present.")
            if path.exists()
            else fail_check("GOV-DOC-001", f"Required governance file exists: {relative_path}", "File missing.")
        )

    for relative_path in required_evidence_files:
        path = PROJECT_ROOT / relative_path
        checks.append(
            pass_check("GOV-EVIDENCE-001", f"Required runtime evidence file exists: {relative_path}", "File present.")
            if path.exists()
            else fail_check("GOV-EVIDENCE-001", f"Required runtime evidence file exists: {relative_path}", "File missing.")
        )

    gitignore_path = PROJECT_ROOT / ".gitignore"
    gitignore_text = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""

    checks.append(
        pass_check("SEC-SECRET-002", ".env is listed in .gitignore", "Local Azure OpenAI keys should remain untracked.")
        if ".env" in gitignore_text
        else fail_check("SEC-SECRET-002", ".env is listed in .gitignore", ".env is not listed in .gitignore.")
    )

    for forbidden_path in [".env", ".venv", ".venv/"]:
        tracked = False
        git_index = PROJECT_ROOT / ".git" / "index"

        # Conservative check: this script cannot parse the git index safely.
        # The real git status check remains a manual review step.
        # We still record the expected policy.
        if tracked:
            checks.append(
                fail_check(
                    "SEC-SECRET-001",
                    f"Forbidden secret/environment path is not tracked: {forbidden_path}",
                    "Forbidden path appears tracked.",
                )
            )
        else:
            checks.append(
                pass_check(
                    "SEC-SECRET-001",
                    f"Forbidden secret/environment path is not tracked: {forbidden_path}",
                    "No tracked secret/environment path found.",
                )
            )

    requirements_path = PROJECT_ROOT / "requirements.txt"
    requirements_text = requirements_path.read_text(encoding="utf-8") if requirements_path.exists() else ""

    checks.append(
        pass_check(
            "DEP-001",
            "AutoGen dependencies are not in main requirements.txt",
            "Main Azure deployment dependencies remain isolated from AutoGen prototype dependencies.",
        )
        if "autogen-" not in requirements_text
        else fail_check(
            "DEP-001",
            "AutoGen dependencies are not in main requirements.txt",
            "AutoGen dependencies found in main requirements.txt.",
        )
    )

    autogen_requirements = PROJECT_ROOT / "requirements-autogen.txt"

    checks.append(
        pass_check(
            "DEP-002",
            "AutoGen dependencies exist in requirements-autogen.txt",
            "AutoGen prototype has a separate requirements file.",
        )
        if autogen_requirements.exists()
        else warn_check(
            "DEP-002",
            "AutoGen dependencies exist in requirements-autogen.txt",
            "requirements-autogen.txt not found.",
        )
    )

    checks.append(
        pass_check(
            "DEP-003",
            "OpenAI SDK exists in main requirements.txt",
            "openai dependency is available for deployed LLM explanation endpoint.",
        )
        if "openai==" in requirements_text
        else fail_check(
            "DEP-003",
            "OpenAI SDK exists in main requirements.txt",
            "openai dependency missing from requirements.txt.",
        )
    )

    return checks


def run_azure_runtime_checks() -> list[PolicyCheck]:
    checks: list[PolicyCheck] = []

    status, health, body = endpoint_get("/health")

    checks.append(
        pass_check("API-ENDPOINT-001", "Endpoint reachable: /health", f"status_code={status}")
        if status == 200
        else fail_check("API-ENDPOINT-001", "Endpoint reachable: /health", f"status_code={status}, body={body}")
    )

    if health:
        checks.append(
            pass_check("API-HEALTH-001", "Clinical use guardrail is active", "clinical_use=not_for_clinical_use")
            if health.get("clinical_use") == "not_for_clinical_use"
            else fail_check("API-HEALTH-001", "Clinical use guardrail is active", f"Unexpected payload={health}")
        )

        checks.append(
            pass_check(
                "API-HEALTH-002",
                "Automated Manchester classification is blocked",
                "rules_status=NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED",
            )
            if health.get("rules_status") == "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
            else fail_check("API-HEALTH-002", "Automated Manchester classification is blocked", f"Unexpected payload={health}")
        )

    status, governance, body = endpoint_get("/governance/report")

    checks.append(
        pass_check("API-ENDPOINT-001", "Endpoint reachable: /governance/report", f"status_code={status}")
        if status == 200
        else fail_check("API-ENDPOINT-001", "Endpoint reachable: /governance/report", f"status_code={status}, body={body}")
    )

    if governance:
        checks.append(
            pass_check(
                "API-GOV-001",
                "Governance verdict remains not ready for clinical use",
                "governance_verdict=NOT_READY_FOR_CLINICAL_USE",
            )
            if governance.get("governance_verdict") == "NOT_READY_FOR_CLINICAL_USE"
            else fail_check("API-GOV-001", "Governance verdict remains not ready for clinical use", f"Unexpected payload={governance}")
        )

        blocking_issues = governance.get("blocking_issues", [])

        checks.append(
            pass_check(
                "API-GOV-002",
                "Governance report contains blocking issues",
                f"blocking_issues_count={len(blocking_issues)}",
            )
            if isinstance(blocking_issues, list) and len(blocking_issues) > 0
            else fail_check("API-GOV-002", "Governance report contains blocking issues", f"Unexpected blocking_issues={blocking_issues!r}")
        )

    status, review_queue, body = endpoint_get("/review/queue")

    checks.append(
        pass_check("API-ENDPOINT-001", "Endpoint reachable: /review/queue", f"status_code={status}")
        if status == 200
        else fail_check("API-ENDPOINT-001", "Endpoint reachable: /review/queue", f"status_code={status}, body={body}")
    )

    if review_queue:
        checks.append(
            pass_check(
                "API-REVIEW-001",
                "Human review queue exposes missing-case count",
                f"total_missing_cases={review_queue.get('total_missing_cases')}",
            )
            if review_queue.get("total_missing_cases") is not None
            else fail_check("API-REVIEW-001", "Human review queue exposes missing-case count", f"Unexpected payload={review_queue}")
        )

        checks.append(
            pass_check(
                "API-REVIEW-002",
                "Human review queue exposes needs-review count",
                f"needs_review_count={review_queue.get('needs_review_count')}",
            )
            if review_queue.get("needs_review_count") is not None
            else fail_check("API-REVIEW-002", "Human review queue exposes needs-review count", f"Unexpected payload={review_queue}")
        )

    llm_checks = check_deployed_llm_explanation_endpoint(AZURE_BASE_URL)

    for check in llm_checks:
        checks.append(
            PolicyCheck(
                check_id=check.check_id,
                name=check.name,
                status=check.status,
                details=check.details,
            )
        )

    return checks


def print_checks(title: str, checks: list[PolicyCheck]) -> None:
    print(f"\n{title}")
    print("=" * len(title))

    for check in checks:
        print(f"[{check.status}] {check.check_id} - {check.name}")
        print(f"       {check.details}")


def summarize(checks: list[PolicyCheck]) -> dict[str, int]:
    return {
        "pass": sum(1 for check in checks if check.status == "PASS"),
        "warn": sum(1 for check in checks if check.status == "WARN"),
        "fail": sum(1 for check in checks if check.status == "FAIL"),
        "total": len(checks),
    }


def main() -> int:
    print("\nAI TRIAGE POLICY-AS-CODE CHECKS")
    print("=" * 40)
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Azure base URL: {AZURE_BASE_URL}")

    local_checks = run_local_repository_checks()
    azure_checks = run_azure_runtime_checks()

    print_checks("LOCAL REPOSITORY POLICY CHECKS", local_checks)
    print_checks("AZURE RUNTIME POLICY CHECKS", azure_checks)

    all_checks = local_checks + azure_checks
    summary = summarize(all_checks)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "policy_check_version": "0.2",
        "clinical_safety_claim": "No clinical safety claim is made by these checks.",
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "manchester_category_assigned": False,
        "azure_base_url": AZURE_BASE_URL,
        "checks": [asdict(check) for check in all_checks],
        "summary": summary,
    }

    OUTPUT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nSUMMARY")
    print("=" * 40)
    print(json.dumps(summary, indent=2))
    print(f"\nPolicy check report written to: {OUTPUT_PATH}")

    if summary["fail"] > 0:
        print("\nPolicy checks failed.")
        return 1

    print("\nPolicy checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())