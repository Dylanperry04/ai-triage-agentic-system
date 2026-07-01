"""Azure/runtime smoke test for the two-service app.

This checks live endpoints after deployment. It is intentionally separate from
unit tests: green pytest results do not prove Azure wiring, auth, audit, model
artefacts, or endpoint reachability.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Optional

import requests


BASE_ENDPOINTS = (
    ("GET", "/health", None),
    ("GET", "/runtime/status", None),
    ("GET", "/status/full-mimic", None),
    ("GET", "/status/llm", None),
    ("GET", "/security/status", None),
    ("GET", "/cases", None),
    ("GET", "/model/performance", None),
    ("GET", "/audit/events", None),
)


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text[:500]


def _call(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        resp = session.request(method, url, json=json_body, timeout=timeout)
        return {
            "status_code": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
            "body": _safe_json(resp),
        }
    except Exception as exc:
        return {
            "status_code": None,
            "ok": False,
            "body": f"{type(exc).__name__}: {exc}",
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a deployed triage app.")
    parser.add_argument("--base-url", required=True, help="Backend base URL.")
    parser.add_argument("--demo-role", default="", help="Optional X-Demo-Role header.")
    parser.add_argument("--case-uid", default="", help="Optional case_uid to exercise.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero on any non-2xx endpoint. Without this, auth-gated "
             "401/403 responses are reported but not treated as script failure.",
    )
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    session = requests.Session()
    if args.demo_role:
        session.headers.update({"X-Demo-Role": args.demo_role})

    results: Dict[str, Any] = {"base_url": base, "checks": {}}
    for method, path, body in BASE_ENDPOINTS:
        results["checks"][path] = _call(
            session, method, base + path, timeout=args.timeout, json_body=body)

    case_uid = args.case_uid
    cases_body = results["checks"].get("/cases", {}).get("body")
    if not case_uid and isinstance(cases_body, dict):
        cases = cases_body.get("cases") or []
        if cases:
            case_uid = cases[0].get("case_uid") or ""

    if case_uid:
        case_paths = {
            f"/cases/{case_uid}/assessments": ("POST", None),
            f"/cases/{case_uid}/multiagent-explanations": (
                "POST", {"question": "Smoke test: summarize already-computed evidence."}
            ),
            f"/cases/{case_uid}/followups": (
                "POST", {"updated_vitals": {"heartrate": 120}}
            ),
            f"/cases/{case_uid}/followups/multiagent-explanations": (
                "POST",
                {
                    "updated_vitals": {"heartrate": 120},
                    "question": "Smoke test: why did the follow-up result change or stay the same?",
                },
            ),
        }
        for path, (method, body) in case_paths.items():
            results["checks"][path] = _call(
                session, method, base + path, timeout=args.timeout, json_body=body)
    else:
        results["case_exercise_skipped"] = (
            "No case_uid supplied and /cases did not return an available case."
        )

    results["audit_write_read_note"] = (
        "Protected endpoint calls above should create access-audit records; "
        "/audit/events checks whether audit reads are reachable for the active role."
    )
    hard_failures = [
        path for path, item in results["checks"].items()
        if item.get("status_code") is None or int(item.get("status_code") or 0) >= 500
    ]
    strict_failures = [
        path for path, item in results["checks"].items()
        if not item.get("ok")
    ]
    results["status"] = (
        "FAIL"
        if hard_failures or (args.strict and strict_failures)
        else "PASS_WITH_AUTH_GATED_WARNINGS"
        if strict_failures
        else "PASS"
    )
    results["hard_failures"] = hard_failures
    results["strict_failures"] = strict_failures
    print(json.dumps(results, indent=2, sort_keys=True))
    return 1 if results["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
