"""Recipient resolution for the strictly allowlisted personal demonstration."""
from __future__ import annotations

from dataclasses import dataclass

from app.notifications.models import mask_e164


def normalise_irish_mobile(value: str) -> str:
    try:
        import phonenumbers
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("phonenumbers is required for SMS recipient validation") from exc
    parsed = phonenumbers.parse(str(value or "").strip(), "IE")
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("the configured demo recipient is not a valid phone number")
    if phonenumbers.region_code_for_number(parsed) != "IE":
        raise ValueError("demo SMS routing permits only an Irish recipient")
    if phonenumbers.number_type(parsed) not in {
        phonenumbers.PhoneNumberType.MOBILE,
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
    }:
        raise ValueError("the configured demo recipient must be an Irish mobile number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


@dataclass(frozen=True)
class ResolvedRecipient:
    e164: str
    masked: str
    source: str


class DemoAllowlistRecipientResolver:
    """Routes every demo alert to exactly one Key-Vault-backed number."""

    def __init__(self, configured_value: str):
        self._recipient = normalise_irish_mobile(configured_value)

    def resolve(self, *, target_role: str, target_user_id: str = "") -> ResolvedRecipient:
        del target_role, target_user_id
        return ResolvedRecipient(
            e164=self._recipient,
            masked=mask_e164(self._recipient),
            source="demo_key_vault_allowlist",
        )
