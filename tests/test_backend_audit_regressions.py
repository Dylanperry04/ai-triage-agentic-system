"""Confirmed security/storage regressions from the October 2026 review."""
import base64
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import pytest

from app.security import rate_limit
from app.security.identity import AzureTrustedHeaderProvider, is_loopback_host, map_groups_to_roles
from app.storage.jsonl_io import append_jsonl, read_jsonl_dicts


def _header(payload):
    return {"X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(payload).encode()).decode()}


@pytest.mark.parametrize("payload", [
    None, [], "not an object", 17,
    {"claims": None}, {"claims": {}}, {"claims": "roles"},
    {"claims": [None]}, {"claims": ["admin"]},
    {"claims": [{"typ": "oid", "val": ["user"]}]},
    {"claims": [{"typ": ["roles"], "val": "security-admins"}]},
])
def test_malformed_trusted_principal_is_denied_without_exception(monkeypatch, payload):
    monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
    result = AzureTrustedHeaderProvider().get_context(_header(payload))
    assert result.authenticated is False
    assert result.roles == []
    assert result.source == "azure_header_malformed"


def test_empty_role_mapping_grants_nothing(monkeypatch):
    monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
    header = _header({"claims": [
        {"typ": "oid", "val": "staff-1"},
        {"typ": "roles", "val": "security-admins"},
    ]})
    assert map_groups_to_roles(["security-admins"], {}) == []
    result = AzureTrustedHeaderProvider(group_role_map={}).get_context(header)
    assert result.authenticated is True
    assert result.roles == []
    assert AzureTrustedHeaderProvider().get_context(header).roles == ["security_admin"]


@pytest.mark.parametrize("host", [
    "127.evil.example", "127.0.0.1.attacker.example", "127.0.0.999",
    "127.", "127.0.0.0/8", "0.0.0.0", "::", "192.168.1.2", "",
])
def test_only_actual_loopback_hosts_pass_bind_guard(host):
    assert is_loopback_host(host) is False


@pytest.mark.parametrize("host", ["127.0.0.1", "127.100.25.2", "::1", "localhost", " LOCALHOST "])
def test_valid_loopback_hosts_remain_supported(host):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_invalid_rate_limit_setting_uses_safe_default(monkeypatch, value):
    rate_limit.reset_state()
    monkeypatch.setenv("CHATBOT_RATE_MAX_REQUESTS", value)
    assert rate_limit.rate_limit_max_requests() == 20
    decisions = [rate_limit.check_rate("staff", now=100).allowed for _ in range(21)]
    assert decisions == [True] * 20 + [False]
    rate_limit.reset_state()


def test_concurrent_requests_reserve_single_available_slot(monkeypatch):
    # Delaying append exposes the real check-then-append race deterministically
    # enough to exercise the entire window, without changing limiter semantics.
    class SlowAppendDeque(deque):
        def append(self, value):
            time.sleep(.01)
            super().append(value)

    rate_limit.reset_state()
    monkeypatch.setenv("CHATBOT_RATE_MAX_REQUESTS", "1")
    monkeypatch.setitem(rate_limit._request_times, "concurrent", SlowAppendDeque())
    start = threading.Barrier(12)

    def request(_):
        start.wait(timeout=5)
        return rate_limit.check_rate("concurrent", now=100).allowed

    with ThreadPoolExecutor(max_workers=12) as workers:
        decisions = list(workers.map(request, range(12)))
    assert sum(decisions) == 1
    rate_limit.reset_state()


@pytest.mark.parametrize("limit,expected", [(None, [1, 2, 3]), (2, [2, 3]), (0, []), (-1, [])])
def test_jsonl_tail_limit_is_honoured(tmp_path, limit, expected):
    path = tmp_path / "audit.jsonl"
    for value in [1, 2, 3]:
        append_jsonl(path, {"value": value})
    assert [row["value"] for row in read_jsonl_dicts(path, limit=limit)] == expected
