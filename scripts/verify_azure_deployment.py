import sys
import requests


DEFAULT_BASE_URL = "https://ai-triage-agentic-system-afcmdbdpcsana4h3.swedencentral-01.azurewebsites.net"


ENDPOINTS = [
    "/health",
    "/triage/cases",
    "/audit/dataset-report",
    "/audit/missing-triage-inputs",
    "/review/queue",
    "/governance/report",
]


def print_check(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}")
    if detail:
        print(f"       {detail}")


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    base_url = base_url.rstrip("/")

    print("\nAZURE DEPLOYMENT LIVE VERIFICATION")
    print("=" * 45)
    print(f"Base URL: {base_url}")
    print("=" * 45)

    all_passed = True

    for endpoint in ENDPOINTS:
        url = f"{base_url}{endpoint}"

        try:
            response = requests.get(url, timeout=30)
            passed = response.status_code == 200

            detail = f"status_code={response.status_code}"

            if endpoint == "/health" and passed:
                body = response.json()
                detail += f", clinical_use={body.get('clinical_use')}, rules_status={body.get('rules_status')}"

            if endpoint == "/review/queue" and passed:
                body = response.json()
                detail += f", total={body.get('total_missing_cases')}, reviewed={body.get('reviewed_count')}, needs_review={body.get('needs_review_count')}"

            if endpoint == "/governance/report" and passed:
                body = response.json()
                detail += f", verdict={body.get('governance_verdict')}"

            print_check(endpoint, passed, detail)
            all_passed = all_passed and passed

        except Exception as exc:
            print_check(endpoint, False, str(exc))
            all_passed = False

    print("=" * 45)

    if all_passed:
        print("Azure live deployment verification passed.")
    else:
        print("Azure live deployment verification failed.")

    print()


if __name__ == "__main__":
    main()