"""Repository contract with transactional SQLite and ETag-safe Azure Table stores."""
from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta
from contextlib import contextmanager
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

from app.notifications.config import NotificationSettings, SmsRolloutPolicy
from app.notifications.models import (
    NotificationRecord,
    ScheduleRecord,
    TERMINAL_SMS_STATES,
    parse_utc,
    utc_iso,
    utc_now,
)


class NotificationRepository(Protocol):
    def create_notification(self, record: NotificationRecord) -> tuple[NotificationRecord, bool]: ...
    def get_notification(self, notification_id: str) -> NotificationRecord | None: ...
    def get_rollout_policy(self) -> SmsRolloutPolicy | None: ...
    def upsert_rollout_policy(self, policy: SmsRolloutPolicy) -> SmsRolloutPolicy: ...
    def list_notifications(self, *, roles: Iterable[str], user_id: str, limit: int, offset: int = 0) -> list[dict[str, Any]]: ...
    def count_notifications(self, *, roles: Iterable[str], user_id: str) -> int: ...
    def mark_read(self, notification_id: str, user_id: str, read_at: str) -> bool: ...
    def acknowledge_notification(self, notification_id: str, user_id: str, acknowledged_at: str) -> bool: ...
    def deactivate_notifications(
        self, case_uid: str, kind: str | None, updated_at: str, *,
        cancel_sms: bool = True, cancel_reason: str = "event_obsolete",
    ) -> int: ...
    def deactivate_notifications_except_event(
        self, case_uid: str, kind: str, event_key: str, updated_at: str, *,
        cancel_reason: str = "event_replaced",
    ) -> int: ...
    def cancel_notification(self, notification_id: str, *, reason: str, now: str) -> bool: ...
    def activate_notification(self, notification_id: str, *, now: str) -> NotificationRecord | None: ...
    def upsert_schedule(self, record: ScheduleRecord) -> tuple[ScheduleRecord, bool]: ...
    def get_schedule(self, schedule_id: str) -> ScheduleRecord | None: ...
    def cancel_schedule(self, schedule_id: str) -> None: ...
    def consume_schedule_if_version(
        self, schedule_id: str, expected_version: str, expected_generation: int,
        notification_id: str, now: str,
    ) -> bool: ...
    def requeue_schedule_generation(
        self, schedule_id: str, expected_version: str, expected_generation: int, now: str,
    ) -> bool: ...
    def list_unpublished(self, *, limit: int) -> list[tuple[str, dict[str, Any]]]: ...
    def claim_publication(
        self, entity_type: str, entity_id: str, expected_generation: str,
        owner: str, now: str, lease_seconds: int,
    ) -> bool: ...
    def mark_published(
        self, entity_type: str, entity_id: str, expected_generation: str,
        owner: str, published_at: str,
    ) -> bool: ...
    def release_publication(
        self, entity_type: str, entity_id: str, expected_generation: str, owner: str,
    ) -> None: ...
    def claim_dispatch(
        self, notification_id: str, expected_generation: int,
        owner: str, now: str, lease_seconds: int,
    ) -> str: ...
    def reserve_attempt(
        self, notification_id: str, expected_generation: int,
        owner: str, day: str, limit: int,
    ) -> str: ...
    def mark_sending(
        self, notification_id: str, expected_generation: int, owner: str,
        now: str, rollout_policy_version: str,
    ) -> bool: ...
    def revalidate_dispatch(
        self, notification_id: str, expected_generation: int, owner: str, expected_state: str,
    ) -> bool: ...
    def cancel_sending_before_submit(
        self, notification_id: str, expected_generation: int, owner: str, now: str,
    ) -> bool: ...
    def cancel_dispatch_for_policy(
        self, notification_id: str, expected_generation: int, owner: str,
        expected_state: str, reason: str, now: str,
    ) -> bool: ...
    def requeue_unclaimed_generation(
        self, notification_id: str, expected_generation: int, now: str,
    ) -> bool: ...
    def defer_disabled_dispatch(
        self, notification_id: str, expected_generation: int, now: str,
    ) -> str: ...
    def mark_preflight_failure(self, notification_id: str, expected_generation: int, owner: str, *, error_code: str, now: str) -> None: ...
    def mark_submitted(self, notification_id: str, expected_generation: int, owner: str, *, message_id: str, masked: str, now: str) -> None: ...
    def mark_retryable(self, notification_id: str, expected_generation: int, owner: str, *, error_code: str, retry_at: str, now: str) -> None: ...
    def mark_terminal_failure(self, notification_id: str, expected_generation: int, owner: str, *, state: str, error_code: str, now: str) -> None: ...
    def recover_stale_sending(self, now: str) -> int: ...
    def recover_expired_claims(self, now: str) -> int: ...
    def record_delivery(self, *, event_id: str, message_id: str, status: str, observed_at: str, detail_code: str) -> bool: ...
    def repair_delivery_state(self, *, limit: int = 500) -> dict[str, int]: ...
    def record_worker_heartbeat(
        self, *, component: str, build_id: str, status: str, observed_at: str,
        sms_enabled: bool, sms_publish_enabled: bool,
    ) -> None: ...
    def pre_enable_report(self, *, allowed_case_uids: set[str]) -> dict[str, Any]: ...
    def purge_expired(self, now: str, retention_days: int = 90) -> dict[str, int]: ...
    def health(self) -> dict[str, Any]: ...


_NOTIFICATION_FIELDS = {field.name for field in fields(NotificationRecord)}
_SCHEDULE_FIELDS = {field.name for field in fields(ScheduleRecord)}


def _notification_from_mapping(value: dict[str, Any]) -> NotificationRecord:
    payload = {key: value[key] for key in _NOTIFICATION_FIELDS if key in value}
    for key in ("active", "sms_eligible"):
        if key in payload:
            payload[key] = bool(payload[key])
    for key in ("attempt_count", "outbox_generation"):
        if key in payload:
            payload[key] = int(payload[key] or 0)
    return NotificationRecord(**payload)


def _schedule_from_mapping(value: dict[str, Any]) -> ScheduleRecord:
    payload = {key: value[key] for key in _SCHEDULE_FIELDS if key in value}
    for key in ("active", "sms_eligible"):
        if key in payload:
            payload[key] = bool(payload[key])
    if "outbox_generation" in payload:
        payload["outbox_generation"] = int(payload["outbox_generation"] or 1)
    return ScheduleRecord(**payload)


class SQLiteNotificationRepository:
    """Local/dev repository; SQLite transactions also exercise concurrency invariants."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialise()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialise(self) -> None:
        with self._init_lock, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    case_uid TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    target_role TEXT NOT NULL,
                    target_user_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    sms_state TEXT NOT NULL,
                    queue_published_at TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    claim_owner TEXT NOT NULL DEFAULT '',
                    claim_until TEXT NOT NULL DEFAULT '',
                    retry_at TEXT NOT NULL DEFAULT '',
                    budget_day TEXT NOT NULL DEFAULT '',
                    acs_message_id TEXT NOT NULL DEFAULT '',
                    recipient_masked TEXT NOT NULL DEFAULT '',
                    delivery_status TEXT NOT NULL DEFAULT '',
                    delivery_updated_at TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    sms_eligible INTEGER NOT NULL DEFAULT 0,
                    sms_ineligible_reason TEXT NOT NULL DEFAULT '',
                    outbox_generation INTEGER NOT NULL DEFAULT 1,
                    publish_claim_owner TEXT NOT NULL DEFAULT '',
                    publish_claim_until TEXT NOT NULL DEFAULT '',
                    cancel_reason TEXT NOT NULL DEFAULT '',
                    cancelled_at TEXT NOT NULL DEFAULT '',
                    acknowledged_at TEXT NOT NULL DEFAULT '',
                    acknowledged_by TEXT NOT NULL DEFAULT '',
                    rollout_policy_version TEXT NOT NULL DEFAULT '',
                    UNIQUE(kind, case_uid, event_key)
                );
                CREATE INDEX IF NOT EXISTS ix_notifications_visible
                    ON notifications(active, target_role, created_at DESC);
                CREATE INDEX IF NOT EXISTS ix_notifications_acs
                    ON notifications(acs_message_id);
                CREATE TABLE IF NOT EXISTS notification_reads (
                    notification_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    read_at TEXT NOT NULL,
                    PRIMARY KEY(notification_id, user_id),
                    FOREIGN KEY(notification_id) REFERENCES notifications(notification_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    schedule_id TEXT PRIMARY KEY,
                    case_uid TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    version TEXT NOT NULL,
                    reference_at TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    target_role TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    queue_published_at TEXT NOT NULL DEFAULT '',
                    sms_eligible INTEGER NOT NULL DEFAULT 0,
                    sms_ineligible_reason TEXT NOT NULL DEFAULT '',
                    outbox_generation INTEGER NOT NULL DEFAULT 1,
                    publish_claim_owner TEXT NOT NULL DEFAULT '',
                    publish_claim_until TEXT NOT NULL DEFAULT '',
                    materialized_notification_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS sms_budgets (
                    utc_day TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS delivery_events (
                    event_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    detail_code TEXT NOT NULL DEFAULT '',
                    applied INTEGER NOT NULL DEFAULT 0,
                    applied_at TEXT NOT NULL DEFAULT '',
                    apply_error_code TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS operational_alerts (
                    alert_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    detail_code TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_heartbeats (
                    component TEXT PRIMARY KEY,
                    build_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    sms_enabled INTEGER NOT NULL,
                    sms_publish_enabled INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sms_rollout_policy (
                    policy_id TEXT PRIMARY KEY CHECK(policy_id='active'),
                    version TEXT NOT NULL,
                    sms_publish_enabled INTEGER NOT NULL,
                    sms_activated_at_utc TEXT NOT NULL,
                    sms_demo_case_uid_allowlist_json TEXT NOT NULL,
                    daily_limit INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_columns(db, "notifications", {
                "sms_eligible": "INTEGER NOT NULL DEFAULT 0",
                "sms_ineligible_reason": "TEXT NOT NULL DEFAULT ''",
                "outbox_generation": "INTEGER NOT NULL DEFAULT 1",
                "publish_claim_owner": "TEXT NOT NULL DEFAULT ''",
                "publish_claim_until": "TEXT NOT NULL DEFAULT ''",
                "cancel_reason": "TEXT NOT NULL DEFAULT ''",
                "cancelled_at": "TEXT NOT NULL DEFAULT ''",
                "acknowledged_at": "TEXT NOT NULL DEFAULT ''",
                "acknowledged_by": "TEXT NOT NULL DEFAULT ''",
                "rollout_policy_version": "TEXT NOT NULL DEFAULT ''",
            })
            self._ensure_columns(db, "schedules", {
                "sms_eligible": "INTEGER NOT NULL DEFAULT 0",
                "sms_ineligible_reason": "TEXT NOT NULL DEFAULT ''",
                "outbox_generation": "INTEGER NOT NULL DEFAULT 1",
                "publish_claim_owner": "TEXT NOT NULL DEFAULT ''",
                "publish_claim_until": "TEXT NOT NULL DEFAULT ''",
                "materialized_notification_id": "TEXT NOT NULL DEFAULT ''",
            })
            self._ensure_columns(db, "delivery_events", {
                "applied": "INTEGER NOT NULL DEFAULT 0",
                "applied_at": "TEXT NOT NULL DEFAULT ''",
                "apply_error_code": "TEXT NOT NULL DEFAULT ''",
            })

    @staticmethod
    def _ensure_columns(
        db: sqlite3.Connection, table: str, definitions: dict[str, str]
    ) -> None:
        existing = {
            str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in definitions.items():
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def create_notification(self, record: NotificationRecord) -> tuple[NotificationRecord, bool]:
        values = record.as_dict()
        columns = list(values)
        with self._connect() as db:
            try:
                db.execute(
                    f"INSERT INTO notifications ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    [int(value) if isinstance(value, bool) else value for value in values.values()],
                )
                return record, True
            except sqlite3.IntegrityError:
                existing = self.get_notification(record.notification_id)
                if existing is None:
                    row = db.execute(
                        "SELECT * FROM notifications WHERE kind=? AND case_uid=? AND event_key=?",
                        (record.kind, record.case_uid, record.event_key),
                    ).fetchone()
                    existing = _notification_from_mapping(dict(row)) if row else None
                if existing is None:
                    raise
                return existing, False

    def get_notification(self, notification_id: str) -> NotificationRecord | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM notifications WHERE notification_id=?", (notification_id,)
            ).fetchone()
        return _notification_from_mapping(dict(row)) if row else None

    def get_rollout_policy(self) -> SmsRolloutPolicy | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM sms_rollout_policy WHERE policy_id='active'"
            ).fetchone()
        return SmsRolloutPolicy.from_storage_dict(dict(row)) if row else None

    def upsert_rollout_policy(self, policy: SmsRolloutPolicy) -> SmsRolloutPolicy:
        """Promote a policy monotonically; reject version reuse with new values."""
        candidate = replace(policy, updated_at=policy.updated_at or utc_iso())
        payload = candidate.as_storage_dict()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM sms_rollout_policy WHERE policy_id='active'"
            ).fetchone()
            if row is not None:
                current = SmsRolloutPolicy.from_storage_dict(dict(row))
                if candidate.version < current.version:
                    db.execute("COMMIT")
                    return current
                if candidate.version == current.version:
                    db.execute("COMMIT")
                    if not candidate.same_definition(current):
                        raise ValueError(
                            "SMS rollout policy version was reused with different settings"
                        )
                    return current
            db.execute(
                """
                INSERT INTO sms_rollout_policy(
                    policy_id,version,sms_publish_enabled,sms_activated_at_utc,
                    sms_demo_case_uid_allowlist_json,daily_limit,updated_at
                ) VALUES('active',?,?,?,?,?,?)
                ON CONFLICT(policy_id) DO UPDATE SET
                    version=excluded.version,
                    sms_publish_enabled=excluded.sms_publish_enabled,
                    sms_activated_at_utc=excluded.sms_activated_at_utc,
                    sms_demo_case_uid_allowlist_json=excluded.sms_demo_case_uid_allowlist_json,
                    daily_limit=excluded.daily_limit,
                    updated_at=excluded.updated_at
                """,
                (
                    payload["version"], int(bool(payload["sms_publish_enabled"])),
                    payload["sms_activated_at_utc"],
                    payload["sms_demo_case_uid_allowlist_json"],
                    payload["daily_limit"], payload["updated_at"],
                ),
            )
            db.execute("COMMIT")
        return candidate

    def list_notifications(
        self, *, roles: Iterable[str], user_id: str, limit: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        role_values = sorted({str(role) for role in roles if role})
        if not role_values:
            return []
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        placeholders = ",".join("?" for _ in role_values)
        now = utc_iso()
        params: list[Any] = [user_id, now, *role_values, user_id, limit, offset]
        query = f"""
            SELECT n.*, CASE WHEN r.read_at IS NULL THEN 0 ELSE 1 END AS is_read,
                   COALESCE(r.read_at, '') AS read_at
            FROM notifications n
            LEFT JOIN notification_reads r
              ON r.notification_id=n.notification_id AND r.user_id=?
            WHERE n.active=1 AND n.expires_at>? AND n.target_role IN ({placeholders})
              AND (n.target_user_id='' OR n.target_user_id=?)
            ORDER BY n.created_at DESC LIMIT ? OFFSET ?
        """
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def count_notifications(self, *, roles: Iterable[str], user_id: str) -> int:
        role_values = sorted({str(role) for role in roles if role})
        if not role_values:
            return 0
        placeholders = ",".join("?" for _ in role_values)
        with self._connect() as db:
            row = db.execute(
                f"SELECT COUNT(*) AS count FROM notifications WHERE active=1 "
                f"AND expires_at>? AND target_role IN ({placeholders}) "
                "AND (target_user_id='' OR target_user_id=?)",
                [utc_iso(), *role_values, user_id],
            ).fetchone()
        return int(row["count"] if row else 0)

    def mark_read(self, notification_id: str, user_id: str, read_at: str) -> bool:
        with self._connect() as db:
            exists = db.execute(
                "SELECT 1 FROM notifications WHERE notification_id=?", (notification_id,)
            ).fetchone()
            if not exists:
                return False
            db.execute(
                "INSERT INTO notification_reads(notification_id,user_id,read_at) VALUES(?,?,?) "
                "ON CONFLICT(notification_id,user_id) DO UPDATE SET read_at=excluded.read_at",
                (notification_id, user_id, read_at),
            )
            return True

    def acknowledge_notification(
        self, notification_id: str, user_id: str, acknowledged_at: str
    ) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute(
                "SELECT acknowledged_at FROM notifications WHERE notification_id=?",
                (notification_id,),
            ).fetchone()
            if not exists:
                db.execute("ROLLBACK")
                return False
            db.execute(
                "INSERT INTO notification_reads(notification_id,user_id,read_at) VALUES(?,?,?) "
                "ON CONFLICT(notification_id,user_id) DO UPDATE SET read_at=excluded.read_at",
                (notification_id, user_id, acknowledged_at),
            )
            # First acknowledgement wins.  Do not change sms_state:
            # acknowledgement after creation never cancels the one required
            # SMS for this immutable event.
            if not str(exists["acknowledged_at"] or ""):
                db.execute(
                    "UPDATE notifications SET active=0,acknowledged_at=?,acknowledged_by=?,updated_at=? "
                    "WHERE notification_id=? AND acknowledged_at=''",
                    (acknowledged_at, user_id, acknowledged_at, notification_id),
                )
            db.execute("COMMIT")
            return True

    @staticmethod
    def _cancel_sql(reason: str, now: str) -> tuple[str, list[Any]]:
        return (
            "sms_state=CASE WHEN sms_state IN ('queued','retryable','claimed') "
            "THEN 'cancelled' ELSE sms_state END, "
            "cancel_reason=CASE WHEN sms_state IN ('queued','retryable','claimed') THEN ? "
            "WHEN sms_state IN ('sending','submitted') THEN ? ELSE cancel_reason END, "
            "cancelled_at=CASE WHEN sms_state IN ('queued','retryable','claimed','sending','submitted') "
            "THEN ? ELSE cancelled_at END, claim_owner=CASE WHEN sms_state IN "
            "('queued','retryable','claimed') THEN '' ELSE claim_owner END, "
            "claim_until=CASE WHEN sms_state IN ('queued','retryable','claimed') THEN '' ELSE claim_until END, ",
            [reason, f"too_late:{reason}", now],
        )

    def deactivate_notifications(
        self, case_uid: str, kind: str | None, updated_at: str, *,
        cancel_sms: bool = True, cancel_reason: str = "event_obsolete",
    ) -> int:
        cancel_sql, cancel_params = self._cancel_sql(cancel_reason, updated_at)
        updates = f"active=0,{cancel_sql}updated_at=?" if cancel_sms else "active=0,updated_at=?"
        with self._connect() as db:
            if kind:
                result = db.execute(
                    f"UPDATE notifications SET {updates} WHERE case_uid=? AND kind=? AND active=1",
                    [*(cancel_params if cancel_sms else []), updated_at, case_uid, kind],
                )
            else:
                result = db.execute(
                    f"UPDATE notifications SET {updates} WHERE case_uid=? AND active=1",
                    [*(cancel_params if cancel_sms else []), updated_at, case_uid],
                )
            return int(result.rowcount)

    def deactivate_notifications_except_event(
        self, case_uid: str, kind: str, event_key: str, updated_at: str, *,
        cancel_reason: str = "event_replaced",
    ) -> int:
        cancel_sql, cancel_params = self._cancel_sql(cancel_reason, updated_at)
        with self._connect() as db:
            result = db.execute(
                f"UPDATE notifications SET active=0,{cancel_sql}updated_at=? "
                "WHERE case_uid=? AND kind=? AND event_key<>? AND active=1",
                [*cancel_params, updated_at, case_uid, kind, event_key],
            )
            return int(result.rowcount)

    def cancel_notification(self, notification_id: str, *, reason: str, now: str) -> bool:
        cancel_sql, params = self._cancel_sql(reason, now)
        with self._connect() as db:
            result = db.execute(
                f"UPDATE notifications SET active=0,{cancel_sql}updated_at=? "
                "WHERE notification_id=?",
                [*params, now, notification_id],
            )
        return result.rowcount == 1

    def activate_notification(self, notification_id: str, *, now: str) -> NotificationRecord | None:
        with self._connect() as db:
            db.execute(
                "UPDATE notifications SET active=1,sms_state=CASE WHEN sms_eligible=1 "
                "THEN 'queued' ELSE 'disabled' END,updated_at=? "
                "WHERE notification_id=? AND active=0 AND sms_state='disabled' "
                "AND cancel_reason=''",
                (now, notification_id),
            )
        return self.get_notification(notification_id)

    def upsert_schedule(self, record: ScheduleRecord) -> tuple[ScheduleRecord, bool]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM schedules WHERE schedule_id=?", (record.schedule_id,)
            ).fetchone()
            changed = previous is None or str(previous["version"]) != record.version or not bool(previous["active"])
            published = "" if changed else str(previous["queue_published_at"] or "")
            generation = (
                int(previous["outbox_generation"] or 1) + 1
                if changed and previous is not None
                else int((previous or {"outbox_generation": record.outbox_generation})["outbox_generation"] or 1)
            )
            db.execute(
                """
                INSERT INTO schedules(schedule_id,case_uid,kind,version,reference_at,due_at,target_role,active,updated_at,queue_published_at,sms_eligible,sms_ineligible_reason,outbox_generation,publish_claim_owner,publish_claim_until,materialized_notification_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(schedule_id) DO UPDATE SET
                  case_uid=excluded.case_uid, kind=excluded.kind, version=excluded.version,
                  reference_at=excluded.reference_at, due_at=excluded.due_at,
                  target_role=excluded.target_role, active=excluded.active,
                  updated_at=excluded.updated_at, queue_published_at=excluded.queue_published_at,
                  sms_eligible=excluded.sms_eligible,
                  sms_ineligible_reason=excluded.sms_ineligible_reason,
                  outbox_generation=excluded.outbox_generation,
                  publish_claim_owner=CASE WHEN excluded.queue_published_at='' THEN '' ELSE schedules.publish_claim_owner END,
                  publish_claim_until=CASE WHEN excluded.queue_published_at='' THEN '' ELSE schedules.publish_claim_until END,
                  materialized_notification_id=CASE WHEN excluded.version<>schedules.version THEN '' ELSE schedules.materialized_notification_id END
                """,
                (
                    record.schedule_id, record.case_uid, record.kind, record.version,
                    record.reference_at, record.due_at, record.target_role, int(record.active),
                    record.updated_at, published, int(record.sms_eligible),
                    record.sms_ineligible_reason, generation, "", "", "",
                ),
            )
            db.execute("COMMIT")
        return replace(record, queue_published_at=published, outbox_generation=generation), changed

    def get_schedule(self, schedule_id: str) -> ScheduleRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
        return _schedule_from_mapping(dict(row)) if row else None

    def cancel_schedule(self, schedule_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE schedules SET active=0, updated_at=? WHERE schedule_id=?",
                (utc_iso(), schedule_id),
            )

    def consume_schedule_if_version(
        self, schedule_id: str, expected_version: str, expected_generation: int,
        notification_id: str, now: str,
    ) -> bool:
        with self._connect() as db:
            result = db.execute(
                "UPDATE schedules SET active=0,materialized_notification_id=?,updated_at=?,"
                "publish_claim_owner='',publish_claim_until='' WHERE schedule_id=? AND version=? "
                "AND outbox_generation=? AND active=1",
                (notification_id, now, schedule_id, expected_version, expected_generation),
            )
        return result.rowcount == 1

    def requeue_schedule_generation(
        self, schedule_id: str, expected_version: str, expected_generation: int, now: str,
    ) -> bool:
        with self._connect() as db:
            result = db.execute(
                "UPDATE schedules SET outbox_generation=outbox_generation+1,queue_published_at='',"
                "publish_claim_owner='',publish_claim_until='',updated_at=? WHERE schedule_id=? "
                "AND version=? AND outbox_generation=? AND active=1",
                (now, schedule_id, expected_version, expected_generation),
            )
        return result.rowcount == 1

    def list_unpublished(self, *, limit: int) -> list[tuple[str, dict[str, Any]]]:
        limit = max(1, min(int(limit), 1000))
        with self._connect() as db:
            notifications = db.execute(
                "SELECT * FROM notifications WHERE sms_state IN ('queued','retryable') "
                "AND sms_eligible=1 AND queue_published_at='' "
                "AND (publish_claim_until='' OR publish_claim_until<=?) "
                "ORDER BY created_at LIMIT ?", (utc_iso(), limit,)
            ).fetchall()
            remaining = max(0, limit - len(notifications))
            schedules = db.execute(
                "SELECT * FROM schedules WHERE active=1 AND sms_eligible=1 "
                "AND queue_published_at='' AND (publish_claim_until='' OR publish_claim_until<=?) "
                "ORDER BY due_at LIMIT ?", (utc_iso(), remaining,)
            ).fetchall() if remaining else []
        return [("notification", dict(row)) for row in notifications] + [
            ("schedule", dict(row)) for row in schedules
        ]

    @staticmethod
    def _generation_sql(entity_type: str) -> tuple[str, str]:
        if entity_type == "notification":
            return "notifications", "CAST(outbox_generation AS TEXT)=?"
        if entity_type == "schedule":
            return "schedules", "version || ':' || CAST(outbox_generation AS TEXT)=?"
        raise ValueError("unsupported outbox entity type")

    def claim_publication(
        self, entity_type: str, entity_id: str, expected_generation: str,
        owner: str, now: str, lease_seconds: int,
    ) -> bool:
        table, generation = self._generation_sql(entity_type)
        key = "notification_id" if entity_type == "notification" else "schedule_id"
        eligible = "sms_state IN ('queued','retryable') AND sms_eligible=1" if entity_type == "notification" else "active=1 AND sms_eligible=1"
        claim_until = utc_iso(parse_utc(now) + timedelta(seconds=lease_seconds))
        with self._connect() as db:
            result = db.execute(
                f"UPDATE {table} SET publish_claim_owner=?,publish_claim_until=? "
                f"WHERE {key}=? AND {generation} AND {eligible} AND queue_published_at='' "
                "AND (publish_claim_until='' OR publish_claim_until<=?)",
                (owner, claim_until, entity_id, expected_generation, now),
            )
        return result.rowcount == 1

    def mark_published(
        self, entity_type: str, entity_id: str, expected_generation: str,
        owner: str, published_at: str,
    ) -> bool:
        table, generation = self._generation_sql(entity_type)
        key = "notification_id" if entity_type == "notification" else "schedule_id"
        with self._connect() as db:
            result = db.execute(
                f"UPDATE {table} SET queue_published_at=?,publish_claim_owner='',publish_claim_until='' "
                f"WHERE {key}=? AND {generation} AND publish_claim_owner=? AND queue_published_at=''",
                (published_at, entity_id, expected_generation, owner),
            )
        return result.rowcount == 1

    def release_publication(
        self, entity_type: str, entity_id: str, expected_generation: str, owner: str,
    ) -> None:
        table, generation = self._generation_sql(entity_type)
        key = "notification_id" if entity_type == "notification" else "schedule_id"
        with self._connect() as db:
            db.execute(
                f"UPDATE {table} SET publish_claim_owner='',publish_claim_until='' "
                f"WHERE {key}=? AND {generation} AND publish_claim_owner=?",
                (entity_id, expected_generation, owner),
            )

    def claim_dispatch(
        self, notification_id: str, expected_generation: int,
        owner: str, now: str, lease_seconds: int,
    ) -> str:
        current = parse_utc(now)
        claim_until = utc_iso(current + timedelta(seconds=lease_seconds))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT sms_state,claim_until,retry_at,outbox_generation,sms_eligible,cancel_reason "
                "FROM notifications WHERE notification_id=?",
                (notification_id,),
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return "missing"
            state = str(row["sms_state"])
            if int(row["outbox_generation"] or 1) != int(expected_generation):
                db.execute("ROLLBACK")
                return "stale_generation"
            if not bool(row["sms_eligible"]) or row["cancel_reason"]:
                db.execute("ROLLBACK")
                return "cancelled" if row["cancel_reason"] else "disabled"
            if state in {"submitted", "delivered", "failed_permanent", "ambiguous", "cap_blocked", "disabled", "cancelled", "sending"}:
                db.execute("ROLLBACK")
                return state
            if state == "claimed" and row["claim_until"] and parse_utc(row["claim_until"]) > current:
                db.execute("ROLLBACK")
                return "busy"
            if state == "retryable" and row["retry_at"] and parse_utc(row["retry_at"]) > current:
                db.execute("ROLLBACK")
                return "not_due"
            db.execute(
                "UPDATE notifications SET sms_state='claimed',claim_owner=?,claim_until=?,updated_at=? "
                "WHERE notification_id=?",
                (owner, claim_until, now, notification_id),
            )
            db.execute("COMMIT")
            return "claimed"

    def reserve_attempt(
        self, notification_id: str, expected_generation: int,
        owner: str, day: str, limit: int,
    ) -> str:
        now = utc_iso()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT sms_state,claim_owner,outbox_generation,budget_day FROM notifications "
                "WHERE notification_id=?",
                (notification_id,),
            ).fetchone()
            if (
                not row or row["sms_state"] != "claimed" or row["claim_owner"] != owner
                or int(row["outbox_generation"] or 1) != int(expected_generation)
            ):
                db.execute("ROLLBACK")
                return "not_claimed"
            if row["budget_day"]:
                db.execute("COMMIT")
                return "reserved"
            budget = db.execute("SELECT attempts FROM sms_budgets WHERE utc_day=?", (day,)).fetchone()
            attempts = int(budget["attempts"] if budget else 0)
            if attempts >= limit:
                db.execute(
                    "UPDATE notifications SET sms_state='cap_blocked',error_code='daily_cap_reached',updated_at=? "
                    "WHERE notification_id=?", (now, notification_id),
                )
                db.execute(
                    "INSERT OR IGNORE INTO operational_alerts(alert_id,kind,created_at,detail_code) VALUES(?,?,?,?)",
                    (f"daily-cap-{day}", "daily_cap_reached", now, "sms_submission_blocked"),
                )
                db.execute("COMMIT")
                return "cap_blocked"
            db.execute(
                "INSERT INTO sms_budgets(utc_day,attempts) VALUES(?,1) "
                "ON CONFLICT(utc_day) DO UPDATE SET attempts=attempts+1", (day,)
            )
            db.execute(
                "UPDATE notifications SET attempt_count=attempt_count+1,budget_day=?,updated_at=? "
                "WHERE notification_id=?", (day, now, notification_id),
            )
            db.execute("COMMIT")
            return "reserved"

    def mark_sending(
        self, notification_id: str, expected_generation: int, owner: str,
        now: str, rollout_policy_version: str,
    ) -> bool:
        with self._connect() as db:
            result = db.execute(
                "UPDATE notifications SET sms_state='sending',updated_at=?,"
                "rollout_policy_version=? "
                "WHERE notification_id=? AND outbox_generation=? AND sms_state='claimed' "
                "AND claim_owner=? AND budget_day<>'' AND sms_eligible=1 AND cancel_reason=''",
                (
                    now, rollout_policy_version, notification_id,
                    expected_generation, owner,
                ),
            )
            return result.rowcount == 1

    def revalidate_dispatch(
        self, notification_id: str, expected_generation: int, owner: str, expected_state: str,
    ) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM notifications WHERE notification_id=? AND outbox_generation=? "
                "AND claim_owner=? AND sms_state=? AND sms_eligible=1 AND cancel_reason=''",
                (notification_id, expected_generation, owner, expected_state),
            ).fetchone()
        return bool(row)

    def cancel_sending_before_submit(
        self, notification_id: str, expected_generation: int, owner: str, now: str,
    ) -> bool:
        with self._connect() as db:
            result = db.execute(
                "UPDATE notifications SET sms_state='cancelled',cancel_reason=CASE WHEN cancel_reason='' "
                "THEN 'dispatch_invalidated' ELSE replace(cancel_reason,'too_late:','') END,"
                "cancelled_at=?,claim_owner='',claim_until='',updated_at=? WHERE notification_id=? "
                "AND outbox_generation=? AND sms_state='sending' AND claim_owner=?",
                (now, now, notification_id, expected_generation, owner),
            )
        return result.rowcount == 1

    def cancel_dispatch_for_policy(
        self, notification_id: str, expected_generation: int, owner: str,
        expected_state: str, reason: str, now: str,
    ) -> bool:
        """Atomically stop owned, unsent work rejected by current rollout policy."""
        if expected_state not in {"claimed", "sending"}:
            raise ValueError("policy cancellation requires claimed or sending state")
        if reason != "rollout_policy_revoked":
            raise ValueError("unsupported policy cancellation reason")
        with self._connect() as db:
            result = db.execute(
                "UPDATE notifications SET sms_state='cancelled',cancel_reason=?,"
                "cancelled_at=?,claim_owner='',claim_until='',updated_at=? "
                "WHERE notification_id=? AND outbox_generation=? AND sms_state=? "
                "AND claim_owner=? AND acs_message_id=''",
                (
                    reason, now, now, notification_id, expected_generation,
                    expected_state, owner,
                ),
            )
        return result.rowcount == 1

    def requeue_unclaimed_generation(
        self, notification_id: str, expected_generation: int, now: str,
    ) -> bool:
        with self._connect() as db:
            result = db.execute(
                "UPDATE notifications SET outbox_generation=outbox_generation+1,"
                "queue_published_at='',publish_claim_owner='',publish_claim_until='',updated_at=? "
                "WHERE notification_id=? AND outbox_generation=? AND sms_state='retryable'",
                (now, notification_id, expected_generation),
            )
        return result.rowcount == 1

    def defer_disabled_dispatch(
        self, notification_id: str, expected_generation: int, now: str,
    ) -> str:
        """Create a durable successor before a disabled worker settles work."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT sms_state,outbox_generation,sms_eligible,cancel_reason "
                "FROM notifications WHERE notification_id=?",
                (notification_id,),
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                return "missing"
            if int(row["outbox_generation"] or 1) != int(expected_generation):
                db.execute("ROLLBACK")
                return "stale_generation"
            state = str(row["sms_state"] or "")
            if state in TERMINAL_SMS_STATES:
                db.execute("ROLLBACK")
                return state
            if row["cancel_reason"]:
                db.execute("ROLLBACK")
                return "cancelled"
            if not bool(row["sms_eligible"]):
                db.execute("ROLLBACK")
                return "disabled"
            if state in {"claimed", "sending"}:
                db.execute("ROLLBACK")
                return "busy"
            if state not in {"queued", "retryable"}:
                db.execute("ROLLBACK")
                return "state_conflict"
            result = db.execute(
                "UPDATE notifications SET outbox_generation=outbox_generation+1,"
                "queue_published_at='',publish_claim_owner='',publish_claim_until='',"
                "claim_owner='',claim_until='',updated_at=? WHERE notification_id=? "
                "AND outbox_generation=? AND sms_state IN ('queued','retryable')",
                (now, notification_id, expected_generation),
            )
            if result.rowcount != 1:
                db.execute("ROLLBACK")
                return "state_conflict"
            db.execute("COMMIT")
            return "deferred"

    def mark_preflight_failure(self, notification_id: str, expected_generation: int, owner: str, *, error_code: str, now: str) -> None:
        with self._connect() as db:
            result = db.execute(
                "UPDATE notifications SET sms_state='failed_permanent',error_code=?,claim_owner='',"
                "claim_until='',updated_at=? WHERE notification_id=? AND outbox_generation=? "
                "AND sms_state='claimed' AND claim_owner=?",
                (error_code, now, notification_id, expected_generation, owner),
            )
            if result.rowcount != 1:
                raise RuntimeError("dispatch state changed before preflight failure could be recorded")

    def mark_submitted(self, notification_id: str, expected_generation: int, owner: str, *, message_id: str, masked: str, now: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "UPDATE notifications SET sms_state='submitted',acs_message_id=?,recipient_masked=?,"
                "claim_owner='',claim_until='',error_code='',updated_at=? "
                "WHERE notification_id=? AND outbox_generation=? AND sms_state='sending' AND claim_owner=?",
                (message_id, masked, now, notification_id, expected_generation, owner),
            )
            if result.rowcount != 1:
                db.execute("ROLLBACK")
                raise RuntimeError("dispatch state changed before ACS submission could be recorded")
            self._apply_pending_deliveries_sqlite(db, message_id)
            db.execute("COMMIT")

    def mark_retryable(self, notification_id: str, expected_generation: int, owner: str, *, error_code: str, retry_at: str, now: str) -> None:
        self._transition_from_sending(notification_id, expected_generation, owner, "retryable", error_code, now, retry_at)

    def mark_terminal_failure(self, notification_id: str, expected_generation: int, owner: str, *, state: str, error_code: str, now: str) -> None:
        if state not in {"failed_permanent", "ambiguous"}:
            raise ValueError("invalid terminal failure state")
        self._transition_from_sending(notification_id, expected_generation, owner, state, error_code, now, "")

    def _transition_from_sending(self, notification_id: str, expected_generation: int, owner: str, state: str, error: str, now: str, retry_at: str) -> None:
        with self._connect() as db:
            result = db.execute(
                "UPDATE notifications SET sms_state=?,error_code=?,retry_at=?,queue_published_at='',"
                "outbox_generation=CASE WHEN ?='retryable' THEN outbox_generation+1 ELSE outbox_generation END,"
                "publish_claim_owner='',publish_claim_until='',budget_day='',claim_owner='',claim_until='',updated_at=? "
                "WHERE notification_id=? AND outbox_generation=? AND sms_state='sending' AND claim_owner=?",
                (state, error, retry_at, state, now, notification_id, expected_generation, owner),
            )
            if result.rowcount != 1:
                raise RuntimeError("dispatch state changed before failure could be recorded")
            if state == "ambiguous":
                db.execute(
                    "INSERT OR IGNORE INTO operational_alerts(alert_id,kind,created_at,detail_code) VALUES(?,?,?,?)",
                    (f"ambiguous-{notification_id}", "ambiguous_sms_submission", now, error),
                )

    def recover_stale_sending(self, now: str) -> int:
        """Fail closed when a worker vanished around an ACS submission.

        Once a request enters ``sending`` the external outcome can no longer be
        proven after a worker crash. Retrying could send a duplicate, so expired
        leases are made explicitly ambiguous and surfaced to operations.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT notification_id FROM notifications "
                "WHERE sms_state='sending' AND claim_until<>'' AND claim_until<=?",
                (now,),
            ).fetchall()
            for row in rows:
                notification_id = str(row["notification_id"])
                db.execute(
                    "UPDATE notifications SET sms_state='ambiguous',error_code='worker_lost_during_submission',"
                    "claim_owner='',claim_until='',updated_at=? WHERE notification_id=? AND sms_state='sending'",
                    (now, notification_id),
                )
                db.execute(
                    "INSERT OR IGNORE INTO operational_alerts(alert_id,kind,created_at,detail_code) VALUES(?,?,?,?)",
                    (
                        f"ambiguous-{notification_id}",
                        "ambiguous_sms_submission",
                        now,
                        "worker_lost_during_submission",
                    ),
                )
            db.execute("COMMIT")
            return len(rows)

    def recover_expired_claims(self, now: str) -> int:
        """A claimed row proves ACS was not called; make a durable successor."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "UPDATE notifications SET sms_state='queued',outbox_generation=outbox_generation+1,"
                "queue_published_at='',publish_claim_owner='',publish_claim_until='',"
                "claim_owner='',claim_until='',updated_at=? WHERE sms_state='claimed' "
                "AND claim_until<>'' AND claim_until<=?",
                (now, now),
            )
            db.execute("COMMIT")
        return int(result.rowcount)

    @staticmethod
    def _apply_pending_deliveries_sqlite(db: sqlite3.Connection, message_id: str) -> int:
        current = db.execute(
            "SELECT notification_id,delivery_status,delivery_updated_at FROM notifications "
            "WHERE acs_message_id=?", (message_id,),
        ).fetchone()
        if not current:
            return 0
        applied = 0
        events = db.execute(
            "SELECT * FROM delivery_events WHERE message_id=? AND applied=0 "
            "ORDER BY observed_at,event_id", (message_id,),
        ).fetchall()
        current_status = str(current["delivery_status"] or "").lower()
        current_time = str(current["delivery_updated_at"] or "")
        for event in events:
            observed = str(event["observed_at"])
            status_value = str(event["status"])
            should_update = (
                (not current_time or parse_utc(current_time) <= parse_utc(observed))
                and not (current_status == "delivered" and status_value == "failed")
            )
            if should_update:
                new_state = "delivered" if status_value == "delivered" else "failed_permanent"
                db.execute(
                    "UPDATE notifications SET sms_state=?,delivery_status=?,delivery_updated_at=?,"
                    "error_code=?,updated_at=? WHERE notification_id=? AND sms_state IN "
                    "('submitted','delivered','failed_permanent')",
                    (
                        new_state, status_value, observed,
                        str(event["detail_code"]) if status_value == "failed" else "",
                        observed, str(current["notification_id"]),
                    ),
                )
                current_status, current_time = status_value, observed
            db.execute(
                "UPDATE delivery_events SET applied=1,applied_at=?,apply_error_code='' WHERE event_id=?",
                (utc_iso(), str(event["event_id"])),
            )
            applied += 1
        return applied

    def record_delivery(self, *, event_id: str, message_id: str, status: str, observed_at: str, detail_code: str) -> bool:
        status_value = status.lower()
        if status_value not in {"delivered", "failed"}:
            raise ValueError("unsupported ACS delivery status")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            created = True
            try:
                db.execute(
                    "INSERT INTO delivery_events(event_id,message_id,status,observed_at,detail_code,applied,applied_at,apply_error_code) VALUES(?,?,?,?,?,0,'','')",
                    (event_id, message_id, status_value, observed_at, detail_code),
                )
            except sqlite3.IntegrityError:
                created = False
            self._apply_pending_deliveries_sqlite(db, message_id)
            db.execute("COMMIT")
            return created

    def repair_delivery_state(self, *, limit: int = 500) -> dict[str, int]:
        limit = max(1, min(int(limit), 5000))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            message_ids = [
                str(row["message_id"]) for row in db.execute(
                    "SELECT DISTINCT message_id FROM delivery_events WHERE applied=0 LIMIT ?",
                    (limit,),
                ).fetchall()
            ]
            applied = sum(self._apply_pending_deliveries_sqlite(db, value) for value in message_ids)
            db.execute("COMMIT")
        return {"correlations_repaired": 0, "delivery_events_applied": applied}

    def record_worker_heartbeat(
        self, *, component: str, build_id: str, status: str, observed_at: str,
        sms_enabled: bool, sms_publish_enabled: bool,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO worker_heartbeats(component,build_id,status,observed_at,sms_enabled,sms_publish_enabled) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(component) DO UPDATE SET build_id=excluded.build_id,"
                "status=excluded.status,observed_at=excluded.observed_at,sms_enabled=excluded.sms_enabled,"
                "sms_publish_enabled=excluded.sms_publish_enabled",
                (component, build_id, status, observed_at, int(sms_enabled), int(sms_publish_enabled)),
            )

    def pre_enable_report(self, *, allowed_case_uids: set[str]) -> dict[str, Any]:
        wildcard = "*" in allowed_case_uids
        with self._connect() as db:
            rows = db.execute(
                "SELECT notification_id,case_uid,created_at,sms_state FROM notifications "
                "WHERE sms_eligible=1 AND sms_state IN ('queued','retryable','claimed','sending')"
            ).fetchall()
            schedules = db.execute(
                "SELECT schedule_id,case_uid,due_at FROM schedules WHERE active=1 AND sms_eligible=1"
            ).fetchall()
        non_canary = sum(
            1 for row in [*rows, *schedules]
            if not wildcard and str(row["case_uid"]) not in allowed_case_uids
        )
        times = [str(row["created_at"]) for row in rows] + [str(row["due_at"]) for row in schedules]
        total_work = len(rows) + len(schedules)
        return {
            "eligible_notifications": len(rows),
            "eligible_schedules": len(schedules),
            "non_canary_work": non_canary,
            "oldest_event_at": min(times) if times else "",
            "queue_empty": total_work == 0,
            "safe_to_enable": non_canary == 0 and total_work == 0,
        }

    def purge_expired(self, now: str, retention_days: int = 90) -> dict[str, int]:
        if not 1 <= int(retention_days) <= 365:
            raise ValueError("retention_days must be between 1 and 365")
        cutoff = utc_iso(parse_utc(now) - timedelta(days=int(retention_days)))
        cutoff_day = parse_utc(cutoff).strftime("%Y-%m-%d")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            n = db.execute("DELETE FROM notifications WHERE expires_at<=?", (now,)).rowcount
            d = db.execute("DELETE FROM delivery_events WHERE observed_at<=?", (cutoff,)).rowcount
            o = db.execute("DELETE FROM operational_alerts WHERE created_at<=?", (cutoff,)).rowcount
            s = db.execute("DELETE FROM schedules WHERE active=0 AND updated_at<=?", (cutoff,)).rowcount
            b = db.execute("DELETE FROM sms_budgets WHERE utc_day<=?", (cutoff_day,)).rowcount
            h = db.execute("DELETE FROM worker_heartbeats WHERE observed_at<=?", (cutoff,)).rowcount
            db.execute("COMMIT")
        return {
            "notifications": n,
            "delivery_events": d,
            "operational_alerts": o,
            "schedules": s,
            "sms_budgets": b,
            "worker_heartbeats": h,
        }

    def health(self) -> dict[str, Any]:
        try:
            with self._connect() as db:
                db.execute("SELECT 1").fetchone()
                counts = {
                    row["sms_state"]: int(row["count"])
                    for row in db.execute(
                        "SELECT sms_state,COUNT(*) AS count FROM notifications GROUP BY sms_state"
                    ).fetchall()
                }
                pending = db.execute(
                    "SELECT COUNT(*) AS count,MIN(created_at) AS oldest FROM notifications "
                    "WHERE sms_state IN ('queued','retryable','claimed','sending')"
                ).fetchone()
                heartbeat = db.execute(
                    "SELECT * FROM worker_heartbeats WHERE component='notification_worker'"
                ).fetchone()
                unapplied = db.execute(
                    "SELECT COUNT(*) AS count FROM delivery_events WHERE applied=0"
                ).fetchone()
                policy_row = db.execute(
                    "SELECT * FROM sms_rollout_policy WHERE policy_id='active'"
                ).fetchone()
            policy = (
                SmsRolloutPolicy.from_storage_dict(dict(policy_row))
                if policy_row else None
            )
            return {
                "available": True, "backend": "sqlite", "sms_states": counts,
                "pending_count": int(pending["count"] if pending else 0),
                "oldest_pending_at": str(pending["oldest"] or "") if pending else "",
                "unapplied_delivery_events": int(unapplied["count"] if unapplied else 0),
                "worker": dict(heartbeat) if heartbeat else None,
                "rollout_policy": {
                    "version": policy.version,
                    "publish_enabled": policy.sms_publish_enabled,
                    "daily_limit": policy.daily_limit,
                    "updated_at": policy.updated_at,
                } if policy else None,
            }
        except Exception as exc:
            return {"available": False, "backend": "sqlite", "error": exc.__class__.__name__}


class AzureTableNotificationRepository:
    """Azure Table implementation using create-if-absent and optimistic ETags."""

    def __init__(self, endpoint: str, table_name: str, client_id: str = ""):
        try:
            from azure.data.tables import TableClient
            from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise RuntimeError("Azure notification dependencies are not installed") from exc
        credential = (
            ManagedIdentityCredential(client_id=client_id)
            if client_id
            else DefaultAzureCredential(exclude_interactive_browser_credential=True)
        )
        self.client = TableClient(endpoint=endpoint, table_name=table_name, credential=credential)

    @staticmethod
    def _entity(partition: str, row: str, value: dict[str, Any]) -> dict[str, Any]:
        return {"PartitionKey": partition, "RowKey": row, **value}

    @staticmethod
    def _clean(entity: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in entity.items()
            if key not in {"PartitionKey", "RowKey", "etag", "odata.etag", "Timestamp"}
        }

    @staticmethod
    def _not_found(exc: Exception) -> bool:
        return getattr(exc, "status_code", None) == 404 or "notfound" in exc.__class__.__name__.lower()

    @staticmethod
    def _conflict(exc: Exception) -> bool:
        return getattr(exc, "status_code", None) in {409, 412} or any(
            token in exc.__class__.__name__.lower() for token in ("exists", "modified")
        )

    def _get(self, partition: str, row: str) -> dict[str, Any] | None:
        try:
            raw = self.client.get_entity(partition_key=partition, row_key=row)
            entity = dict(raw)
            metadata = getattr(raw, "metadata", None) or {}
            etag = metadata.get("etag") or entity.get("etag") or entity.get("odata.etag")
            if not etag:
                raise RuntimeError("Azure Table entity response did not contain an ETag")
            entity["etag"] = str(etag)
            return entity
        except Exception as exc:
            if self._not_found(exc):
                return None
            raise

    def _replace(self, entity: dict[str, Any], etag: str) -> None:
        from azure.core import MatchConditions
        from azure.data.tables import UpdateMode

        if not etag or etag == "*":
            raise RuntimeError("An explicit Azure Table ETag is required for replacement")
        clean = {
            key: value for key, value in entity.items()
            if key not in {"etag", "odata.etag", "Timestamp"}
        }
        self.client.update_entity(
            entity=clean,
            mode=UpdateMode.REPLACE,
            etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )

    def create_notification(self, record: NotificationRecord) -> tuple[NotificationRecord, bool]:
        entity = self._entity("notification", record.notification_id, record.as_dict())
        try:
            self.client.create_entity(entity)
            return record, True
        except Exception as exc:
            if not self._conflict(exc):
                raise
            existing = self.get_notification(record.notification_id)
            if existing is None:
                raise
            return existing, False

    def get_notification(self, notification_id: str) -> NotificationRecord | None:
        entity = self._get("notification", notification_id)
        return _notification_from_mapping(self._clean(entity)) if entity else None

    def get_rollout_policy(self) -> SmsRolloutPolicy | None:
        entity = self._get("rollout_policy", "active")
        return SmsRolloutPolicy.from_storage_dict(self._clean(entity)) if entity else None

    def upsert_rollout_policy(self, policy: SmsRolloutPolicy) -> SmsRolloutPolicy:
        candidate = replace(policy, updated_at=policy.updated_at or utc_iso())
        for _ in range(12):
            current_entity = self._get("rollout_policy", "active")
            if current_entity is None:
                try:
                    self.client.create_entity(
                        self._entity("rollout_policy", "active", candidate.as_storage_dict())
                    )
                    return candidate
                except Exception as exc:
                    if self._conflict(exc):
                        continue
                    raise
            current = SmsRolloutPolicy.from_storage_dict(self._clean(current_entity))
            if candidate.version < current.version:
                return current
            if candidate.version == current.version:
                if not candidate.same_definition(current):
                    raise ValueError(
                        "SMS rollout policy version was reused with different settings"
                    )
                return current
            replacement = self._entity(
                "rollout_policy", "active", candidate.as_storage_dict()
            )
            try:
                self._replace(replacement, str(current_entity.get("etag") or ""))
                return candidate
            except Exception as exc:
                if self._conflict(exc):
                    continue
                raise
        raise RuntimeError("SMS rollout policy contention exceeded retry limit")

    def list_notifications(
        self, *, roles: Iterable[str], user_id: str, limit: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        roles_set = {str(role) for role in roles if role}
        if not roles_set:
            return []
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        rows: list[dict[str, Any]] = []
        query = self.client.query_entities(
            query_filter="PartitionKey eq 'notification' and active eq true"
        )
        for raw in query:
            value = self._clean(dict(raw))
            if value.get("target_role") not in roles_set:
                continue
            if value.get("target_user_id") and value.get("target_user_id") != user_id:
                continue
            if parse_utc(value["expires_at"]) <= utc_now():
                continue
            read = self._get("notification_read", f"{value['notification_id']}:{user_id}")
            value["is_read"] = bool(read)
            value["read_at"] = str((read or {}).get("read_at") or "")
            rows.append(value)
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows[offset:offset + limit]

    def count_notifications(self, *, roles: Iterable[str], user_id: str) -> int:
        roles_set = {str(role) for role in roles if role}
        if not roles_set:
            return 0
        count = 0
        for raw in self.client.query_entities(
            query_filter="PartitionKey eq 'notification' and active eq true"
        ):
            if raw.get("target_role") not in roles_set:
                continue
            if raw.get("target_user_id") and raw.get("target_user_id") != user_id:
                continue
            if parse_utc(str(raw.get("expires_at") or "")) <= utc_now():
                continue
            count += 1
        return count

    def mark_read(self, notification_id: str, user_id: str, read_at: str) -> bool:
        if self.get_notification(notification_id) is None:
            return False
        entity = self._entity(
            "notification_read",
            f"{notification_id}:{user_id}",
            {"notification_id": notification_id, "user_id": user_id, "read_at": read_at},
        )
        self.client.upsert_entity(entity)
        return True

    def acknowledge_notification(
        self, notification_id: str, user_id: str, acknowledged_at: str
    ) -> bool:
        for _ in range(8):
            current = self._get("notification", notification_id)
            if current is None:
                return False
            self.client.upsert_entity(self._entity(
                "notification_read",
                f"{notification_id}:{user_id}",
                {
                    "notification_id": notification_id,
                    "user_id": user_id,
                    "read_at": acknowledged_at,
                },
            ))
            # Workflow acknowledgement is intentionally written first and its
            # reconciliation can already have hidden this row.  Audit fields
            # still belong on the durable notification.  Preserve the first
            # acknowledgement on retries or competing role-level readers.
            if str(current.get("acknowledged_at") or ""):
                return True
            current["active"] = False
            current["acknowledged_at"] = acknowledged_at
            current["acknowledged_by"] = user_id
            current["updated_at"] = acknowledged_at
            try:
                self._replace(current, str(current.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        raise RuntimeError("notification acknowledgement contention exceeded retry limit")

    @staticmethod
    def _cancel_entity(entity: dict[str, Any], *, reason: str, now: str) -> None:
        state = str(entity.get("sms_state") or "")
        if state in {"queued", "retryable", "claimed"}:
            entity.update({
                "sms_state": "cancelled", "cancel_reason": reason,
                "cancelled_at": now, "claim_owner": "", "claim_until": "",
            })
        elif state in {"sending", "submitted"}:
            entity.update({"cancel_reason": f"too_late:{reason}", "cancelled_at": now})

    def deactivate_notifications(
        self, case_uid: str, kind: str | None, updated_at: str, *,
        cancel_sms: bool = True, cancel_reason: str = "event_obsolete",
    ) -> int:
        count = 0
        for raw in self.client.query_entities(
            query_filter="PartitionKey eq 'notification' and active eq true"
        ):
            entity = dict(raw)
            if entity.get("case_uid") != case_uid or (kind and entity.get("kind") != kind):
                continue
            for _ in range(8):
                current = self._get("notification", str(entity["RowKey"]))
                if current is None or not bool(current.get("active")):
                    break
                current["active"] = False
                if cancel_sms:
                    self._cancel_entity(current, reason=cancel_reason, now=updated_at)
                current["updated_at"] = updated_at
                try:
                    self._replace(current, str(current.get("etag") or current.get("odata.etag") or "*"))
                    count += 1
                    break
                except Exception as exc:
                    if not self._conflict(exc):
                        raise
        return count

    def deactivate_notifications_except_event(
        self, case_uid: str, kind: str, event_key: str, updated_at: str, *,
        cancel_reason: str = "event_replaced",
    ) -> int:
        count = 0
        for raw in self.client.query_entities(
            query_filter="PartitionKey eq 'notification' and active eq true"
        ):
            if (
                raw.get("case_uid") != case_uid
                or raw.get("kind") != kind
                or raw.get("event_key") == event_key
            ):
                continue
            notification_id = str(raw.get("RowKey") or "")
            for _ in range(8):
                current = self._get("notification", notification_id)
                if current is None or not bool(current.get("active")):
                    break
                if str(current.get("event_key") or "") == event_key:
                    break
                current["active"] = False
                self._cancel_entity(current, reason=cancel_reason, now=updated_at)
                current["updated_at"] = updated_at
                try:
                    self._replace(current, str(current.get("etag") or ""))
                    count += 1
                    break
                except Exception as exc:
                    if not self._conflict(exc):
                        raise
        return count

    def cancel_notification(self, notification_id: str, *, reason: str, now: str) -> bool:
        for _ in range(8):
            current = self._get("notification", notification_id)
            if current is None:
                return False
            current["active"] = False
            self._cancel_entity(current, reason=reason, now=now)
            current["updated_at"] = now
            try:
                self._replace(current, str(current.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        raise RuntimeError("notification cancellation contention exceeded retry limit")

    def activate_notification(self, notification_id: str, *, now: str) -> NotificationRecord | None:
        for _ in range(8):
            current = self._get("notification", notification_id)
            if current is None:
                return None
            if bool(current.get("active")):
                return _notification_from_mapping(self._clean(current))
            if current.get("cancel_reason") or current.get("sms_state") != "disabled":
                return _notification_from_mapping(self._clean(current))
            current.update({
                "active": True,
                "sms_state": "queued" if bool(current.get("sms_eligible")) else "disabled",
                "updated_at": now,
            })
            try:
                self._replace(current, str(current.get("etag") or ""))
                return self.get_notification(notification_id)
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        raise RuntimeError("notification activation contention exceeded retry limit")

    def upsert_schedule(self, record: ScheduleRecord) -> tuple[ScheduleRecord, bool]:
        for _ in range(8):
            previous = self._get("schedule", record.schedule_id)
            changed = previous is None or previous.get("version") != record.version or not bool(previous.get("active"))
            value = record.as_dict()
            value["queue_published_at"] = "" if changed else str((previous or {}).get("queue_published_at") or "")
            value["outbox_generation"] = (
                int((previous or {}).get("outbox_generation") or 1) + 1
                if changed and previous is not None
                else int((previous or {}).get("outbox_generation") or record.outbox_generation)
            )
            if changed:
                value.update({
                    "publish_claim_owner": "", "publish_claim_until": "",
                    "materialized_notification_id": "",
                })
            entity = self._entity("schedule", record.schedule_id, value)
            try:
                if previous is None:
                    self.client.create_entity(entity)
                else:
                    self._replace(entity, str(previous.get("etag") or previous.get("odata.etag") or "*"))
                return _schedule_from_mapping(value), changed
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        raise RuntimeError("schedule update contention exceeded retry limit")

    def get_schedule(self, schedule_id: str) -> ScheduleRecord | None:
        entity = self._get("schedule", schedule_id)
        return _schedule_from_mapping(self._clean(entity)) if entity else None

    def cancel_schedule(self, schedule_id: str) -> None:
        for _ in range(8):
            current = self._get("schedule", schedule_id)
            if current is None or not bool(current.get("active")):
                return
            current["active"] = False
            current["updated_at"] = utc_iso()
            try:
                self._replace(current, str(current.get("etag") or current.get("odata.etag") or "*"))
                return
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        raise RuntimeError("schedule cancellation contention exceeded retry limit")

    def consume_schedule_if_version(
        self, schedule_id: str, expected_version: str, expected_generation: int,
        notification_id: str, now: str,
    ) -> bool:
        for _ in range(8):
            current = self._get("schedule", schedule_id)
            if (
                current is None or not bool(current.get("active"))
                or str(current.get("version") or "") != expected_version
                or int(current.get("outbox_generation") or 1) != int(expected_generation)
            ):
                return False
            current.update({
                "active": False, "materialized_notification_id": notification_id,
                "publish_claim_owner": "", "publish_claim_until": "", "updated_at": now,
            })
            try:
                self._replace(current, str(current.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return False

    def defer_disabled_dispatch(
        self, notification_id: str, expected_generation: int, now: str,
    ) -> str:
        """Create a durable successor before a disabled worker settles work."""
        for _ in range(8):
            current = self._get("notification", notification_id)
            if current is None:
                return "missing"
            if int(current.get("outbox_generation") or 1) != int(expected_generation):
                return "stale_generation"
            state = str(current.get("sms_state") or "")
            if state in TERMINAL_SMS_STATES:
                return state
            if current.get("cancel_reason"):
                return "cancelled"
            if not bool(current.get("sms_eligible")):
                return "disabled"
            if state in {"claimed", "sending"}:
                return "busy"
            if state not in {"queued", "retryable"}:
                return "state_conflict"
            current.update({
                "outbox_generation": expected_generation + 1,
                "queue_published_at": "",
                "publish_claim_owner": "",
                "publish_claim_until": "",
                "claim_owner": "",
                "claim_until": "",
                "updated_at": now,
            })
            try:
                self._replace(current, str(current.get("etag") or ""))
                return "deferred"
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return "state_conflict"

    def requeue_schedule_generation(
        self, schedule_id: str, expected_version: str, expected_generation: int, now: str,
    ) -> bool:
        for _ in range(8):
            current = self._get("schedule", schedule_id)
            if (
                current is None or not bool(current.get("active"))
                or str(current.get("version") or "") != expected_version
                or int(current.get("outbox_generation") or 1) != int(expected_generation)
            ):
                return False
            current.update({
                "outbox_generation": expected_generation + 1,
                "queue_published_at": "", "publish_claim_owner": "",
                "publish_claim_until": "", "updated_at": now,
            })
            try:
                self._replace(current, str(current.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return False

    def list_unpublished(self, *, limit: int) -> list[tuple[str, dict[str, Any]]]:
        limit = max(1, min(int(limit), 1000))
        result: list[tuple[str, dict[str, Any]]] = []
        for partition, entity_type in (("notification", "notification"), ("schedule", "schedule")):
            for raw in self.client.query_entities(query_filter=f"PartitionKey eq '{partition}'"):
                value = self._clean(dict(raw))
                eligible = (
                    value.get("sms_state") in {"queued", "retryable"} and bool(value.get("sms_eligible"))
                    if entity_type == "notification"
                    else bool(value.get("active")) and bool(value.get("sms_eligible"))
                )
                claim_until = str(value.get("publish_claim_until") or "")
                if eligible and not value.get("queue_published_at") and (
                    not claim_until or parse_utc(claim_until) <= utc_now()
                ):
                    result.append((entity_type, value))
                if len(result) >= limit:
                    return result
        return result

    @staticmethod
    def _entity_generation(entity_type: str, entity: dict[str, Any]) -> str:
        if entity_type == "notification":
            return str(int(entity.get("outbox_generation") or 1))
        if entity_type == "schedule":
            return f"{entity.get('version')}:{int(entity.get('outbox_generation') or 1)}"
        raise ValueError("unsupported outbox entity type")

    def claim_publication(
        self, entity_type: str, entity_id: str, expected_generation: str,
        owner: str, now: str, lease_seconds: int,
    ) -> bool:
        partition = "notification" if entity_type == "notification" else "schedule"
        for _ in range(8):
            current = self._get(partition, entity_id)
            if current is None or current.get("queue_published_at"):
                return False
            if self._entity_generation(entity_type, current) != expected_generation:
                return False
            eligible = (
                current.get("sms_state") in {"queued", "retryable"} and bool(current.get("sms_eligible"))
                if entity_type == "notification"
                else bool(current.get("active")) and bool(current.get("sms_eligible"))
            )
            claim_until = str(current.get("publish_claim_until") or "")
            if not eligible or (claim_until and parse_utc(claim_until) > parse_utc(now)):
                return False
            current.update({
                "publish_claim_owner": owner,
                "publish_claim_until": utc_iso(parse_utc(now) + timedelta(seconds=lease_seconds)),
            })
            try:
                self._replace(current, str(current.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return False

    def mark_published(
        self, entity_type: str, entity_id: str, expected_generation: str,
        owner: str, published_at: str,
    ) -> bool:
        partition = "notification" if entity_type == "notification" else "schedule"
        for _ in range(8):
            current = self._get(partition, entity_id)
            if (
                current is None or current.get("queue_published_at")
                or self._entity_generation(entity_type, current) != expected_generation
                or current.get("publish_claim_owner") != owner
            ):
                return False
            current.update({
                "queue_published_at": published_at,
                "publish_claim_owner": "", "publish_claim_until": "",
            })
            try:
                self._replace(current, str(current.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return False

    def release_publication(
        self, entity_type: str, entity_id: str, expected_generation: str, owner: str,
    ) -> None:
        partition = "notification" if entity_type == "notification" else "schedule"
        for _ in range(8):
            current = self._get(partition, entity_id)
            if (
                current is None or self._entity_generation(entity_type, current) != expected_generation
                or current.get("publish_claim_owner") != owner
            ):
                return
            current.update({"publish_claim_owner": "", "publish_claim_until": ""})
            try:
                self._replace(current, str(current.get("etag") or ""))
                return
            except Exception as exc:
                if not self._conflict(exc):
                    raise

    def claim_dispatch(
        self, notification_id: str, expected_generation: int,
        owner: str, now: str, lease_seconds: int,
    ) -> str:
        current_time = parse_utc(now)
        for _ in range(12):
            entity = self._get("notification", notification_id)
            if entity is None:
                return "missing"
            state = str(entity.get("sms_state") or "")
            if int(entity.get("outbox_generation") or 1) != int(expected_generation):
                return "stale_generation"
            if not bool(entity.get("sms_eligible")) or entity.get("cancel_reason"):
                return "cancelled" if entity.get("cancel_reason") else "disabled"
            if state in {"submitted", "delivered", "failed_permanent", "ambiguous", "cap_blocked", "disabled", "cancelled", "sending"}:
                return state
            if state == "claimed" and entity.get("claim_until") and parse_utc(entity["claim_until"]) > current_time:
                return "busy"
            if state == "retryable" and entity.get("retry_at") and parse_utc(entity["retry_at"]) > current_time:
                return "not_due"
            entity.update({
                "sms_state": "claimed", "claim_owner": owner,
                "claim_until": utc_iso(current_time + timedelta(seconds=lease_seconds)),
                "updated_at": now,
            })
            try:
                self._replace(entity, str(entity.get("etag") or entity.get("odata.etag") or "*"))
                return "claimed"
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return "busy"

    def reserve_attempt(
        self, notification_id: str, expected_generation: int,
        owner: str, day: str, limit: int,
    ) -> str:
        # Claiming happens first, so only one worker can reserve for this alert.
        for _ in range(12):
            notification = self._get("notification", notification_id)
            if (
                not notification or notification.get("sms_state") != "claimed"
                or notification.get("claim_owner") != owner
                or int(notification.get("outbox_generation") or 1) != int(expected_generation)
            ):
                return "not_claimed"
            if notification.get("budget_day"):
                return "reserved"
            budget = self._get("sms_budget", day)
            attempts = int((budget or {}).get("attempts") or 0)
            if attempts >= limit:
                notification.update({"sms_state": "cap_blocked", "error_code": "daily_cap_reached", "updated_at": utc_iso()})
                try:
                    self._replace(notification, str(notification.get("etag") or notification.get("odata.etag") or "*"))
                    self.client.upsert_entity(self._entity("operational_alert", f"daily-cap-{day}", {
                        "kind": "daily_cap_reached", "created_at": utc_iso(), "detail_code": "sms_submission_blocked"
                    }))
                    return "cap_blocked"
                except Exception as exc:
                    if self._conflict(exc):
                        continue
                    raise
            new_budget = self._entity("sms_budget", day, {"attempts": attempts + 1, "updated_at": utc_iso()})
            try:
                if budget is None:
                    self.client.create_entity(new_budget)
                else:
                    self._replace(new_budget, str(budget.get("etag") or budget.get("odata.etag") or "*"))
            except Exception as exc:
                if self._conflict(exc):
                    continue
                raise
            # A crash here can conservatively consume one cap unit without a send;
            # it cannot exceed the cap or duplicate a submitted message.
            for _inner in range(8):
                notification = self._get("notification", notification_id)
                if not notification or notification.get("claim_owner") != owner:
                    return "not_claimed"
                notification["attempt_count"] = int(notification.get("attempt_count") or 0) + 1
                notification["budget_day"] = day
                notification["updated_at"] = utc_iso()
                try:
                    self._replace(notification, str(notification.get("etag") or notification.get("odata.etag") or "*"))
                    return "reserved"
                except Exception as exc:
                    if not self._conflict(exc):
                        raise
            return "not_claimed"
        return "cap_blocked"

    def _transition(
        self, notification_id: str, expected_generation: int,
        owner: str, expected: str, updates: dict[str, Any],
    ) -> bool:
        for _ in range(10):
            entity = self._get("notification", notification_id)
            if (
                not entity or entity.get("sms_state") != expected
                or entity.get("claim_owner") != owner
                or int(entity.get("outbox_generation") or 1) != int(expected_generation)
            ):
                return False
            entity.update(updates)
            try:
                self._replace(entity, str(entity.get("etag") or entity.get("odata.etag") or "*"))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return False

    def mark_sending(
        self, notification_id: str, expected_generation: int, owner: str,
        now: str, rollout_policy_version: str,
    ) -> bool:
        current = self._get("notification", notification_id)
        if (
            not current or not current.get("budget_day")
            or int(current.get("outbox_generation") or 1) != int(expected_generation)
            or not bool(current.get("sms_eligible")) or current.get("cancel_reason")
        ):
            return False
        return self._transition(
            notification_id, expected_generation, owner, "claimed",
            {
                "sms_state": "sending", "updated_at": now,
                "rollout_policy_version": rollout_policy_version,
            },
        )

    def revalidate_dispatch(
        self, notification_id: str, expected_generation: int, owner: str, expected_state: str,
    ) -> bool:
        current = self._get("notification", notification_id)
        return bool(
            current
            and int(current.get("outbox_generation") or 1) == int(expected_generation)
            and current.get("claim_owner") == owner
            and current.get("sms_state") == expected_state
            and bool(current.get("sms_eligible"))
            and not current.get("cancel_reason")
        )

    def cancel_sending_before_submit(
        self, notification_id: str, expected_generation: int, owner: str, now: str,
    ) -> bool:
        for _ in range(8):
            current = self._get("notification", notification_id)
            if (
                not current or current.get("sms_state") != "sending"
                or current.get("claim_owner") != owner
                or int(current.get("outbox_generation") or 1) != int(expected_generation)
            ):
                return False
            reason = str(current.get("cancel_reason") or "dispatch_invalidated")
            current.update({
                "sms_state": "cancelled", "cancel_reason": reason.removeprefix("too_late:"),
                "cancelled_at": now, "claim_owner": "", "claim_until": "", "updated_at": now,
            })
            try:
                self._replace(current, str(current.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return False

    def cancel_dispatch_for_policy(
        self, notification_id: str, expected_generation: int, owner: str,
        expected_state: str, reason: str, now: str,
    ) -> bool:
        """ETag-safe equivalent of the SQLite rollout-policy cancellation."""
        if expected_state not in {"claimed", "sending"}:
            raise ValueError("policy cancellation requires claimed or sending state")
        if reason != "rollout_policy_revoked":
            raise ValueError("unsupported policy cancellation reason")
        for _ in range(10):
            current = self._get("notification", notification_id)
            if (
                not current
                or current.get("sms_state") != expected_state
                or current.get("claim_owner") != owner
                or int(current.get("outbox_generation") or 1)
                != int(expected_generation)
                or current.get("acs_message_id")
            ):
                return False
            current.update({
                "sms_state": "cancelled",
                "cancel_reason": reason,
                "cancelled_at": now,
                "claim_owner": "",
                "claim_until": "",
                "updated_at": now,
            })
            try:
                self._replace(current, str(current.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return False

    def requeue_unclaimed_generation(
        self, notification_id: str, expected_generation: int, now: str,
    ) -> bool:
        for _ in range(8):
            current = self._get("notification", notification_id)
            if (
                not current or current.get("sms_state") != "retryable"
                or int(current.get("outbox_generation") or 1) != int(expected_generation)
            ):
                return False
            current.update({
                "outbox_generation": expected_generation + 1, "queue_published_at": "",
                "publish_claim_owner": "", "publish_claim_until": "", "updated_at": now,
            })
            try:
                self._replace(current, str(current.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        return False

    def mark_preflight_failure(self, notification_id: str, expected_generation: int, owner: str, *, error_code: str, now: str) -> None:
        if not self._transition(notification_id, expected_generation, owner, "claimed", {
            "sms_state": "failed_permanent", "error_code": error_code,
            "claim_owner": "", "claim_until": "", "updated_at": now,
        }):
            raise RuntimeError("dispatch state changed before preflight failure could be recorded")

    def mark_submitted(self, notification_id: str, expected_generation: int, owner: str, *, message_id: str, masked: str, now: str) -> None:
        if not self._transition(notification_id, expected_generation, owner, "sending", {
            "sms_state": "submitted", "acs_message_id": message_id,
            "recipient_masked": masked, "claim_owner": "", "claim_until": "",
            "error_code": "", "updated_at": now,
        }):
            raise RuntimeError("dispatch state changed before ACS submission could be recorded")
        try:
            self.client.create_entity(self._entity("acs_correlation", message_id, {
                "notification_id": notification_id, "created_at": now
            }))
        except Exception as exc:
            if not self._conflict(exc):
                raise
        self._apply_pending_delivery_events(message_id, limit=500)

    def mark_retryable(self, notification_id: str, expected_generation: int, owner: str, *, error_code: str, retry_at: str, now: str) -> None:
        if not self._transition(notification_id, expected_generation, owner, "sending", {
            "sms_state": "retryable", "error_code": error_code, "retry_at": retry_at,
            "queue_published_at": "", "outbox_generation": int(
                (self._get("notification", notification_id) or {}).get("outbox_generation") or 1
            ) + 1,
            "publish_claim_owner": "", "publish_claim_until": "", "budget_day": "",
            "claim_owner": "", "claim_until": "", "updated_at": now,
        }):
            raise RuntimeError("dispatch state changed before retry state could be recorded")

    def mark_terminal_failure(self, notification_id: str, expected_generation: int, owner: str, *, state: str, error_code: str, now: str) -> None:
        if state not in {"failed_permanent", "ambiguous"}:
            raise ValueError("invalid terminal failure state")
        if not self._transition(notification_id, expected_generation, owner, "sending", {
            "sms_state": state, "error_code": error_code, "claim_owner": "",
            "claim_until": "", "updated_at": now,
        }):
            raise RuntimeError("dispatch state changed before failure could be recorded")
        if state == "ambiguous":
            self.client.upsert_entity(self._entity("operational_alert", f"ambiguous-{notification_id}", {
                "kind": "ambiguous_sms_submission", "created_at": now, "detail_code": error_code
            }))

    def recover_stale_sending(self, now: str) -> int:
        recovered = 0
        current_time = parse_utc(now)
        for raw in self.client.query_entities(
            query_filter="PartitionKey eq 'notification' and sms_state eq 'sending'"
        ):
            notification_id = str(raw.get("RowKey") or "")
            if not notification_id:
                continue
            for _ in range(8):
                entity = self._get("notification", notification_id)
                if not entity or entity.get("sms_state") != "sending":
                    break
                claim_until = str(entity.get("claim_until") or "")
                if not claim_until or parse_utc(claim_until) > current_time:
                    break
                entity.update({
                    "sms_state": "ambiguous",
                    "error_code": "worker_lost_during_submission",
                    "claim_owner": "",
                    "claim_until": "",
                    "updated_at": now,
                })
                try:
                    self._replace(entity, str(entity.get("etag") or ""))
                    self.client.upsert_entity(self._entity(
                        "operational_alert",
                        f"ambiguous-{notification_id}",
                        {
                            "kind": "ambiguous_sms_submission",
                            "created_at": now,
                            "detail_code": "worker_lost_during_submission",
                        },
                    ))
                    recovered += 1
                    break
                except Exception as exc:
                    if not self._conflict(exc):
                        raise
        return recovered

    def recover_expired_claims(self, now: str) -> int:
        recovered = 0
        current_time = parse_utc(now)
        for raw in self.client.query_entities(
            query_filter="PartitionKey eq 'notification' and sms_state eq 'claimed'"
        ):
            notification_id = str(raw.get("RowKey") or "")
            for _ in range(8):
                entity = self._get("notification", notification_id)
                if not entity or entity.get("sms_state") != "claimed":
                    break
                claim_until = str(entity.get("claim_until") or "")
                if not claim_until or parse_utc(claim_until) > current_time:
                    break
                entity.update({
                    "sms_state": "queued",
                    "outbox_generation": int(entity.get("outbox_generation") or 1) + 1,
                    "queue_published_at": "", "publish_claim_owner": "",
                    "publish_claim_until": "", "claim_owner": "", "claim_until": "",
                    "updated_at": now,
                })
                try:
                    self._replace(entity, str(entity.get("etag") or ""))
                    recovered += 1
                    break
                except Exception as exc:
                    if not self._conflict(exc):
                        raise
        return recovered

    def _ensure_correlation(self, message_id: str, notification_id: str, now: str) -> bool:
        existing = self._get("acs_correlation", message_id)
        if existing:
            return False
        try:
            self.client.create_entity(self._entity("acs_correlation", message_id, {
                "notification_id": notification_id, "created_at": now,
            }))
            return True
        except Exception as exc:
            if self._conflict(exc):
                return False
            raise

    def _apply_delivery_event(self, event_id: str) -> bool:
        for _ in range(8):
            delivery = self._get("delivery_event", event_id)
            if not delivery or bool(delivery.get("applied")):
                return False
            message_id = str(delivery.get("message_id") or "")
            correlation = self._get("acs_correlation", message_id)
            if not correlation:
                return False
            notification_id = str(correlation.get("notification_id") or "")
            notification = self._get("notification", notification_id)
            if not notification:
                return False
            status_value = str(delivery.get("status") or "").lower()
            observed_at = str(delivery.get("observed_at") or "")
            current_status = str(notification.get("delivery_status") or "").lower()
            current_time = str(notification.get("delivery_updated_at") or "")
            should_update = (
                (not current_time or parse_utc(current_time) <= parse_utc(observed_at))
                and not (current_status == "delivered" and status_value == "failed")
            )
            if should_update:
                notification.update({
                    "sms_state": "delivered" if status_value == "delivered" else "failed_permanent",
                    "delivery_status": status_value, "delivery_updated_at": observed_at,
                    "error_code": str(delivery.get("detail_code") or "") if status_value == "failed" else "",
                    "updated_at": observed_at,
                })
                try:
                    self._replace(notification, str(notification.get("etag") or ""))
                except Exception as exc:
                    if self._conflict(exc):
                        continue
                    raise
            delivery["applied"] = True
            delivery["applied_at"] = utc_iso()
            delivery["apply_error_code"] = ""
            try:
                self._replace(delivery, str(delivery.get("etag") or ""))
                return True
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        raise RuntimeError("delivery application contention exceeded retry limit")

    def _apply_pending_delivery_events(self, message_id: str, *, limit: int) -> int:
        applied = 0
        for raw in self.client.query_entities(
            query_filter="PartitionKey eq 'delivery_event' and applied eq false"
        ):
            if str(raw.get("message_id") or "") != message_id:
                continue
            applied += int(self._apply_delivery_event(str(raw.get("RowKey") or "")))
            if applied >= limit:
                break
        return applied

    def record_delivery(self, *, event_id: str, message_id: str, status: str, observed_at: str, detail_code: str) -> bool:
        status_value = status.lower()
        if status_value not in {"delivered", "failed"}:
            raise ValueError("unsupported ACS delivery status")
        created = True
        try:
            self.client.create_entity(self._entity("delivery_event", event_id, {
                "message_id": message_id, "status": status_value,
                "observed_at": observed_at, "detail_code": detail_code,
                "applied": False, "applied_at": "", "apply_error_code": "",
            }))
        except Exception as exc:
            if self._conflict(exc):
                created = False
            else:
                raise
        self._apply_delivery_event(event_id)
        return created

    def repair_delivery_state(self, *, limit: int = 500) -> dict[str, int]:
        limit = max(1, min(int(limit), 5000))
        correlations = applied = 0
        for raw in self.client.query_entities(query_filter="PartitionKey eq 'notification'"):
            message_id = str(raw.get("acs_message_id") or "")
            notification_id = str(raw.get("RowKey") or "")
            if not message_id or not notification_id:
                continue
            correlations += int(self._ensure_correlation(message_id, notification_id, utc_iso()))
            applied += self._apply_pending_delivery_events(message_id, limit=max(1, limit - applied))
            if applied >= limit:
                break
        if applied < limit:
            for raw in self.client.query_entities(
                query_filter="PartitionKey eq 'delivery_event' and applied eq false"
            ):
                applied += int(self._apply_delivery_event(str(raw.get("RowKey") or "")))
                if applied >= limit:
                    break
        return {"correlations_repaired": correlations, "delivery_events_applied": applied}

    def record_worker_heartbeat(
        self, *, component: str, build_id: str, status: str, observed_at: str,
        sms_enabled: bool, sms_publish_enabled: bool,
    ) -> None:
        for _ in range(8):
            current = self._get("worker_heartbeat", component)
            value = self._entity("worker_heartbeat", component, {
                "component": component, "build_id": build_id, "status": status,
                "observed_at": observed_at, "sms_enabled": sms_enabled,
                "sms_publish_enabled": sms_publish_enabled,
            })
            try:
                if current is None:
                    self.client.create_entity(value)
                else:
                    self._replace(value, str(current.get("etag") or ""))
                return
            except Exception as exc:
                if not self._conflict(exc):
                    raise
        raise RuntimeError("worker heartbeat contention exceeded retry limit")

    def pre_enable_report(self, *, allowed_case_uids: set[str]) -> dict[str, Any]:
        wildcard = "*" in allowed_case_uids
        notifications: list[dict[str, Any]] = []
        schedules: list[dict[str, Any]] = []
        for raw in self.client.query_entities(query_filter="PartitionKey eq 'notification'"):
            if bool(raw.get("sms_eligible")) and raw.get("sms_state") in {
                "queued", "retryable", "claimed", "sending",
            }:
                notifications.append(dict(raw))
        for raw in self.client.query_entities(query_filter="PartitionKey eq 'schedule'"):
            if bool(raw.get("active")) and bool(raw.get("sms_eligible")):
                schedules.append(dict(raw))
        non_canary = sum(
            1 for row in [*notifications, *schedules]
            if not wildcard and str(row.get("case_uid") or "") not in allowed_case_uids
        )
        times = [str(row.get("created_at") or "") for row in notifications]
        times += [str(row.get("due_at") or "") for row in schedules]
        times = [value for value in times if value]
        total_work = len(notifications) + len(schedules)
        return {
            "eligible_notifications": len(notifications),
            "eligible_schedules": len(schedules), "non_canary_work": non_canary,
            "oldest_event_at": min(times) if times else "",
            "queue_empty": total_work == 0,
            "safe_to_enable": non_canary == 0 and total_work == 0,
        }

    def purge_expired(self, now: str, retention_days: int = 90) -> dict[str, int]:
        if not 1 <= int(retention_days) <= 365:
            raise ValueError("retention_days must be between 1 and 365")
        cutoff = utc_iso(parse_utc(now) - timedelta(days=int(retention_days)))
        cutoff_day = parse_utc(cutoff).strftime("%Y-%m-%d")
        deleted: dict[str, int] = {}
        for partition, field, threshold in (
            ("notification", "expires_at", now),
            ("delivery_event", "observed_at", cutoff),
            ("operational_alert", "created_at", cutoff),
            ("notification_read", "read_at", cutoff),
            ("acs_correlation", "created_at", cutoff),
            ("worker_heartbeat", "observed_at", cutoff),
            ("sms_budget", "RowKey", cutoff_day),
            ("schedule", "updated_at", cutoff),
        ):
            count = 0
            for entity in self.client.query_entities(query_filter=f"PartitionKey eq '{partition}'"):
                if partition == "schedule" and bool(entity.get("active")):
                    continue
                if str(entity.get(field) or "") <= threshold:
                    self.client.delete_entity(partition_key=partition, row_key=entity["RowKey"])
                    count += 1
            deleted[partition] = count
        return deleted

    def health(self) -> dict[str, Any]:
        try:
            iterator = self.client.query_entities(
                query_filter="PartitionKey eq 'notification'", results_per_page=1
            )
            next(iter(iterator), None)
            counts: dict[str, int] = {}
            pending_count = 0
            oldest_pending = ""
            unapplied = 0
            for raw in self.client.query_entities(query_filter="PartitionKey eq 'notification'"):
                state = str(raw.get("sms_state") or "unknown")
                counts[state] = counts.get(state, 0) + 1
                if state in {"queued", "retryable", "claimed", "sending"}:
                    pending_count += 1
                    created = str(raw.get("created_at") or "")
                    if created and (not oldest_pending or parse_utc(created) < parse_utc(oldest_pending)):
                        oldest_pending = created
            for raw in self.client.query_entities(query_filter="PartitionKey eq 'delivery_event'"):
                unapplied += int(not bool(raw.get("applied")))
            heartbeat = self._get("worker_heartbeat", "notification_worker")
            policy = self.get_rollout_policy()
            return {
                "available": True, "backend": "azure_table", "sms_states": counts,
                "pending_count": pending_count, "oldest_pending_at": oldest_pending,
                "unapplied_delivery_events": unapplied,
                "worker": self._clean(heartbeat) if heartbeat else None,
                "rollout_policy": {
                    "version": policy.version,
                    "publish_enabled": policy.sms_publish_enabled,
                    "daily_limit": policy.daily_limit,
                    "updated_at": policy.updated_at,
                } if policy else None,
            }
        except Exception as exc:
            return {"available": False, "backend": "azure_table", "error": exc.__class__.__name__}


_repository: NotificationRepository | None = None
_repository_key: tuple[Any, ...] | None = None
_repository_lock = threading.Lock()


def get_notification_repository(settings: NotificationSettings | None = None) -> NotificationRepository:
    global _repository, _repository_key
    config = settings or NotificationSettings.from_env()
    key = (
        config.backend, str(config.sqlite_path), config.table_endpoint,
        config.table_name, config.managed_identity_client_id,
    )
    with _repository_lock:
        if _repository is None or _repository_key != key:
            _repository = (
                AzureTableNotificationRepository(
                    config.table_endpoint, config.table_name, config.managed_identity_client_id
                )
                if config.backend == "azure_table"
                else SQLiteNotificationRepository(config.sqlite_path)
            )
            _repository_key = key
        return _repository


def reset_notification_repository_for_tests() -> None:
    global _repository, _repository_key
    with _repository_lock:
        _repository = None
        _repository_key = None
