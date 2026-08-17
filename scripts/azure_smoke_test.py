"""Read-only post-deployment verification for the FastAPI/React service.

The script deliberately does not create assessments, follow-ups, alerts, or SMS
work. It logs endpoint/result metadata only, never notification bodies or case
identifiers.
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any

import requests


PUBLIC_ENDPOINTS = ("/", "/health", "/status/uhl", "/runtime/status")
NOTIFICATION_ENDPOINTS = ("/notifications", "/notifications/system/health")


def _request(
    session: requests.Session,
    base_url: str,
    path: str,
    *,
    timeout: float,
) -> tuple[int, Any]:
    response = session.get(
        base_url + path,
        timeout=timeout,
        allow_redirects=True,
    )
    payload: Any = None
    if response.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            payload = response.json()
        except ValueError:
            payload = None
    return response.status_code, payload


def _attempt(
    *,
    base_url: str,
    timeout: float,
    demo_role: str,
    expected_build_id: str,
    check_notifications: bool,
    require_notification_worker: bool,
) -> dict[str, Any]:
    session = requests.Session()
    if demo_role:
        session.headers.update(
            {"X-Demo-Role": demo_role, "X-Demo-User": "deployment-smoke"}
        )

    checks: dict[str, int] = {}
    payloads: dict[str, Any] = {}
    for path in PUBLIC_ENDPOINTS:
        status, payload = _request(session, base_url, path, timeout=timeout)
        checks[path] = status
        payloads[path] = payload
        if not 200 <= status < 300:
            raise RuntimeError(f"{path} returned HTTP {status}")

    if not isinstance(payloads["/health"], dict):
        raise RuntimeError("/health did not return a JSON object")
    if not isinstance(payloads["/status/uhl"], dict):
        raise RuntimeError("/status/uhl did not return a JSON object")

    if check_notifications:
        for path in NOTIFICATION_ENDPOINTS:
            status, payload = _request(session, base_url, path, timeout=timeout)
            checks[path] = status
            payloads[path] = payload
            if not 200 <= status < 300:
                raise RuntimeError(f"{path} returned HTTP {status}")
        notifications = payloads["/notifications"]
        health = payloads["/notifications/system/health"]
        if not isinstance(notifications, dict) or (
            notifications.get("source") != "durable_notification_store"
        ):
            raise RuntimeError("durable notification API source mismatch")
        if not isinstance(health, dict) or not health.get("available"):
            raise RuntimeError("notification repository is unavailable")
        rollout = health.get("rollout_policy") or {}
        if (
            not rollout.get("active_version")
            or rollout.get("configured_version") != rollout.get("active_version")
            or not rollout.get("version_match")
        ):
            raise RuntimeError("durable rollout policy has not converged")
        worker = health.get("submission_worker") or {}
        if expected_build_id and worker.get("web_build_id") != expected_build_id:
            raise RuntimeError("web notification build identity mismatch")
        if require_notification_worker and (
            not worker.get("build_match") or not worker.get("last_heartbeat_at")
        ):
            raise RuntimeError("matching worker heartbeat has not arrived")

    return {
        "status": "PASS",
        "checks": checks,
        "notification_source": check_notifications,
        "worker_required": require_notification_worker,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run read-only health checks against a deployed ALTER service."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--demo-role", default="security_admin")
    parser.add_argument("--expected-build-id", default="")
    parser.add_argument("--check-notifications", action="store_true")
    parser.add_argument("--require-notification-worker", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--retry-seconds", type=float, default=10.0)
    # Retained only so old operator commands fail safely without mutating a case.
    parser.add_argument("--case-uid", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1 <= args.attempts <= 60:
        parser.error("--attempts must be between 1 and 60")
    if args.require_notification_worker and not args.check_notifications:
        parser.error("--require-notification-worker requires --check-notifications")

    base_url = args.base_url.rstrip("/")
    last_error = "not started"
    for attempt in range(1, args.attempts + 1):
        try:
            result = _attempt(
                base_url=base_url,
                timeout=args.timeout,
                demo_role=args.demo_role,
                expected_build_id=args.expected_build_id,
                check_notifications=args.check_notifications,
                require_notification_worker=args.require_notification_worker,
            )
            result["attempt"] = attempt
            result["base_url"] = base_url
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < args.attempts:
                time.sleep(args.retry_seconds)

    print(
        json.dumps(
            {
                "status": "FAIL",
                "attempts": args.attempts,
                "base_url": base_url,
                "error": last_error,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
