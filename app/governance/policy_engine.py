"""
Policy-as-code checks for the AI Triage Agentic System.

These checks are deterministic governance checks.

They do not prove clinical safety.
They do not validate Manchester triage correctness.
They do not approve the system for clinical use.

They only verify that required prototype-stage safety controls remain active.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess
from typing import Any


EXPECTED_CLINICAL_USE = "not_for_clinical_use"
EXPECTED_RULES_STATUS = "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
EXPECTED_GOVERNANCE_VERDICT = "NOT_READY_FOR_CLINICAL_USE"


@dataclass
class PolicyCheckResult:
    policy_id: str
    name: str
    status: str
    details: str


def pass_check(policy_id: str, name: str, details: str) -> PolicyCheckResult:
    return PolicyCheckResult(policy_id, name, "PASS", details)


def fail_check(policy_id: str, name: str, details: str) -> PolicyCheckResult:
    return PolicyCheckResult(policy_id, name, "FAIL", details)


def warn_check(policy_id: str, name: str, details: str) -> PolicyCheckResult:
    return PolicyCheckResult(policy_id, name, "WARN", details)


def result_to_dict(result: PolicyCheckResult) -> dict[str, str]:
    return asdict(result)


def has_failures(results: list[PolicyCheckResult]) -> bool:
    return any(result.status == "FAIL" for result in results)


def summarise_results(results: list[PolicyCheckResult]) -> dict[str, int]:
    return {
        "pass": sum(1 for result in results if result.status == "PASS"),
        "warn": sum(1 for result in results if result.status == "WARN"),
        "fail": sum(1 for result in results if result.status == "FAIL"),
        "total": len(results),
    }


def git_tracked_files(project_root: Path) -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "ls-files"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return set()

    return {
        line.strip().replace("\\", "/")
        for line in completed.stdout.splitlines()
        if line.strip()
    }


def check_required_governance_files(project_root: Path) -> list[PolicyCheckResult]:
    required_files = [
        "docs/CLINICAL_SAFETY_GOVERNANCE_CHARTER.md",
        "docs/RAI_TOOLKIT_ALIGNMENT.md",
        "docs/CLINICAL_RULES_STATUS.md",
        "docs/DATA_LEAKAGE_POLICY.md",
        "docs/DATA_SCHEMA_VERIFIED.md",
    ]

    results: list[PolicyCheckResult] = []

    for relative_path in required_files:
        path = project_root / relative_path
        if path.exists():
            results.append(
                pass_check(
                    "GOV-DOC-001",
                    f"Required governance file exists: {relative_path}",
                    "File present.",
                )
            )
        else:
            results.append(
                fail_check(
                    "GOV-DOC-001",
                    f"Required governance file missing: {relative_path}",
                    "Required governance evidence is missing.",
                )
            )

    return results


def check_required_runtime_evidence_files(project_root: Path) -> list[PolicyCheckResult]:
    required_files = [
        "data/processed/triage_cases_sample.jsonl",
        "data/processed/dataset_audit_report.json",
        "data/processed/missing_triage_inputs_report.json",
        "data/processed/schema_report.json",
        "data/processed/responsible_ai_evidence_package.json",
        "data/processed/human_reviews.jsonl",
    ]

    results: list[PolicyCheckResult] = []

    for relative_path in required_files:
        path = project_root / relative_path
        if path.exists():
            results.append(
                pass_check(
                    "GOV-EVIDENCE-001",
                    f"Required runtime evidence file exists: {relative_path}",
                    "File present.",
                )
            )
        else:
            results.append(
                fail_check(
                    "GOV-EVIDENCE-001",
                    f"Required runtime evidence file missing: {relative_path}",
                    "Azure runtime and governance endpoints may fail without this file.",
                )
            )

    return results


def check_secret_exclusion(project_root: Path) -> list[PolicyCheckResult]:
    tracked = git_tracked_files(project_root)

    forbidden_tracked_paths = [
        ".env",
        ".venv",
        ".venv/",
    ]

    results: list[PolicyCheckResult] = []

    for forbidden in forbidden_tracked_paths:
        is_tracked = any(
            path == forbidden.rstrip("/") or path.startswith(forbidden.rstrip("/") + "/")
            for path in tracked
        )

        if is_tracked:
            results.append(
                fail_check(
                    "SEC-SECRET-001",
                    f"Forbidden secret/environment path is tracked: {forbidden}",
                    "Secrets or virtual environment files must not be committed.",
                )
            )
        else:
            results.append(
                pass_check(
                    "SEC-SECRET-001",
                    f"Forbidden secret/environment path is not tracked: {forbidden}",
                    "No tracked secret/environment path found.",
                )
            )

    gitignore_path = project_root / ".gitignore"
    gitignore_text = gitignore_path.read_text(encoding="utf-8", errors="ignore") if gitignore_path.exists() else ""

    if ".env" in gitignore_text:
        results.append(
            pass_check(
                "SEC-SECRET-002",
                ".env is listed in .gitignore",
                "Local Azure OpenAI keys should remain untracked.",
            )
        )
    else:
        results.append(
            fail_check(
                "SEC-SECRET-002",
                ".env is not listed in .gitignore",
                "Add .env to .gitignore before storing local keys.",
            )
        )

    return results


def check_dependency_separation(project_root: Path) -> list[PolicyCheckResult]:
    results: list[PolicyCheckResult] = []

    main_requirements = project_root / "requirements.txt"
    autogen_requirements = project_root / "requirements-autogen.txt"

    main_text = main_requirements.read_text(encoding="utf-8", errors="ignore") if main_requirements.exists() else ""
    autogen_text = autogen_requirements.read_text(encoding="utf-8", errors="ignore") if autogen_requirements.exists() else ""

    if "autogen-agentchat" in main_text or "autogen-ext" in main_text:
        results.append(
            fail_check(
                "DEP-001",
                "AutoGen dependencies are not in main requirements.txt",
                "AutoGen should remain outside the deployed FastAPI requirements until dependency conflicts are resolved.",
            )
        )
    else:
        results.append(
            pass_check(
                "DEP-001",
                "AutoGen dependencies are not in main requirements.txt",
                "Main Azure deployment dependencies remain isolated from AutoGen prototype dependencies.",
            )
        )

    if "autogen-agentchat" in autogen_text and "autogen-ext" in autogen_text:
        results.append(
            pass_check(
                "DEP-002",
                "AutoGen dependencies exist in requirements-autogen.txt",
                "AutoGen prototype has a separate requirements file.",
            )
        )
    else:
        results.append(
            fail_check(
                "DEP-002",
                "AutoGen dependencies missing from requirements-autogen.txt",
                "AutoGen prototype dependencies should be explicitly documented separately.",
            )
        )

    return results


def check_health_payload(payload: dict[str, Any]) -> list[PolicyCheckResult]:
    results: list[PolicyCheckResult] = []

    clinical_use = payload.get("clinical_use")
    rules_status = payload.get("rules_status")

    if clinical_use == EXPECTED_CLINICAL_USE:
        results.append(
            pass_check(
                "API-HEALTH-001",
                "Clinical use guardrail is active",
                f"clinical_use={clinical_use}",
            )
        )
    else:
        results.append(
            fail_check(
                "API-HEALTH-001",
                "Clinical use guardrail is active",
                f"Expected {EXPECTED_CLINICAL_USE}, got {clinical_use}",
            )
        )

    if rules_status == EXPECTED_RULES_STATUS:
        results.append(
            pass_check(
                "API-HEALTH-002",
                "Automated Manchester classification is blocked",
                f"rules_status={rules_status}",
            )
        )
    else:
        results.append(
            fail_check(
                "API-HEALTH-002",
                "Automated Manchester classification is blocked",
                f"Expected {EXPECTED_RULES_STATUS}, got {rules_status}",
            )
        )

    return results


def check_governance_payload(payload: dict[str, Any]) -> list[PolicyCheckResult]:
    results: list[PolicyCheckResult] = []

    verdict = payload.get("governance_verdict")
    blocking_issues = payload.get("blocking_issues", [])

    if verdict == EXPECTED_GOVERNANCE_VERDICT:
        results.append(
            pass_check(
                "API-GOV-001",
                "Governance verdict remains not ready for clinical use",
                f"governance_verdict={verdict}",
            )
        )
    else:
        results.append(
            fail_check(
                "API-GOV-001",
                "Governance verdict remains not ready for clinical use",
                f"Expected {EXPECTED_GOVERNANCE_VERDICT}, got {verdict}",
            )
        )

    if isinstance(blocking_issues, list) and blocking_issues:
        results.append(
            pass_check(
                "API-GOV-002",
                "Governance report contains blocking issues",
                f"blocking_issues_count={len(blocking_issues)}",
            )
        )
    else:
        results.append(
            fail_check(
                "API-GOV-002",
                "Governance report contains blocking issues",
                "Prototype-stage governance should not have zero blocking issues.",
            )
        )

    return results


def check_review_queue_payload(payload: dict[str, Any]) -> list[PolicyCheckResult]:
    results: list[PolicyCheckResult] = []

    total_missing = payload.get("total_missing_cases")
    needs_review = payload.get("needs_review_count")

    if isinstance(total_missing, int):
        results.append(
            pass_check(
                "API-REVIEW-001",
                "Human review queue exposes missing-case count",
                f"total_missing_cases={total_missing}",
            )
        )
    else:
        results.append(
            fail_check(
                "API-REVIEW-001",
                "Human review queue exposes missing-case count",
                f"Invalid total_missing_cases={total_missing}",
            )
        )

    if isinstance(needs_review, int) and needs_review >= 0:
        results.append(
            pass_check(
                "API-REVIEW-002",
                "Human review queue exposes needs-review count",
                f"needs_review_count={needs_review}",
            )
        )
    else:
        results.append(
            fail_check(
                "API-REVIEW-002",
                "Human review queue exposes needs-review count",
                f"Invalid needs_review_count={needs_review}",
            )
        )

    return results


def evaluate_local_policy_checks(project_root: Path) -> list[PolicyCheckResult]:
    results: list[PolicyCheckResult] = []
    results.extend(check_required_governance_files(project_root))
    results.extend(check_required_runtime_evidence_files(project_root))
    results.extend(check_secret_exclusion(project_root))
    results.extend(check_dependency_separation(project_root))
    return results


def evaluate_azure_policy_checks(base_url: str) -> list[PolicyCheckResult]:
    import requests

    base_url = base_url.rstrip("/")
    results: list[PolicyCheckResult] = []

    endpoint_checks = [
        ("/health", check_health_payload),
        ("/governance/report", check_governance_payload),
        ("/review/queue", check_review_queue_payload),
    ]

    for path, checker in endpoint_checks:
        url = f"{base_url}{path}"

        try:
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                results.append(
                    fail_check(
                        "API-ENDPOINT-001",
                        f"Endpoint reachable: {path}",
                        f"status_code={response.status_code}",
                    )
                )
                continue

            results.append(
                pass_check(
                    "API-ENDPOINT-001",
                    f"Endpoint reachable: {path}",
                    "status_code=200",
                )
            )
            results.extend(checker(response.json()))

        except Exception as exc:
            results.append(
                fail_check(
                    "API-ENDPOINT-001",
                    f"Endpoint reachable: {path}",
                    str(exc),
                )
            )

    return results