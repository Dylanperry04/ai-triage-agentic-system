from fastapi.testclient import TestClient

from app.main import app


def print_check(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}")
    if detail:
        print(f"       {detail}")


def main():
    client = TestClient(app)

    print("\nAI TRIAGE DEMO READINESS CHECK")
    print("=" * 40)

    health = client.get("/health")
    print_check(
        "Health endpoint",
        health.status_code == 200,
        f"status_code={health.status_code}",
    )

    if health.status_code == 200:
        health_body = health.json()
        print_check(
            "Clinical-use guardrail",
            health_body.get("clinical_use") == "not_for_clinical_use",
            f"clinical_use={health_body.get('clinical_use')}",
        )
        print_check(
            "Manchester rules blocked",
            health_body.get("rules_status") == "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED",
            f"rules_status={health_body.get('rules_status')}",
        )

    cases = client.get("/triage/cases")
    cases_ok = cases.status_code == 200 and len(cases.json()) > 0
    print_check(
        "Processed triage cases available",
        cases_ok,
        f"case_count={len(cases.json()) if cases.status_code == 200 else 'unknown'}",
    )

    missing = client.get("/audit/missing-triage-inputs")
    if missing.status_code == 200:
        missing_body = missing.json()
        print_check(
            "Missing-data audit available",
            True,
            f"missing_cases={missing_body.get('cases_with_missing_triage_inputs')}",
        )
    else:
        print_check(
            "Missing-data audit available",
            False,
            f"status_code={missing.status_code}",
        )

    queue = client.get("/review/queue")
    if queue.status_code == 200:
        queue_body = queue.json()
        print_check(
            "Human review queue available",
            True,
            f"total={queue_body.get('total_missing_cases')}, reviewed={queue_body.get('reviewed_count')}, needs_review={queue_body.get('needs_review_count')}",
        )
    else:
        print_check(
            "Human review queue available",
            False,
            f"status_code={queue.status_code}",
        )

    governance = client.get("/governance/report")
    if governance.status_code == 200:
        governance_body = governance.json()
        print_check(
            "Governance report available",
            True,
            f"verdict={governance_body.get('governance_verdict')}",
        )

        print_check(
            "Governance verdict blocks clinical use",
            governance_body.get("governance_verdict") == "NOT_READY_FOR_CLINICAL_USE",
            "Expected while Manchester rules are not configured.",
        )
    else:
        print_check(
            "Governance report available",
            False,
            f"status_code={governance.status_code}",
        )

    print("=" * 40)
    print("Demo readiness check complete.\n")


if __name__ == "__main__":
    main()