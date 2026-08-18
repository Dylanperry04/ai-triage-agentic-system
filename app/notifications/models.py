"""Domain records and validation for durable in-app notifications and SMS work."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any


ALLOWED_NOTIFICATION_KINDS = {"overdue_vitals", "escalation", "clinical_alert", "information_request"}
ALLOWED_TARGET_ROLES = {"triage_nurse", "ed_doctor", "clinical_supervisor", "security_admin"}
TERMINAL_SMS_STATES = {
    "submitted", "delivered", "failed_permanent", "ambiguous",
    "cap_blocked", "disabled", "cancelled",
}
SMS_TEMPLATES = {
    "overdue_vitals": "ALTER: Vitals review is due. Sign in to ALTER securely.",
    "escalation": "ALTER: A clinical alert needs your attention. Sign in securely.",
    "clinical_alert": "ALTER: A clinical alert needs your attention. Sign in securely.",
    "information_request": "ALTER: More information has been requested. Sign in to ALTER securely.",
}

# The one-segment templates are ASCII and use only this GSM-7 basic alphabet.
_GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:~-]{1,256}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    current = value or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def canonical_time_key(value: str | datetime) -> str:
    """Canonical UTC identity for time-derived events.

    Naive values retain the application's established contract and are defined
    as UTC. Six fractional digits are always emitted, so ``Z``, ``+00:00`` and
    equivalent offsets hash to one event while distinct microseconds remain
    distinct.
    """
    return parse_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def stable_id(prefix: str, *parts: str) -> str:
    material = "\x1f".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]
    return f"{prefix}-{digest}"


def validate_identifier(name: str, value: str) -> str:
    cleaned = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(cleaned):
        raise ValueError(f"{name} must be 1-256 characters from the safe identifier alphabet")
    return cleaned


def sms_template(kind: str) -> str:
    if kind not in SMS_TEMPLATES:
        return SMS_TEMPLATES["clinical_alert"]
    return SMS_TEMPLATES[kind]


def assert_one_segment_gsm7(message: str) -> None:
    if not message or len(message) > 160:
        raise ValueError("SMS template must contain 1-160 GSM-7 characters")
    unsupported = sorted(set(message) - _GSM7_BASIC)
    if unsupported:
        raise ValueError("SMS template contains characters outside the GSM-7 basic alphabet")


def mask_e164(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return f"+***{digits[-4:]}" if len(digits) >= 4 else "+***"


@dataclass(frozen=True)
class NotificationRecord:
    notification_id: str
    kind: str
    case_uid: str
    event_key: str
    target_role: str
    title: str
    body: str
    created_at: str
    expires_at: str
    target_user_id: str = ""
    active: bool = True
    sms_state: str = "queued"
    queue_published_at: str = ""
    attempt_count: int = 0
    claim_owner: str = ""
    claim_until: str = ""
    retry_at: str = ""
    budget_day: str = ""
    acs_message_id: str = ""
    recipient_masked: str = ""
    delivery_status: str = ""
    delivery_updated_at: str = ""
    error_code: str = ""
    updated_at: str = ""
    sms_eligible: bool = False
    sms_ineligible_reason: str = ""
    outbox_generation: int = 1
    publish_claim_owner: str = ""
    publish_claim_until: str = ""
    cancel_reason: str = ""
    cancelled_at: str = ""
    acknowledged_at: str = ""
    acknowledged_by: str = ""
    rollout_policy_version: str = ""

    def __post_init__(self) -> None:
        validate_identifier("notification_id", self.notification_id)
        validate_identifier("case_uid", self.case_uid)
        if self.kind not in ALLOWED_NOTIFICATION_KINDS:
            raise ValueError(f"unsupported notification kind: {self.kind}")
        if self.target_role not in ALLOWED_TARGET_ROLES:
            raise ValueError(f"unsupported target role: {self.target_role}")
        parse_utc(self.created_at)
        parse_utc(self.expires_at)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        case_uid: str,
        event_key: str,
        target_role: str,
        title: str,
        body: str,
        created_at: str | None = None,
        target_user_id: str = "",
        sms_enabled: bool = False,
        sms_eligible: bool | None = None,
        sms_ineligible_reason: str = "",
        active: bool = True,
        retention_days: int = 90,
    ) -> "NotificationRecord":
        created = parse_utc(created_at) if created_at else utc_now()
        event = str(event_key or "").strip()
        notification_id = stable_id("ntf-v1", kind, case_uid, event)
        eligible = bool(sms_enabled if sms_eligible is None else sms_eligible)
        return cls(
            notification_id=notification_id,
            kind=kind,
            case_uid=case_uid,
            event_key=event,
            target_role=target_role,
            target_user_id=target_user_id,
            title=title,
            body=body,
            created_at=utc_iso(created),
            expires_at=utc_iso(created + timedelta(days=retention_days)),
            active=active,
            sms_state="queued" if eligible and active else "disabled",
            sms_eligible=eligible,
            sms_ineligible_reason="" if eligible else (sms_ineligible_reason or "publication_disabled"),
            updated_at=utc_iso(created),
        )


@dataclass(frozen=True)
class ScheduleRecord:
    schedule_id: str
    case_uid: str
    kind: str
    version: str
    reference_at: str
    due_at: str
    target_role: str
    active: bool
    updated_at: str
    queue_published_at: str = ""
    sms_eligible: bool = False
    sms_ineligible_reason: str = ""
    outbox_generation: int = 1
    publish_claim_owner: str = ""
    publish_claim_until: str = ""
    materialized_notification_id: str = ""

    def __post_init__(self) -> None:
        validate_identifier("schedule_id", self.schedule_id)
        validate_identifier("case_uid", self.case_uid)
        if self.kind != "overdue_vitals":
            raise ValueError("only overdue_vitals schedules are supported")
        if self.target_role not in ALLOWED_TARGET_ROLES:
            raise ValueError(f"unsupported target role: {self.target_role}")
        parse_utc(self.reference_at)
        parse_utc(self.due_at)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(
        cls,
        *,
        case_uid: str,
        reference_at: str,
        due_minutes: int,
        target_role: str,
        active: bool = True,
        sms_eligible: bool = False,
        sms_ineligible_reason: str = "",
    ) -> "ScheduleRecord":
        reference = parse_utc(reference_at)
        reference_key = canonical_time_key(reference)
        version = stable_id("v1", case_uid, reference_key)
        return cls(
            schedule_id=stable_id("sch-v1", "overdue_vitals", case_uid),
            case_uid=case_uid,
            kind="overdue_vitals",
            version=version,
            reference_at=reference_key,
            due_at=utc_iso(reference + timedelta(minutes=due_minutes)),
            target_role=target_role,
            active=active,
            updated_at=utc_iso(),
            sms_eligible=sms_eligible,
            sms_ineligible_reason="" if sms_eligible else (
                sms_ineligible_reason or "publication_disabled"
            ),
        )
