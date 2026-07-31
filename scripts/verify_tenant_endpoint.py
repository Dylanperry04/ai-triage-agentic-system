"""Anonymous-side verification for the tenant-restricted Azure endpoint.

This intentionally does not accept or print credentials. It proves that an
anonymous caller cannot reach the triage entry point or API and that Azure sends
the caller into Microsoft Entra authentication. Complete the signed-in and
wrong-tenant checks manually using docs/TENANT_ENDPOINT_SETUP.md.
"""
from __future__ import annotations

import argparse
import json
from urllib.parse import urljoin

import requests


def _check(session: requests.Session, base_url: str, path: str, timeout: float) -> dict:
    response = session.get(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        allow_redirects=False,
        timeout=timeout,
        headers={"Accept": "text/html"},
    )
    location = response.headers.get("Location", "")
    protected = response.status_code in {302, 303, 307, 308, 401, 403}
    entra_redirect = (
        "/.auth/login/aad" in location
        or "login.microsoftonline.com" in location
    )
    return {
        "path": path,
        "status_code": response.status_code,
        "anonymous_access_blocked": protected,
        "entra_redirect_observed": entra_redirect,
        "redirect_host_or_path": location.split("?")[0] if location else None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    session = requests.Session()
    checks = [
        _check(session, args.base_url, "/triage", args.timeout),
        _check(session, args.base_url, "/auth/triage-link", args.timeout),
        _check(session, args.base_url, "/cases", args.timeout),
    ]
    passed = all(item["anonymous_access_blocked"] for item in checks)
    result = {
        "status": "PASS" if passed else "FAIL",
        "checks": checks,
        "manual_checks_still_required": [
            "assigned target-tenant user can open /triage",
            "personal or other-tenant account is denied",
            "/auth/triage-link reports tenant_locked=true and tenant_validated=true",
            "unassigned target-tenant user receives no application role",
            "sign-out ends the App Service authentication session",
        ],
    }
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
