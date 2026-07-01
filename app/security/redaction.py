"""
Patient-data protection: pseudonymisation + log redaction.

PRINCIPLE: logs and audit records must never contain raw patient or visit
identifiers. They use a PSEUDONYMOUS case_uid. For research/demo data (MIMIC
demo, KTAS) identifiers are already de-identified, but this module enforces the
SAME discipline that will be mandatory for full MIMIC / real UHL data, so the
behaviour does not have to change when the data sensitivity changes.

Two functions matter:
  - pseudonymous_case_uid(): a STABLE, NON-REVERSIBLE token for a case, salted via
    a configurable secret. Same case -> same token (so a case's records correlate)
    but the token does not expose the underlying identifiers.
  - redact_for_log(): strips/replaces identifier-like keys in any dict before it
    is written to a log, leaving only the pseudonymous token + non-identifying
    fields.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any, Dict, Iterable, Optional


# Keys that must NEVER be written to a log in raw form. Conservative superset
# covering MIMIC, KTAS, and likely real-data identifier names.
IDENTIFIER_KEYS = {
    "subject_id", "patient_id", "mrn", "medical_record_number", "nhs_number",
    "hse_number", "name", "first_name", "last_name", "full_name", "dob",
    "date_of_birth", "address", "phone", "email", "ssn", "ppsn",
    # visit-level raw ids (kept out of logs; case_uid is the safe surrogate)
    "stay_id", "hadm_id", "visit_id", "encounter_id",
}

# Audit/model metadata fields that are safe and useful only if preserved exactly.
# These are not patient identifiers; redacting digits inside them would break audit
# ordering, provenance checks, version traceability, and hash comparison.
SAFE_METADATA_STRING_KEYS = {
    "timestamp_utc", "created_at_utc", "generated_at_utc", "updated_at_utc",
    "run_id", "workflow_run_id", "review_id", "rerun_id", "training_run_id",
    "model_hash", "model_artifact_sha256", "feature_schema_hash",
    "app_version", "backend_version", "model_version", "registry_schema_version",
    "package_checkpoint",
    "route", "path", "method", "role", "roles", "action", "decision",
    "permission", "auth_source", "record_kind", "source", "source_dataset",
    "status", "clinical_use", "current_mode",
}

# Fields intended to hold clinician/user/LLM text. These are scrubbed even when
# nested because they can accidentally include names, phone numbers, IDs, or dates.
FREE_TEXT_KEYS = {
    "chiefcomplaint", "chief_complaint", "review_comment", "clinician_note",
    "question", "answer", "llm_output", "detail", "note", "notes",
    "explanation", "reason", "override_reason", "clinician_decision",
    "clinician_override", "system_prediction",
}

# Free-text values that might embed identifiers (e.g. a chief complaint a clinician
# typed a name into). We redact obvious patterns defensively.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS_RE = re.compile(r"\b\d{7,}\b")  # long digit runs (MRN/phone-like)
# Phone-like numbers with separators, e.g. 555-123-4567, (555) 123 4567, +1 555 123 4567.
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
# Dates: 12/03/1980, 1980-12-03, 3.12.80, etc.
_DATE_RE = re.compile(r"\b\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}\b")
# US SSN-like 123-45-6789.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Two or more consecutive Capitalized words (possible person name). Conservative:
# requires both words >=2 letters. Clinical terms are usually lower-case in MIMIC
# chiefcomplaint, so this mainly catches injected names in free text.
_NAME_PAIR_RE = re.compile(r"\b[A-Z][a-z]{1,}\s+[A-Z][a-z]{1,}\b")


_DEV_SALT = "dev-only-pseudonym-salt-not-for-real-data"


def _pseudonym_secret() -> bytes:
    """Salt/key for pseudonymisation. In production this comes from Key Vault via
    the secrets provider. The fixed dev salt is fine for de-identified demo data
    only and is REFUSED in patient-data mode: handling real patient data with a
    public, hard-coded salt would make the pseudonyms predictable, so we fail
    closed and require a real secret (PSEUDONYM_SECRET locally, Key Vault in
    patient-data deployments)."""
    patient_mode = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    secrets_provider = os.environ.get("SECRETS_PROVIDER", "env").lower()

    if patient_mode and secrets_provider == "keyvault":
        if (
            os.environ.get("PSEUDONYM_SECRET")
            and os.environ.get("ALLOW_ENV_PSEUDONYM_SECRET_WITH_KEYVAULT", "").lower()
            != "true"
        ):
            raise PatientDataLeakError(
                "PATIENT_DATA_MODE with SECRETS_PROVIDER=keyvault refuses the "
                "plain environment PSEUDONYM_SECRET. Remove it and store the "
                "secret in Key Vault, or set the explicit dev-test override "
                "ALLOW_ENV_PSEUDONYM_SECRET_WITH_KEYVAULT=true."
            )
        try:
            from app.security.secrets_provider import get_secrets_provider
            secret = get_secrets_provider().get_secret("PSEUDONYM_SECRET")
        except Exception:
            secret = None
        if not secret:
            raise PatientDataLeakError(
                "PATIENT_DATA_MODE with SECRETS_PROVIDER=keyvault requires "
                "runtime retrieval of PSEUDONYM_SECRET from Key Vault."
            )
        return secret.encode("utf-8")

    secret = os.environ.get("PSEUDONYM_SECRET")
    if not secret:
        # Try the configured secrets provider for non-patient modes too, so local
        # integration tests can exercise the provider path without changing call
        # sites.
        try:
            from app.security.secrets_provider import get_secrets_provider
            secret = get_secrets_provider().get_secret("PSEUDONYM_SECRET")
        except Exception:
            secret = None
    if not secret:
        # No real secret available: only allowed outside patient-data and local
        # credentialed MIMIC research mode.
        patient_or_credentialed = (
            patient_mode
            or os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        )
        allow_dev = (
            os.environ.get("ALLOW_DEV_PSEUDONYM_SECRET_FOR_LOCAL_RESEARCH", "").lower()
            == "true"
        )
        if patient_or_credentialed and not allow_dev:
            raise PatientDataLeakError(
                "Refusing to pseudonymise patient data with the dev salt. Set a "
                "real PSEUDONYM_SECRET (from Key Vault for deployment) in "
                "patient-data or local credentialed research mode."
            )
        secret = _DEV_SALT
    return secret.encode("utf-8")


def pseudonymous_case_uid(source_dataset: Optional[str], stay_id: Any) -> str:
    """A stable, non-reversible pseudonym for a case. Same inputs -> same token.
    Format: '<dataset>~<24-hex>' so the dataset is still readable (it is not
    sensitive) but the visit id is not exposed."""
    msg = f"{source_dataset or 'UNKNOWN'}:{stay_id}".encode("utf-8")
    digest = hmac.new(_pseudonym_secret(), msg, hashlib.sha256).hexdigest()[:24]
    # Separator '~' is URL-safe (RFC 3986 unreserved) and does not appear in our
    # dataset labels, so it cleanly splits prefix from digest in a path segment.
    # ('#' would be truncated as a URL fragment; '.' collides with version dots
    # in labels like 'MIMIC-IV-ED-Demo-v2.2'.)
    return f"{source_dataset or 'UNKNOWN'}~{digest}"


def redact_text(value: str) -> str:
    """Redact obvious identifier patterns from a free-text string: emails, SSNs,
    phone-like numbers, dates, long digit runs, and consecutive Capitalized word
    pairs (possible names). Order matters (SSN/date before generic phone)."""
    if not isinstance(value, str):
        return value
    value = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = _SSN_RE.sub("[REDACTED_ID]", value)
    value = _DATE_RE.sub("[REDACTED_DATE]", value)
    value = _PHONE_RE.sub("[REDACTED_NUM]", value)
    value = _LONG_DIGITS_RE.sub("[REDACTED_NUM]", value)
    value = _NAME_PAIR_RE.sub("[REDACTED_NAME]", value)
    return value


def redact_for_log(data: Dict[str, Any], *, extra_keys: Optional[Iterable[str]] = None,
                   keep_case_uid: bool = True) -> Dict[str, Any]:
    """
    Return a copy of `data` safe to write to a log:
      - identifier keys removed (or replaced with a redaction marker),
      - free-text values scrubbed of obvious identifier patterns,
      - the pseudonymous case_uid retained (if present and keep_case_uid).
    Nested dicts/lists are processed recursively.
    """
    block = set(IDENTIFIER_KEYS)
    if extra_keys:
        block |= set(extra_keys)

    def _clean(obj: Any, parent_key: Optional[str] = None) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                kl = str(k).lower()
                if kl in block:
                    # Drop raw identifiers entirely.
                    continue
                if keep_case_uid and kl == "case_uid":
                    out[k] = v
                else:
                    out[k] = _clean(v, parent_key=kl)
            return out
        if isinstance(obj, list):
            return [_clean(x, parent_key=parent_key) for x in obj]
        if isinstance(obj, str):
            if keep_case_uid and parent_key == "case_uid":
                return obj
            if parent_key in SAFE_METADATA_STRING_KEYS:
                return obj
            if parent_key in FREE_TEXT_KEYS:
                return redact_text(obj)
            return redact_text(obj)
        return obj

    cleaned = _clean(data)
    return cleaned


def assert_no_raw_identifiers(record: Dict[str, Any]) -> None:
    """Raise if a record about to be logged still contains a raw identifier key.
    Use as a final guard before writing to any audit log."""
    def _scan(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k).lower() in IDENTIFIER_KEYS:
                    raise PatientDataLeakError(
                        f"Raw identifier '{k}' present at '{path}{k}' in a record "
                        "bound for a log. Use case_uid / pseudonymisation instead."
                    )
                _scan(v, f"{path}{k}.")
        elif isinstance(obj, list):
            for i, x in enumerate(obj):
                _scan(x, f"{path}{i}.")
    _scan(record)


class PatientDataLeakError(RuntimeError):
    """Raised when a record bound for a log still contains a raw identifier."""
