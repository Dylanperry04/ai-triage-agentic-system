"""
Central security-status builder.

Single source of truth for the deployment's security posture, used by both the
/security/status endpoint and the startup guard. It reports the effective config
and any UNSAFE combinations. It never exposes secrets or the full-MIMIC path (only
whether it is configured).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple


def _b(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _is_true(name: str) -> bool:
    return os.environ.get(name, "").lower() == "true"


def _provisional_mts_enabled() -> bool:
    raw = _b("PROVISIONAL_MTS_MODE").strip().lower()
    return raw not in {"off", "0", "false", "no", "disabled"}


def _keyvault_pseudonym_secret_available() -> bool:
    try:
        from app.security.secrets_provider import get_secrets_provider
        return bool(get_secrets_provider().get_secret("PSEUDONYM_SECRET"))
    except Exception:
        return False


_DURABLE_AUDIT_PROBE_CACHE: Dict[str, Any] = {"signature": None, "result": None}


def _durable_audit_probe_signature() -> Tuple[str, ...]:
    """Environment values that define the current durable-audit wiring."""
    return (
        _b("PATIENT_DATA_MODE", "").lower(),
        _b("AUDIT_SINK", "local").lower(),
        _b("AZURE_AUDIT_TABLE_NAME", ""),
        _b("AZURE_AUDIT_TABLE_ENDPOINT", ""),
        "connection_string_set" if _b("AZURE_AUDIT_TABLE_CONNECTION_STRING", "") else "",
    )


def _run_durable_audit_write_read_probe() -> bool:
    try:
        from app.security.audit_sink import EncryptedDurableAuditSink, get_audit_sink
        sink = get_audit_sink()
        return (
            isinstance(sink, EncryptedDurableAuditSink)
            and getattr(sink, "client", None) is not None
            and sink.probe_write_read()
        )
    except Exception:
        return False


def run_durable_audit_startup_probe(*, force: bool = False) -> Dict[str, Any]:
    """Run and cache the durable audit write/read probe.

    This is intentionally a startup/readiness action because it writes a harmless
    probe record. Status endpoints must call durable_audit_probe_status() instead
    so GET /security/status remains side-effect free.
    """
    signature = _durable_audit_probe_signature()
    cached = _DURABLE_AUDIT_PROBE_CACHE.get("result")
    if (
        not force
        and cached is not None
        and _DURABLE_AUDIT_PROBE_CACHE.get("signature") == signature
    ):
        return dict(cached)

    required = _is_true("PATIENT_DATA_MODE") and _b("AUDIT_SINK", "local").lower() == "durable"
    if not required:
        result = {
            "required": False,
            "ran": False,
            "ok": False,
            "reason": "not_required",
        }
    else:
        ok = _run_durable_audit_write_read_probe()
        result = {
            "required": True,
            "ran": True,
            "ok": bool(ok),
            "reason": "ok" if ok else "failed",
        }
    _DURABLE_AUDIT_PROBE_CACHE["signature"] = signature
    _DURABLE_AUDIT_PROBE_CACHE["result"] = dict(result)
    return result


def durable_audit_probe_status() -> Dict[str, Any]:
    """Return cached durable-audit probe status without performing writes."""
    signature = _durable_audit_probe_signature()
    cached = _DURABLE_AUDIT_PROBE_CACHE.get("result")
    if cached is not None and _DURABLE_AUDIT_PROBE_CACHE.get("signature") == signature:
        return dict(cached)
    required = _is_true("PATIENT_DATA_MODE") and _b("AUDIT_SINK", "local").lower() == "durable"
    return {
        "required": required,
        "ran": False,
        "ok": False,
        "reason": "not_run" if required else "not_required",
    }


def unsafe_combinations(*, run_probes: bool = True) -> List[str]:
    """Return a list of unsafe configuration problems (empty == OK). These are the
    conditions that must NOT hold in patient-data mode; the startup guard refuses
    to boot if any are present."""
    problems: List[str] = []
    patient = _is_true("PATIENT_DATA_MODE")
    local_research = _is_true("LOCAL_CREDENTIALED_RESEARCH") and not patient
    azure_demo = _is_true("AZURE_SUPERVISOR_DEMO_MODE")
    cors = _b("CORS_ALLOWED_ORIGINS")
    # Parse the comma-separated origin list; reject if ANY entry is a wildcard.
    cors_origins = [o.strip() for o in cors.split(",") if o.strip()]
    if "*" in cors_origins:
        problems.append("CORS_ALLOWED_ORIGINS contains '*' (wildcard origin is never allowed)")
    if patient:
        if not _is_true("AUTH_REQUIRED"):
            problems.append("PATIENT_DATA_MODE=true requires AUTH_REQUIRED=true")
        if _b("AUTH_PROVIDER", "demo").lower() == "demo":
            problems.append("PATIENT_DATA_MODE=true requires a non-demo AUTH_PROVIDER")
        if not _is_true("TRUSTED_AUTH_PROXY"):
            problems.append("PATIENT_DATA_MODE=true requires TRUSTED_AUTH_PROXY=true")
        secrets_provider = _b("SECRETS_PROVIDER", "env").lower()
        if secrets_provider != "keyvault":
            problems.append("PATIENT_DATA_MODE=true requires SECRETS_PROVIDER=keyvault")
            if not _b("PSEUDONYM_SECRET"):
                problems.append(
                    "PATIENT_DATA_MODE=true with env secrets requires a real "
                    "PSEUDONYM_SECRET (no dev salt)"
                )
        elif not _keyvault_pseudonym_secret_available():
            problems.append(
                "PATIENT_DATA_MODE=true with SECRETS_PROVIDER=keyvault requires "
                "successful Key Vault retrieval of PSEUDONYM_SECRET"
            )
        if (
            secrets_provider == "keyvault"
            and _b("PSEUDONYM_SECRET")
            and not _is_true("ALLOW_ENV_PSEUDONYM_SECRET_WITH_KEYVAULT")
        ):
            problems.append(
                "PATIENT_DATA_MODE=true with SECRETS_PROVIDER=keyvault refuses "
                "plain env PSEUDONYM_SECRET; remove it and use Key Vault as the "
                "runtime pseudonym-secret source"
            )
        if _b("AUDIT_SINK", "local").lower() != "durable":
            problems.append("PATIENT_DATA_MODE=true requires AUDIT_SINK=durable")
        else:
            probe = (
                run_durable_audit_startup_probe(force=True)
                if run_probes else durable_audit_probe_status()
            )
            if probe.get("ok") is not True:
                problems.append(
                    "PATIENT_DATA_MODE=true with AUDIT_SINK=durable requires a "
                    "successful durable audit write/read startup probe"
                )
        if _provisional_mts_enabled():
            problems.append(
                "PATIENT_DATA_MODE=true requires PROVISIONAL_MTS_MODE=off; "
                "the provisional Manchester-style research ruleset is not a "
                "deployment/clinical ruleset."
            )
    if local_research:
        if not _b("PSEUDONYM_SECRET") and not _is_true("ALLOW_DEV_PSEUDONYM_SECRET_FOR_LOCAL_RESEARCH"):
            problems.append(
                "LOCAL_CREDENTIALED_RESEARCH=true requires PSEUDONYM_SECRET "
                "(or explicit ALLOW_DEV_PSEUDONYM_SECRET_FOR_LOCAL_RESEARCH=true for tests)"
            )
        if (
            not _b("LOCAL_CREDENTIALED_OUTPUT_DIR")
            and not _b("ACCESS_AUDIT_DIR")
            and not _is_true("ALLOW_REPO_LOCAL_OUTPUTS_FOR_LOCAL_RESEARCH")
        ):
            problems.append(
                "LOCAL_CREDENTIALED_RESEARCH=true requires ACCESS_AUDIT_DIR or "
                "LOCAL_CREDENTIALED_OUTPUT_DIR outside the repo"
            )
    if azure_demo:
        if patient or local_research or _is_true("REAL_PATIENT_DATA"):
            problems.append(
                "AZURE_SUPERVISOR_DEMO_MODE=true is demo-only and cannot be "
                "combined with PATIENT_DATA_MODE, LOCAL_CREDENTIALED_RESEARCH, "
                "or REAL_PATIENT_DATA"
            )
        if _is_true("TRUSTED_AUTH_PROXY") or _is_true("AUTH_REQUIRED"):
            problems.append(
                "AZURE_SUPERVISOR_DEMO_MODE=true uses simulated roles and cannot "
                "be combined with AUTH_REQUIRED or TRUSTED_AUTH_PROXY"
            )
        if _is_true("ALLOW_FULL_MIMIC_IN_AZURE_DEMO") and not _is_true("REAL_MIMIC_DEMO_ACKNOWLEDGED"):
            problems.append(
                "ALLOW_FULL_MIMIC_IN_AZURE_DEMO=true requires "
                "REAL_MIMIC_DEMO_ACKNOWLEDGED=true; this confirms the Azure "
                "environment is approved for credentialed MIMIC and prevents "
                "accidental real-data demos."
            )
        if _b("MIMIC_FULL_ED_DIR") and not _is_true("ALLOW_FULL_MIMIC_IN_AZURE_DEMO"):
            problems.append(
                "AZURE_SUPERVISOR_DEMO_MODE=true must not configure "
                "MIMIC_FULL_ED_DIR unless ALLOW_FULL_MIMIC_IN_AZURE_DEMO=true is "
                "explicitly set for a governed non-public demo"
            )
    return problems


def full_mimic_configured() -> bool:
    """Whether the full-MIMIC seam is configured (without revealing the path)."""
    try:
        from app.data_pipeline.mimic_full_loader import is_full_mimic_available
        return bool(is_full_mimic_available())
    except Exception:
        return False


def build_security_status() -> Dict[str, Any]:
    """The security posture, safe to surface in the UI / an endpoint."""
    patient = _is_true("PATIENT_DATA_MODE")
    local_research = _is_true("LOCAL_CREDENTIALED_RESEARCH") and not patient
    azure_demo = _is_true("AZURE_SUPERVISOR_DEMO_MODE") and not patient and not local_research
    auth_provider = _b("AUTH_PROVIDER", "demo")
    demo_switcher = (
        (auth_provider.lower() == "demo" or azure_demo)
        and not patient
        and not local_research
        and not _is_true("TRUSTED_AUTH_PROXY")
        and not _is_true("AUTH_REQUIRED")
        and not _is_true("REAL_PATIENT_DATA")
    )
    problems = unsafe_combinations(run_probes=False)
    durable_probe = durable_audit_probe_status()
    env_secret_set = bool(_b("PSEUDONYM_SECRET"))
    keyvault_selected = _b("SECRETS_PROVIDER", "env").lower() == "keyvault"
    keyvault_secret_available = (
        _keyvault_pseudonym_secret_available() if keyvault_selected else False
    )
    runtime_secret_source = (
        "keyvault" if keyvault_selected and keyvault_secret_available
        else "env" if env_secret_set and not keyvault_selected
        else "unavailable_or_dev_fallback"
    )
    current_mode = (
        "secured_research" if patient
        else "local_credentialed_research" if local_research
        else "azure_supervisor_demo" if azure_demo
        else "public_demo"
    )
    return {
        "patient_data_mode": patient,
        "local_credentialed_research_mode": local_research,
        "azure_supervisor_demo_mode": azure_demo,
        "auth_required": _is_true("AUTH_REQUIRED"),
        "auth_provider": auth_provider,
        "trusted_auth_proxy": _is_true("TRUSTED_AUTH_PROXY"),
        "secrets_provider": _b("SECRETS_PROVIDER", "env"),
        "audit_sink": _b("AUDIT_SINK", "local"),
        "cors_allowed_origins_set": bool(_b("CORS_ALLOWED_ORIGINS")),
        "cors_is_wildcard": "*" in [o.strip() for o in _b("CORS_ALLOWED_ORIGINS").split(",")],
        "demo_role_switcher_enabled": demo_switcher,
        "full_mimic_configured": full_mimic_configured(),
        "key_vault_configured": keyvault_selected,
        "durable_audit_configured": _b("AUDIT_SINK", "local").lower() == "durable",
        "pseudonym_secret_set": env_secret_set,
        "env_pseudonym_secret_set": env_secret_set,
        "env_pseudonym_secret_allowed_with_keyvault": _is_true(
            "ALLOW_ENV_PSEUDONYM_SECRET_WITH_KEYVAULT"
        ),
        "key_vault_pseudonym_secret_available": keyvault_secret_available,
        "runtime_pseudonym_secret_source": runtime_secret_source,
        "durable_audit_write_read_probe_ok": bool(durable_probe.get("ok")),
        "durable_audit_startup_probe_required": bool(durable_probe.get("required")),
        "durable_audit_startup_probe_ran": bool(durable_probe.get("ran")),
        "durable_audit_startup_probe_reason": durable_probe.get("reason"),
        "local_credentialed_output_dir_set": bool(_b("LOCAL_CREDENTIALED_OUTPUT_DIR")),
        "access_audit_dir_set": bool(_b("ACCESS_AUDIT_DIR")),
        "repo_local_output_override": _is_true("ALLOW_REPO_LOCAL_OUTPUTS_FOR_LOCAL_RESEARCH"),
        "current_mode": current_mode,
        "unsafe_combinations": problems,
        "is_safe": not problems,
    }
