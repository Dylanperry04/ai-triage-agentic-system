"""
Streamlit -> FastAPI API client.

Streamlit is the FRONTEND ONLY. Every protected action goes through this client to
the FastAPI backend, which is the SOLE server-side enforcement boundary (identity,
RBAC, audit, redaction, fail-closed). Streamlit never imports the workflow/storage
layers for protected actions; it calls these endpoints.

Two transports, same enforcement path:
  * TWO-SERVICE (production): set FASTAPI_BASE_URL to the backend URL. Calls go
    over HTTP to the separate FastAPI service (the real Azure topology).
  * SINGLE-PROCESS (local/demo): if FASTAPI_BASE_URL is unset, calls use httpx's
    in-process ASGI transport against the real FastAPI app object. This still
    executes the actual FastAPI routes/dependencies (RBAC, audit, redaction) — so
    there is ONE enforcement path, not a separate Streamlit bypass.

Identity propagation: in the real deployment the Entra principal header
(X-MS-CLIENT-PRINCIPAL) arrives at Streamlit; the client forwards it (and the
trusted-proxy expectation) so the backend resolves the same identity. In demo
mode the backend uses its labelled demo stub.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx


class BackendError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"backend {status_code}: {detail}")


def _base_url() -> Optional[str]:
    url = os.environ.get("FASTAPI_BASE_URL", "").strip()
    return url or None


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").lower() == "true"


def _demo_role_forward_allowed() -> bool:
    if (
        _env_true("PATIENT_DATA_MODE")
        or _env_true("LOCAL_CREDENTIALED_RESEARCH")
        or _env_true("AUTH_REQUIRED")
        or _env_true("TRUSTED_AUTH_PROXY")
        or _env_true("REAL_PATIENT_DATA")
    ):
        return False
    auth_provider = os.environ.get("AUTH_PROVIDER", "demo").lower()
    azure_demo = (
        _env_true("AZURE_SUPERVISOR_DEMO_MODE")
        and _env_true("ALLOW_DEMO_ROLE_SWITCHER")
    )
    return auth_provider == "demo" or azure_demo


def _forward_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build the headers to forward to the backend, including the Entra principal
    if present so the backend resolves the same identity."""
    headers: Dict[str, str] = {}
    # Forward the trusted Entra principal header if Streamlit received one.
    try:
        import streamlit as st
        incoming = dict(st.context.headers) if hasattr(st, "context") else {}
    except Exception:
        incoming = {}
    for k, v in incoming.items():
        if k.lower() in ("x-ms-client-principal", "x-ms-client-principal-id",
                         "x-ms-client-principal-name"):
            headers[k] = v
    # In demo mode, forward the role selected in the UI's demo role-switcher so the
    # backend acts as that role (ignored by the backend in patient-data mode).
    try:
        import streamlit as st
        if _demo_role_forward_allowed():
            demo_role = st.session_state.get("demo_role")
            if demo_role:
                headers["X-Demo-Role"] = demo_role
    except Exception:
        pass
    if extra:
        headers.update(extra)
    return headers


def _request(method: str, path: str, *, json: Any = None,
             params: Optional[Dict[str, Any]] = None,
             headers: Optional[Dict[str, str]] = None) -> Any:
    fwd = _forward_headers(headers)
    base = _base_url()
    patient_mode = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    local_credentialed = (
        os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        and not patient_mode
    )
    if base is None and patient_mode:
        # Patient-data mode must use a real backend service, not the in-process
        # fallback, unless an explicit, clearly-named dev override is set.
        if os.environ.get("ALLOW_IN_PROCESS_BACKEND_FOR_PATIENT_DATA", "false").lower() != "true":
            raise BackendError(
                503,
                "FASTAPI_BASE_URL is required in patient-data mode. The in-process "
                "backend fallback is disabled for patient data (set it explicitly "
                "with ALLOW_IN_PROCESS_BACKEND_FOR_PATIENT_DATA=true only for local dev).")
    if base is None and local_credentialed:
        if (
            os.environ.get(
                "ALLOW_IN_PROCESS_BACKEND_FOR_LOCAL_CREDENTIALED_RESEARCH",
                "false",
            ).lower()
            != "true"
        ):
            raise BackendError(
                503,
                "FASTAPI_BASE_URL is required in LOCAL_CREDENTIALED_RESEARCH. "
                "Run the FastAPI backend separately on 127.0.0.1 and set "
                "FASTAPI_BASE_URL=http://127.0.0.1:8000. The in-process backend "
                "fallback is disabled for credentialed MIMIC unless "
                "ALLOW_IN_PROCESS_BACKEND_FOR_LOCAL_CREDENTIALED_RESEARCH=true "
                "is set for an explicit local dev test.")
    if base:
        # Two-service mode: real HTTP to the backend.
        c = httpx.Client(base_url=base, headers=fwd, timeout=120.0)
        try:
            resp = c.request(method, path, json=json, params=params)
        finally:
            c.close()
        status = resp.status_code
        ctype = resp.headers.get("content-type", "")
        payload_json = resp.json if ctype.startswith("application/json") else None
        text = resp.text
    else:
        # Single-process mode: sync ASGI against the real FastAPI app via the
        # Starlette TestClient (a synchronous ASGI client, not only for tests).
        # This still executes the real FastAPI routes/dependencies (RBAC, audit,
        # redaction), so there is one enforcement path.
        from starlette.testclient import TestClient
        from app.main import app as fastapi_app
        client = TestClient(fastapi_app, headers=fwd)
        resp = client.request(method, path, json=json, params=params)
        status = resp.status_code
        ctype = resp.headers.get("content-type", "")
        payload_json = resp.json if ctype.startswith("application/json") else None
        text = resp.text

    if status >= 400:
        detail = text
        if payload_json is not None:
            try:
                detail = payload_json().get("detail", text)
            except Exception:
                detail = text
        raise BackendError(status, detail)
    if payload_json is not None:
        return payload_json()
    return text


# ---- Endpoint wrappers (the only surface Streamlit should call) -------------

def health() -> Any:
    return _request("GET", "/health")


def full_mimic_status() -> Any:
    """Backend full-MIMIC configuration diagnostic. In two-service mode this
    reflects the BACKEND's environment (where MIMIC_FULL_ED_DIR / MIMIC_FULL_MODEL_PATH
    are configured), not the frontend container's. Use this for the sidebar/status
    rather than inspecting the frontend's own local environment."""
    return _request("GET", "/status/full-mimic")


def llm_status() -> Any:
    return _request("GET", "/status/llm")


def runtime_status() -> Any:
    return _request("GET", "/runtime/status")


def assessment_cache_key() -> Any:
    return _request("GET", "/status/assessment-cache-key")


def security_status() -> Any:
    return _request("GET", "/security/status")


def auth_session() -> Any:
    return _request("GET", "/auth/session")


def ui_access(permission: Optional[str], action: str, page: str, detail: str = "") -> Any:
    return _request(
        "POST",
        "/auth/ui-access",
        json={
            "permission": permission,
            "action": action,
            "page": page,
            "detail": detail,
        },
    )


def list_cases(
    dataset: Optional[str] = None,
    *,
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
) -> Any:
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if dataset:
        params["dataset"] = dataset
    if search:
        params["q"] = search
    return _request("GET", "/cases", params=params)


def get_case(case_uid: str) -> Any:
    return _request("GET", f"/cases/{case_uid}")


def run_assessment(case_uid: str) -> Any:
    return _request("POST", f"/cases/{case_uid}/assessments")


def explain_case(case_uid: str, question: Optional[str] = None) -> Any:
    return _request("POST", f"/cases/{case_uid}/explanations",
                    json={"question": question})


def multiagent_explain_case(case_uid: str, question: Optional[str] = None) -> Any:
    return _request("POST", f"/cases/{case_uid}/multiagent-explanations",
                    json={"question": question})


def submit_review(case_uid: str, body: Dict[str, Any]) -> Any:
    return _request("POST", f"/cases/{case_uid}/reviews", json=body)


def followup_case(case_uid: str, updated_vitals: Dict[str, Any]) -> Any:
    return _request("POST", f"/cases/{case_uid}/followups",
                    json={"updated_vitals": updated_vitals})


def followup_multiagent_explain_case(
    case_uid: str,
    updated_vitals: Dict[str, Any],
    question: Optional[str] = None,
) -> Any:
    return _request(
        "POST",
        f"/cases/{case_uid}/followups/multiagent-explanations",
        json={"updated_vitals": updated_vitals, "question": question},
    )


def audit_events(limit: int = 200) -> Any:
    return _request("GET", "/audit/events", params={"limit": limit})


def audit_records(limit: int = 200) -> Any:
    return _request("GET", "/audit/records", params={"limit": limit})


def model_performance() -> Any:
    return _request("GET", "/model/performance")


def governance_report() -> Any:
    return _request("GET", "/governance/report")


def governance_policy_checks() -> Any:
    return _request("POST", "/governance/policy-checks")


def governance_wandb_status() -> Any:
    return _request("GET", "/governance/wandb-status")


def governance_log_wandb(body: Dict[str, Any]) -> Any:
    return _request("POST", "/governance/log-wandb", json=body)


# ---- Patient-data read guard -----------------------------------------------

def patient_data_mode() -> bool:
    return os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"


def local_credentialed_research_mode() -> bool:
    return os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"


def reads_must_use_backend() -> bool:
    """Sensitive reads must go through the backend enforcement boundary.

    This applies both to formal patient-data mode and to local credentialed
    MIMIC research mode. Plain local file reads are only acceptable for the
    non-sensitive public demo/test profile.
    """
    return patient_data_mode() or local_credentialed_research_mode()
