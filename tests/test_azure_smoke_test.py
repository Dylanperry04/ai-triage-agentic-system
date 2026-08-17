"""Tests for the read-only deployment smoke verifier."""
from __future__ import annotations

import pytest

from scripts import azure_smoke_test


class _Response:
    def __init__(self, payload=None, *, text="ok", status=200):
        self._payload = payload
        self.text = text
        self.status_code = status
        self.headers = {
            "content-type": "application/json" if payload is not None else "text/html"
        }

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses = responses
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        path = "/" + url.split("/", 3)[-1] if url.count("/") >= 3 else "/"
        return self.responses[path]


def _responses(*, heartbeat=True):
    return {
        "/": _Response(text="<html>ALTER</html>"),
        "/health": _Response({"status": "ok"}),
        "/status/uhl": _Response({"active": True}),
        "/runtime/status": _Response({"status": "ok"}),
        "/notifications": _Response({"source": "durable_notification_store"}),
        "/notifications/system/health": _Response(
            {
                "available": True,
                "rollout_policy": {
                    "active_version": "20260817T1200000000000Z-test",
                    "configured_version": "20260817T1200000000000Z-test",
                    "version_match": True,
                },
                "submission_worker": {
                    "web_build_id": "build-1",
                    "build_match": heartbeat,
                    "last_heartbeat_at": "2026-08-17T12:00:00Z" if heartbeat else "",
                },
            }
        ),
    }


def test_smoke_check_is_read_only_and_requires_matching_worker(monkeypatch):
    session = _Session(_responses())
    monkeypatch.setattr(azure_smoke_test.requests, "Session", lambda: session)

    result = azure_smoke_test._attempt(
        base_url="https://triage.example",
        timeout=1,
        demo_role="security_admin",
        expected_build_id="build-1",
        check_notifications=True,
        require_notification_worker=True,
    )

    assert result["status"] == "PASS"
    assert session.headers["X-Demo-Role"] == "security_admin"
    assert {url.rsplit("triage.example", 1)[-1] for url, _ in session.calls} == {
        *azure_smoke_test.PUBLIC_ENDPOINTS,
        *azure_smoke_test.NOTIFICATION_ENDPOINTS,
    }
    assert all("json" not in kwargs and "data" not in kwargs for _, kwargs in session.calls)


def test_smoke_check_rejects_missing_current_worker_heartbeat(monkeypatch):
    monkeypatch.setattr(
        azure_smoke_test.requests,
        "Session",
        lambda: _Session(_responses(heartbeat=False)),
    )

    with pytest.raises(RuntimeError, match="matching worker heartbeat"):
        azure_smoke_test._attempt(
            base_url="https://triage.example",
            timeout=1,
            demo_role="security_admin",
            expected_build_id="build-1",
            check_notifications=True,
            require_notification_worker=True,
        )


def test_smoke_check_rejects_rollout_policy_that_has_not_converged(monkeypatch):
    responses = _responses()
    responses["/notifications/system/health"]._payload["rollout_policy"][
        "active_version"
    ] = "older-policy"
    monkeypatch.setattr(
        azure_smoke_test.requests,
        "Session",
        lambda: _Session(responses),
    )

    with pytest.raises(RuntimeError, match="rollout policy has not converged"):
        azure_smoke_test._attempt(
            base_url="https://triage.example",
            timeout=1,
            demo_role="security_admin",
            expected_build_id="build-1",
            check_notifications=True,
            require_notification_worker=False,
        )
