"""Strict configuration for notification persistence, queueing, and ACS SMS."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re


_POLICY_VERSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True)
class SmsRolloutPolicy:
    """Versioned, durable charge-control policy read at dispatch time.

    App Settings remain the operator input, but a Function invocation cannot
    observe changes made after its process started. The active policy is
    therefore copied into the notification repository and point-read before
    submission. Versions are timestamp-prefixed and lexically ordered so an
    older process cannot overwrite a newer operator decision.
    """

    version: str
    sms_publish_enabled: bool
    sms_activated_at_utc: str
    sms_demo_case_uid_allowlist: tuple[str, ...]
    daily_limit: int
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not _POLICY_VERSION_RE.fullmatch(self.version):
            raise ValueError("SMS_ROLLOUT_POLICY_VERSION must be a safe 1-128 character identifier")
        if not 1 <= int(self.daily_limit) <= 100:
            raise ValueError("SMS rollout daily limit must be between 1 and 100")
        if self.sms_publish_enabled and not self.sms_activated_at_utc:
            raise ValueError("Enabled SMS rollout policy requires an activation timestamp")
        if self.sms_activated_at_utc:
            from app.notifications.models import canonical_time_key

            canonical_time_key(self.sms_activated_at_utc)
        for case_uid in self.sms_demo_case_uid_allowlist:
            if case_uid == "*":
                continue
            if not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", case_uid):
                raise ValueError("SMS rollout policy contains an invalid case identifier")
        if self.sms_publish_enabled and not self.sms_demo_case_uid_allowlist:
            raise ValueError("Enabled SMS rollout policy requires a case allowlist")

    def sms_eligibility(self, case_uid: str, event_at: str | datetime) -> tuple[bool, str]:
        if not self.sms_publish_enabled:
            return False, "publication_disabled"
        from app.notifications.models import parse_utc

        try:
            event_time = parse_utc(event_at)
            activated = parse_utc(self.sms_activated_at_utc)
        except (TypeError, ValueError):
            return False, "invalid_event_time"
        if event_time < activated:
            return False, "pre_activation_event"
        allowed = set(self.sms_demo_case_uid_allowlist)
        if "*" not in allowed and str(case_uid or "").strip() not in allowed:
            return False, "demo_case_not_allowlisted"
        return True, ""

    def as_storage_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "sms_publish_enabled": self.sms_publish_enabled,
            "sms_activated_at_utc": self.sms_activated_at_utc,
            "sms_demo_case_uid_allowlist_json": json.dumps(
                list(self.sms_demo_case_uid_allowlist), separators=(",", ":")
            ),
            "daily_limit": int(self.daily_limit),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_storage_dict(cls, value: dict[str, object]) -> "SmsRolloutPolicy":
        raw_allowlist = value.get("sms_demo_case_uid_allowlist_json") or "[]"
        allowlist = json.loads(str(raw_allowlist))
        if not isinstance(allowlist, list) or not all(isinstance(item, str) for item in allowlist):
            raise ValueError("Stored SMS rollout allowlist is invalid")
        return cls(
            version=str(value.get("version") or ""),
            sms_publish_enabled=bool(value.get("sms_publish_enabled")),
            sms_activated_at_utc=str(value.get("sms_activated_at_utc") or ""),
            sms_demo_case_uid_allowlist=tuple(allowlist),
            daily_limit=int(value.get("daily_limit") or 0),
            updated_at=str(value.get("updated_at") or ""),
        )

    def same_definition(self, other: "SmsRolloutPolicy") -> bool:
        return (
            self.version == other.version
            and self.sms_publish_enabled == other.sms_publish_enabled
            and self.sms_activated_at_utc == other.sms_activated_at_utc
            and self.sms_demo_case_uid_allowlist == other.sms_demo_case_uid_allowlist
            and int(self.daily_limit) == int(other.daily_limit)
        )


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class NotificationSettings:
    backend: str
    sqlite_path: Path
    table_endpoint: str
    table_name: str
    servicebus_fqdn: str
    dispatch_queue: str
    managed_identity_client_id: str
    sms_enabled: bool
    sms_publish_enabled: bool
    sms_sender: str
    sms_recipient_mode: str
    demo_recipient_e164: str
    acs_endpoint: str
    daily_limit: int
    retention_days: int
    retry_max_attempts: int
    retry_base_seconds: int
    sms_activated_at_utc: str = ""
    sms_demo_case_uid_allowlist: tuple[str, ...] = ()
    dispatch_min_age_seconds: int = 90
    worker_stale_seconds: int = 180
    build_id: str = "local"
    sms_rollout_policy_version: str = "00000000T000000000000000Z-local"

    @classmethod
    def from_env(cls) -> "NotificationSettings":
        backend = os.environ.get("NOTIFICATION_BACKEND", "sqlite").strip().lower()
        if backend not in {"sqlite", "azure_table"}:
            raise ValueError("NOTIFICATION_BACKEND must be sqlite or azure_table")
        sqlite_path_raw = os.environ.get("NOTIFICATION_SQLITE_PATH", "").strip()
        if sqlite_path_raw:
            sqlite_path = Path(sqlite_path_raw).expanduser()
        elif backend == "sqlite":
            # Reuse the application's resolved runtime directory. This keeps
            # tests, local development, and ALTER_DATA_ROOT deployments aligned
            # instead of accidentally writing relative to the process cwd.
            from app.config import settings as app_settings

            sqlite_path = app_settings.processed_dir / "notifications.sqlite3"
        else:
            # Azure Table mode never opens SQLite, but the frozen settings model
            # intentionally remains complete and deterministic.
            sqlite_path = Path("/tmp/alter-unused-notifications.sqlite3")
        sms_enabled = _bool("SMS_ENABLED", False)
        sms_publish_enabled = _bool("SMS_PUBLISH_ENABLED", sms_enabled)
        sender = os.environ.get("SMS_SENDER", "ALTER").strip()
        recipient_mode = os.environ.get("SMS_RECIPIENT_MODE", "demo_allowlist").strip().lower()
        if recipient_mode != "demo_allowlist":
            raise ValueError(
                "Only SMS_RECIPIENT_MODE=demo_allowlist is implemented; production staff routing "
                "must not silently fall back to the demonstration recipient"
            )
        raw_allowlist = os.environ.get("SMS_DEMO_CASE_UID_ALLOWLIST", "").strip()
        allowlist = tuple(
            dict.fromkeys(part.strip() for part in raw_allowlist.split(",") if part.strip())
        )
        value = cls(
            backend=backend,
            sqlite_path=sqlite_path,
            table_endpoint=os.environ.get("NOTIFICATION_TABLE_ENDPOINT", "").strip(),
            table_name=os.environ.get("NOTIFICATION_TABLE_NAME", "alternotifications").strip(),
            servicebus_fqdn=os.environ.get("SERVICEBUS_FQDN", "").strip(),
            dispatch_queue=os.environ.get("SMS_DISPATCH_QUEUE", "alter-sms-dispatch").strip(),
            managed_identity_client_id=os.environ.get(
                "NOTIFICATION_MANAGED_IDENTITY_CLIENT_ID",
                os.environ.get("AZURE_CLIENT_ID", ""),
            ).strip(),
            sms_enabled=sms_enabled,
            sms_publish_enabled=sms_publish_enabled,
            sms_sender=sender,
            sms_recipient_mode=recipient_mode,
            demo_recipient_e164=os.environ.get("DEMO_SMS_RECIPIENT", "").strip(),
            acs_endpoint=os.environ.get("ACS_ENDPOINT", "").strip(),
            daily_limit=_int("SMS_DAILY_LIMIT", 100, 1, 100),
            retention_days=_int("NOTIFICATION_RETENTION_DAYS", 90, 1, 365),
            retry_max_attempts=_int("SMS_RETRY_MAX_ATTEMPTS", 3, 1, 5),
            retry_base_seconds=_int("SMS_RETRY_BASE_SECONDS", 60, 10, 3600),
            sms_activated_at_utc=os.environ.get("SMS_ACTIVATED_AT_UTC", "").strip(),
            sms_demo_case_uid_allowlist=allowlist,
            dispatch_min_age_seconds=_int("SMS_DISPATCH_MIN_AGE_SECONDS", 90, 0, 3600),
            worker_stale_seconds=_int("NOTIFICATION_WORKER_STALE_SECONDS", 180, 60, 3600),
            build_id=os.environ.get("ALTER_BUILD_ID", "local").strip() or "local",
            sms_rollout_policy_version=os.environ.get(
                "SMS_ROLLOUT_POLICY_VERSION", "00000000T000000000000000Z-local"
            ).strip(),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if not _POLICY_VERSION_RE.fullmatch(self.sms_rollout_policy_version):
            raise ValueError(
                "SMS_ROLLOUT_POLICY_VERSION must be a safe 1-128 character identifier"
            )
        if not self.table_name.isalnum():
            raise ValueError("NOTIFICATION_TABLE_NAME must contain only letters and numbers")
        if self.backend == "azure_table" and not self.table_endpoint:
            raise ValueError("NOTIFICATION_TABLE_ENDPOINT is required for azure_table")
        if self.sms_sender != "ALTER":
            raise ValueError("SMS_SENDER must be the approved sender ID ALTER")
        if self.sms_publish_enabled:
            missing_publish = [
                name
                for name, value in {
                    "SERVICEBUS_FQDN": self.servicebus_fqdn,
                    "NOTIFICATION_TABLE_ENDPOINT": self.table_endpoint,
                }.items()
                if not value
            ]
            if missing_publish:
                raise ValueError("SMS_PUBLISH_ENABLED=true requires " + ", ".join(missing_publish))
            if self.backend != "azure_table":
                raise ValueError("SMS_PUBLISH_ENABLED=true requires NOTIFICATION_BACKEND=azure_table")
            if not self.sms_activated_at_utc:
                raise ValueError("SMS_PUBLISH_ENABLED=true requires SMS_ACTIVATED_AT_UTC")
            from app.notifications.models import canonical_time_key

            canonical_time_key(self.sms_activated_at_utc)
            if not self.sms_demo_case_uid_allowlist:
                raise ValueError(
                    "SMS_PUBLISH_ENABLED=true requires SMS_DEMO_CASE_UID_ALLOWLIST; "
                    "use one canary case first and only use * after the canary is verified"
                )
        for case_uid in self.sms_demo_case_uid_allowlist:
            if case_uid == "*":
                continue
            if not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", case_uid):
                raise ValueError(
                    "SMS_DEMO_CASE_UID_ALLOWLIST contains an invalid case identifier"
                )
        if self.sms_enabled:
            missing = [
                name
                for name, value in {
                    "ACS_ENDPOINT": self.acs_endpoint,
                    "DEMO_SMS_RECIPIENT": self.demo_recipient_e164,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError("SMS_ENABLED=true requires " + ", ".join(missing))
            if not self.sms_publish_enabled:
                raise ValueError("SMS_ENABLED=true requires SMS_PUBLISH_ENABLED=true")

    def sms_eligibility(self, case_uid: str, event_at: str | datetime) -> tuple[bool, str]:
        """Return fail-closed demo eligibility without changing in-app visibility."""
        if not self.sms_publish_enabled:
            return False, "publication_disabled"
        from app.notifications.models import parse_utc

        try:
            event_time = parse_utc(event_at)
            activated = parse_utc(self.sms_activated_at_utc)
        except (TypeError, ValueError):
            return False, "invalid_event_time"
        if event_time < activated:
            return False, "pre_activation_event"
        allowed = set(self.sms_demo_case_uid_allowlist)
        if "*" not in allowed and str(case_uid or "").strip() not in allowed:
            return False, "demo_case_not_allowlisted"
        return True, ""

    def rollout_policy(self, *, updated_at: str = "") -> SmsRolloutPolicy:
        return SmsRolloutPolicy(
            version=self.sms_rollout_policy_version,
            sms_publish_enabled=self.sms_publish_enabled,
            sms_activated_at_utc=self.sms_activated_at_utc,
            sms_demo_case_uid_allowlist=self.sms_demo_case_uid_allowlist,
            daily_limit=self.daily_limit,
            updated_at=updated_at,
        )


def validate_sms_startup() -> NotificationSettings:
    """Fail closed before a live worker can consume chargeable SMS work."""
    settings = NotificationSettings.from_env()
    if settings.sms_enabled:
        from app.notifications.recipient import normalise_irish_mobile

        normalise_irish_mobile(settings.demo_recipient_e164)
    return settings
