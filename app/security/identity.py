"""
Identity boundary for the triage app.

THE SECURITY MODEL IN ONE PARAGRAPH:
The app does NOT authenticate users itself. Authentication (Entra ID / SSO, MFA,
conditional access) and network controls (hospital-managed device, VPN / private
network, Azure private endpoint) are provided by the HOSPITAL's identity platform
and infrastructure, in front of this app. This module defines the trusted
boundary at which an already-authenticated user's identity enters the app, and it
FAILS CLOSED: in patient-data mode it refuses access unless a verified identity is
present. App-level RBAC (see authz.py) is a SECOND layer that decides what that
verified user may do. App RBAC alone is not security; it only has meaning behind a
real identity provider and trusted network.

Pluggability: identity is read through the AuthContextProvider interface so the
production implementation (Azure trusted headers today; a UHL/HSE-specific SSO,
reverse proxy, or private-endpoint pattern later) can be swapped via config
without touching call sites.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Protocol


# ── Internal role model ─────────────────────────────────────────────────────
ROLE_TRIAGE_NURSE = "triage_nurse"
ROLE_ED_DOCTOR = "ed_doctor"
ROLE_CLINICAL_SUPERVISOR = "clinical_supervisor"
ROLE_RESEARCHER = "researcher"
ROLE_SECURITY_ADMIN = "security_admin"
ROLE_GOVERNANCE_AUDITOR = "governance_auditor"

ALL_ROLES = {
    ROLE_TRIAGE_NURSE, ROLE_ED_DOCTOR, ROLE_CLINICAL_SUPERVISOR,
    ROLE_RESEARCHER, ROLE_SECURITY_ADMIN, ROLE_GOVERNANCE_AUDITOR,
}


@dataclass
class AuthContext:
    """A verified, authenticated user as seen by the app. `authenticated=False`
    means no trusted identity was established (the app must fail closed in
    patient-data mode)."""
    authenticated: bool = False
    user_id: Optional[str] = None
    display_name: Optional[str] = None
    email: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    source: str = "none"          # which provider established this identity
    is_demo_stub: bool = False    # True only for the local public-data stub
    raw_groups: List[str] = field(default_factory=list)

    def has_role(self, role: str) -> bool:
        return role in self.roles


class AuthContextProvider(Protocol):
    """Pluggable identity source. Implementations resolve the current request's
    authenticated identity (or an unauthenticated context)."""

    def get_context(self, request_headers: Optional[dict] = None) -> AuthContext:
        ...


# ── Group/role mapping (Entra group or app-role -> internal role) ───────────
# Maps identity-provider group names / app roles to internal roles. The mapping
# is intentionally explicit and configurable; unknown groups grant NO role.
DEFAULT_GROUP_ROLE_MAP = {
    "triage-nurses": ROLE_TRIAGE_NURSE,
    "ed-doctors": ROLE_ED_DOCTOR,
    "clinical-supervisors": ROLE_CLINICAL_SUPERVISOR,
    "researchers": ROLE_RESEARCHER,
    "security-admins": ROLE_SECURITY_ADMIN,
    "governance-auditors": ROLE_GOVERNANCE_AUDITOR,
    # Allow the internal role names themselves to pass through (app-role style).
    **{r: r for r in ALL_ROLES},
}


def map_groups_to_roles(groups: List[str], group_role_map: Optional[dict] = None) -> List[str]:
    m = group_role_map or DEFAULT_GROUP_ROLE_MAP
    roles = []
    for g in groups or []:
        key = str(g).strip().lower()
        if key in m and m[key] not in roles:
            roles.append(m[key])
    return roles


# ── Provider 1: Azure trusted-header provider (production) ───────────────────
class AzureTrustedHeaderProvider:
    """
    Reads the authenticated principal injected by Azure App Service / Azure
    Container Apps authentication (or another TRUSTED reverse proxy) via the
    `X-MS-CLIENT-PRINCIPAL` header (Base64-encoded claims JSON).

    SECURITY PRECONDITION: this is only safe when the app is genuinely behind
    trusted auth that STRIPS and re-sets these headers. If the app is exposed
    without that proxy, arbitrary callers could forge X-MS-* headers. Therefore
    this provider is trusted ONLY when `TRUSTED_AUTH_PROXY=true` is set (which the
    deployment sets only when the platform auth is actually in front of the app).
    Otherwise it returns an unauthenticated context.
    """
    PRINCIPAL_HEADER = "X-MS-CLIENT-PRINCIPAL"
    NAME_HEADER = "X-MS-CLIENT-PRINCIPAL-NAME"
    ID_HEADER = "X-MS-CLIENT-PRINCIPAL-ID"

    def __init__(self, group_role_map: Optional[dict] = None):
        self.group_role_map = group_role_map or DEFAULT_GROUP_ROLE_MAP

    def get_context(self, request_headers: Optional[dict] = None) -> AuthContext:
        headers = {k.lower(): v for k, v in (request_headers or {}).items()}

        if os.environ.get("TRUSTED_AUTH_PROXY", "").lower() != "true":
            # We are not behind a trusted proxy: do NOT trust incoming X-MS-* headers.
            return AuthContext(authenticated=False, source="azure_header_untrusted")

        principal_b64 = headers.get(self.PRINCIPAL_HEADER.lower())
        if not principal_b64:
            return AuthContext(authenticated=False, source="azure_header_missing")

        try:
            decoded = base64.b64decode(principal_b64).decode("utf-8")
            principal = json.loads(decoded)
        except Exception:
            return AuthContext(authenticated=False, source="azure_header_malformed")

        # Azure principal: {"auth_typ":..., "claims":[{"typ":..,"val":..}, ...]}
        claims = principal.get("claims", [])
        def _claim(*types):
            for c in claims:
                if c.get("typ") in types:
                    return c.get("val")
            return None

        user_id = (headers.get(self.ID_HEADER.lower())
                   or _claim("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "oid", "sub"))
        email = _claim("emails", "email",
                       "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress")
        name = (headers.get(self.NAME_HEADER.lower())
                or _claim("name", "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name")
                or email)

        # Groups / roles may appear under several claim types.
        groups = [c.get("val") for c in claims
                  if c.get("typ") in ("groups", "roles",
                                      "http://schemas.microsoft.com/ws/2008/06/identity/claims/role")]
        roles = map_groups_to_roles(groups, self.group_role_map)

        if not user_id:
            return AuthContext(authenticated=False, source="azure_header_no_userid")

        return AuthContext(
            authenticated=True, user_id=user_id, display_name=name, email=email,
            roles=roles, source="azure_trusted_header", is_demo_stub=False,
            raw_groups=[g for g in groups if g],
        )


# ── Provider 2: fail-closed / demo-stub provider (local public data only) ────
class LocalStubProvider:
    """
    For LOCAL PUBLIC-DATA DEMOS ONLY. Returns a clearly-marked stub identity so
    the app is usable on a developer machine without an identity provider. It is
    NOT real authentication and must never be used with patient data: when
    patient-data mode is on, resolve_auth_context() ignores this and fails closed.
    """
    def __init__(
        self,
        demo_roles: Optional[List[str]] = None,
        *,
        source: str = "local_stub",
        display_name: str = "DEMO STUB USER (not real auth)",
    ):
        # A demo user with a broad-but-explicit role set, loudly marked as a stub.
        self.demo_roles = demo_roles or [ROLE_RESEARCHER]
        self.source = source
        self.display_name = display_name

    def get_context(self, request_headers: Optional[dict] = None) -> AuthContext:
        return AuthContext(
            authenticated=True, user_id="demo-stub-user",
            display_name=self.display_name, email=None,
            roles=list(self.demo_roles), source=self.source, is_demo_stub=True,
        )


# ── Mode flags ──────────────────────────────────────────────────────────────
def patient_data_mode() -> bool:
    """When true, the app handles confidential patient data and MUST fail closed:
    no stub identity, real authenticated context required."""
    return os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").lower() == "true"


def _load_local_dotenv_if_present() -> None:
    """Best-effort .env load for direct imports of this module in local dev."""
    try:
        from app.config import load_local_dotenv_if_present
        load_local_dotenv_if_present()
    except Exception:
        return


def local_credentialed_research_mode() -> bool:
    """A DISTINCT, narrower profile for an approved local research machine (e.g.
    the credentialed researcher's own VS Code box), NOT the secured production
    patient-data deployment.

    It exists so a credentialed researcher can load their OWN credentialed
    MIMIC-IV-ED locally and see it in the app, WITHOUT having to assert the full
    production security posture (Entra/MFA/private network/Key Vault/durable
    audit) that PATIENT_DATA_MODE implies. It is enabled with
    LOCAL_CREDENTIALED_RESEARCH=true and is mutually exclusive with
    PATIENT_DATA_MODE (production wins if both are set, and this returns False).

    The loader still enforces every DATA-handling guard in this mode (path set,
    outside the repo, exists, schema present, NO demo/KTAS fallback). Callers are
    responsible for the local-machine guarantees documented for this profile:
    bind to 127.0.0.1 only and do not transmit data to any cloud/LLM by default.
    """
    if patient_data_mode():
        return False
    return os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"


def azure_supervisor_demo_mode() -> bool:
    """Explicit Azure-hosted demo profile for supervisor walkthroughs.

    This is not real authentication and is only allowed when real-data/security
    flags are off. It exists so an Azure demo can show RBAC from the sidebar
    without accidentally weakening patient-data or credentialed-MIMIC modes.
    """
    if patient_data_mode() or local_credentialed_research_mode():
        return False
    if _env_true("TRUSTED_AUTH_PROXY") or _env_true("AUTH_REQUIRED"):
        return False
    if _env_true("REAL_PATIENT_DATA"):
        return False
    return (
        _env_true("AZURE_SUPERVISOR_DEMO_MODE")
        and _env_true("ALLOW_DEMO_ROLE_SWITCHER")
    )


def real_mimic_azure_demo_mode() -> bool:
    """Explicit, governed Azure supervisor demo that loads credentialed MIMIC.

    This remains a demo-auth mode (not hospital SSO, not patient-data readiness),
    but it may access credentialed full MIMIC only when the operator sets both a
    technical allow flag and a separate acknowledgement flag. Without both, the
    full-MIMIC loader refuses access and synthetic fallback must not mask it.
    """
    return (
        azure_supervisor_demo_mode()
        and _env_true("ALLOW_FULL_MIMIC_IN_AZURE_DEMO")
        and _env_true("REAL_MIMIC_DEMO_ACKNOWLEDGED")
    )


def demo_role_switcher_allowed() -> bool:
    """Whether the backend may accept an X-Demo-Role header.

    The answer is deliberately stricter than "not patient mode": stale frontend
    state or a forged header must not change effective permissions in any real
    auth, trusted-proxy, patient-data, or local credentialed research profile.
    """
    if (
        patient_data_mode()
        or local_credentialed_research_mode()
        or _env_true("AUTH_REQUIRED")
        or _env_true("TRUSTED_AUTH_PROXY")
        or _env_true("REAL_PATIENT_DATA")
    ):
        return False
    auth_provider = os.environ.get("AUTH_PROVIDER", "demo").lower()
    if azure_supervisor_demo_mode():
        return True
    return auth_provider == "demo"


def credentialed_data_access_allowed() -> bool:
    """True if either the production patient-data profile OR the local
    credentialed-research profile is active. Used by the full-MIMIC loader to
    decide whether credentialed data access is permitted at all."""
    return (
        patient_data_mode()
        or local_credentialed_research_mode()
        or real_mimic_azure_demo_mode()
    )


def credentialed_mimic_active_or_requested() -> bool:
    """Whether credentialed MIMIC is active/requested in a non-patient mode."""
    if local_credentialed_research_mode() or real_mimic_azure_demo_mode():
        return True
    return False


def cloud_egress_allowed() -> bool:
    """Whether the app may transmit anything to an external cloud service (e.g.
    Azure OpenAI for LLM explanation, or Weights & Biases for logging).

    In the LOCAL_CREDENTIALED_RESEARCH profile this is FALSE BY DEFAULT: the whole
    point of that profile is to work with credentialed MIMIC on an approved local
    machine WITHOUT sending any patient-derived data off the box. Cloud egress can
    be explicitly re-enabled with ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH=true, which the
    operator must only set once they have verified the provider's zero-retention,
    no-training, and no-human-review terms (PhysioNet requires this for API
    services on credentialed data). The code requires both the technical opt-in
    and a separate approval flag so "turn on cloud" and "governance approved the
    provider terms" are not accidentally conflated.

    In production patient-data mode and in the public-demo profile, this returns
    True (cloud features are gated by their own config such as load_azure_config()).
    """
    _load_local_dotenv_if_present()
    if local_credentialed_research_mode():
        return (
            os.environ.get("ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH", "").lower() == "true"
            and os.environ.get("APPROVED_CLOUD_LLM_DATA_PROCESSING", "").lower() == "true"
        )
    if real_mimic_azure_demo_mode():
        return (
            os.environ.get("ALLOW_CLOUD_LLM_WITH_CREDENTIALED_MIMIC", "").lower() == "true"
            and os.environ.get("APPROVED_CLOUD_LLM_DATA_PROCESSING", "").lower() == "true"
        )
    return True


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "127.0.0.0/8"}


def is_loopback_host(host: str) -> bool:
    """True if the given bind host is a loopback (local-only) interface."""
    if not host:
        return False
    h = host.strip().lower()
    if h in {"127.0.0.1", "::1", "localhost"}:
        return True
    # Any 127.x.x.x address is loopback.
    if h.startswith("127."):
        return True
    return False


def assert_local_research_bind_is_loopback() -> None:
    """In the LOCAL_CREDENTIALED_RESEARCH profile, refuse to run if the backend is
    bound to a non-loopback interface. The operator declares the bind host via
    BACKEND_BIND_HOST (the same value passed to uvicorn --host). If it is unset we
    cannot prove loopback, so we FAIL CLOSED and require it to be set explicitly.

    Raises RuntimeError on a non-loopback (or unverifiable) bind. No-op when the
    local-research profile is not active.
    """
    if not local_credentialed_research_mode():
        return
    host = os.environ.get("BACKEND_BIND_HOST", "").strip()
    if not host:
        raise RuntimeError(
            "LOCAL_CREDENTIALED_RESEARCH is enabled but BACKEND_BIND_HOST is not "
            "set. This profile must bind to loopback only. Set BACKEND_BIND_HOST "
            "to the host you pass to uvicorn --host (e.g. 127.0.0.1) so the bind "
            "can be verified. Refusing to start."
        )
    if not is_loopback_host(host):
        raise RuntimeError(
            f"LOCAL_CREDENTIALED_RESEARCH is enabled but BACKEND_BIND_HOST "
            f"('{host}') is not a loopback interface. This profile must bind to "
            "127.0.0.1/::1 only and must not be exposed on a network interface. "
            "Refusing to start."
        )


def auth_required() -> bool:
    """When true (or implied by patient_data_mode), unauthenticated access is
    refused even for non-patient data."""
    return os.environ.get("AUTH_REQUIRED", "").lower() == "true" or patient_data_mode()


def resolve_auth_context(request_headers: Optional[dict] = None) -> AuthContext:
    """
    Resolve the current user's identity through the configured provider chain,
    enforcing fail-closed semantics.

    - In patient-data mode (or AUTH_REQUIRED): ONLY the trusted Azure-header
      provider is consulted; the demo stub is NEVER used; if no verified identity
      exists, an unauthenticated context is returned (callers must deny access).
    - In local/demo mode (public data): try the trusted header first; if absent,
      fall back to the clearly-marked demo stub so the public-data demo is usable.
    """
    azure = AzureTrustedHeaderProvider()
    ctx = azure.get_context(request_headers)

    if patient_data_mode() or auth_required():
        # Fail closed: never substitute a stub when real data/security is required.
        return ctx  # authenticated only if the trusted header genuinely verified

    # Local credentialed research profile: allow a fixed, explicit local role on
    # the loopback-only approved machine, but do not expose the public demo role
    # switcher. This keeps the app usable for credentialed MIMIC review without
    # pretending it is the hospital production auth boundary.
    if local_credentialed_research_mode():
        role = os.environ.get("LOCAL_RESEARCH_ROLE", ROLE_TRIAGE_NURSE)
        return LocalStubProvider(
            demo_roles=[role],
            source="local_fixed_role",
            display_name="LOCAL CREDENTIALED RESEARCH USER",
        ).get_context(request_headers)

    # If the deployment declares a trusted auth proxy or a non-demo provider, do
    # not silently fall back to a stub identity. The proxy/provider context is the
    # only acceptable identity source in that shape, even outside patient-data
    # mode.
    if _env_true("TRUSTED_AUTH_PROXY"):
        return ctx
    if (
        os.environ.get("AUTH_PROVIDER", "demo").lower() != "demo"
        and not azure_supervisor_demo_mode()
    ):
        return ctx

    # Local/demo public-data mode: allow the stub fallback.
    if ctx.authenticated:
        return ctx
    # The UI's demo role-switcher forwards the selected role via X-Demo-Role so the
    # backend acts as that role. The backend accepts that header only in explicit
    # demo profiles; it is ignored in patient-data, real-auth, trusted-proxy, and
    # local credentialed research modes.
    demo_role = None
    if request_headers and demo_role_switcher_allowed():
        demo_role = (request_headers.get("X-Demo-Role")
                     or request_headers.get("x-demo-role"))
    if not demo_role:
        demo_role = os.environ.get("DEMO_ROLE") or None
    roles = [demo_role] if demo_role else None
    if azure_supervisor_demo_mode():
        return LocalStubProvider(
            demo_roles=roles,
            source="azure_supervisor_demo_stub",
            display_name="AZURE SUPERVISOR DEMO USER (not real auth)",
        ).get_context(request_headers)
    return LocalStubProvider(demo_roles=roles).get_context(request_headers)
