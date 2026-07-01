"""
Audit sink interface (encrypted durable storage seam).

Access/workflow audit records are written through this interface so the
DESTINATION is swappable by config. Locally this is append-only JSONL (fine for a
demo, NOT durable on Azure's ephemeral container storage). In a governed
deployment, audit records must go to encrypted durable storage — Azure Table /
Blob / Cosmos / Log Analytics — which is the documented production sink.

As with secrets, the actual Azure client is supplied by the deployment image; this
module defines the interface + a local JSONL sink + a fail-closed remote seam, so
no Azure dependency is needed in the sandbox.
"""
from __future__ import annotations

import os
from pathlib import Path
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol

import orjson

from app.security.redaction import redact_for_log, assert_no_raw_identifiers


class AuditSink(Protocol):
    def write(self, record: Dict[str, Any]) -> bool:
        ...


class LocalJsonlAuditSink:
    """Local/demo append-only JSONL sink. Redacts identifiers and asserts no raw
    identifier leaks before writing."""
    def __init__(self, path: Path):
        self.path = path

    def write(self, record: Dict[str, Any]) -> bool:
        safe = redact_for_log(record)
        assert_no_raw_identifiers(safe)  # final guard
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as f:
                f.write(orjson.dumps(safe))
                f.write(b"\n")
            return True
        except Exception:
            return False


class EncryptedDurableAuditSink:
    """
    Production seam for encrypted durable audit storage (Azure Table/Blob/Cosmos).

    The deployment supplies a client. This accepts either the project's simple
    `.upsert(record)` contract or Azure Tables' `.upsert_entity(entity)` contract.
    Absent a client, it fails closed (returns False or raises in patient-data
    mode). Records are redacted + leak-checked before sending.

    Example production wiring (in the deployment image, not here):
        from azure.data.tables import TableClient
        client = TableClient.from_connection_string(CONN, table_name="accessaudit")
        sink = EncryptedDurableAuditSink(client=client)
    """
    def __init__(self, client=None):
        self.client = client

    def write(self, record: Dict[str, Any]) -> bool:
        if self.client is None:
            # No real durable store wired. In patient-data mode this is unsafe —
            # silently dropping audit data is not acceptable for a governed
            # deployment — so we raise. Outside patient-data mode, return False.
            if os.environ.get("PATIENT_DATA_MODE", "").lower() == "true":
                raise AuditSinkNotConfiguredError(
                    "AUDIT_SINK=durable but no durable audit client is wired. "
                    "Refusing to proceed without a durable audit store in "
                    "patient-data mode (audit data must not be silently dropped)."
                )
            return False  # fail closed
        safe = redact_for_log(record)
        assert_no_raw_identifiers(safe)
        try:
            if hasattr(self.client, "upsert"):
                self.client.upsert(safe)  # simple deployment-provided contract
            elif hasattr(self.client, "upsert_entity"):
                self.client.upsert_entity(_as_table_entity(safe))
            else:
                return False
            return True
        except Exception:
            return False

    def probe_write_read(self) -> bool:
        """Write and, when possible, read back a harmless readiness probe.

        Azure Tables exposes ``upsert_entity`` and ``get_entity``. A patient-data
        deployment should prove both directions at startup so the app does not run
        with an audit sink that only exists on paper.
        """
        if self.client is None:
            return False
        probe = {
            "record_kind": "startup_probe",
            "action": "durable_audit_probe",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "source": "startup_security_check",
        }
        safe = redact_for_log(probe)
        assert_no_raw_identifiers(safe)
        entity = _as_table_entity(safe)
        if not self.write(safe):
            return False
        if hasattr(self.client, "get_entity"):
            try:
                try:
                    got = self.client.get_entity(
                        partition_key=entity["PartitionKey"],
                        row_key=entity["RowKey"],
                    )
                except TypeError:
                    got = self.client.get_entity(entity["PartitionKey"], entity["RowKey"])
                return bool(got)
            except Exception:
                return False
        # A custom client without a read method can prove write wiring but not a
        # full deployment-grade readback probe.
        return False

    def read_recent(
        self,
        limit: int = 200,
        *,
        record_kind: Optional[str] = None,
        since_utc: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Read recent audit records from a durable client.

        Supports either a deployment-provided ``read_recent(limit)`` method, Azure
        Tables' ``query_entities()``, or (outside patient-data mode only)
        ``list_entities()``. Azure Table fallback reads are bounded by a
        server-side record-kind/timestamp filter and a client-side iteration cap.
        Returned records are redacted and checked again before leaving the sink.
        """
        limit = max(1, min(int(limit or 200), 1000))
        patient_mode = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
        if self.client is None:
            if patient_mode:
                raise AuditSinkReadError(
                    "AUDIT_SINK=durable but no durable audit client is wired. "
                    "Cannot read audit events in patient-data mode."
                )
            return []
        try:
            if hasattr(self.client, "read_recent"):
                try:
                    raw_records = list(
                        self.client.read_recent(
                            limit,
                            record_kind=record_kind,
                            since_utc=since_utc,
                        )
                    )
                except TypeError:
                    raw_records = list(self.client.read_recent(limit))
            elif hasattr(self.client, "query_entities"):
                query_filter = _audit_read_query_filter(
                    record_kind=record_kind,
                    since_utc=since_utc or _default_audit_read_since_utc(),
                )
                raw_records = _take_bounded(
                    _query_entities_bounded(self.client, query_filter, limit),
                    _max_audit_read_scan(limit),
                )
            elif hasattr(self.client, "list_entities"):
                if patient_mode and os.environ.get(
                    "ALLOW_UNBOUNDED_DURABLE_AUDIT_LIST_READ", ""
                ).lower() != "true":
                    raise AuditSinkReadError(
                        "Durable audit client exposes only list_entities(); "
                        "patient-data audit reads require bounded query_entities "
                        "or a deployment-provided read_recent implementation."
                    )
                raw_records = _take_bounded(self.client.list_entities(), _max_audit_read_scan(limit))
            else:
                raise AuditSinkReadError(
                    "Durable audit client does not expose read_recent, "
                    "query_entities, or list_entities."
                )

            safe_records: List[Dict[str, Any]] = []
            for raw in raw_records:
                record = _from_table_entity(dict(raw))
                safe = redact_for_log(record)
                assert_no_raw_identifiers(safe)
                safe_records.append(safe)
            safe_records.sort(
                key=lambda rec: str(rec.get("timestamp_utc") or rec.get("created_at_utc") or "")
            )
            return safe_records[-limit:]
        except AuditSinkReadError:
            raise
        except Exception as exc:
            if patient_mode:
                raise AuditSinkReadError("Durable audit read failed.") from exc
            return []


class AuditSinkNotConfiguredError(RuntimeError):
    """Raised when a durable audit sink is required but not wired."""


class AuditSinkReadError(RuntimeError):
    """Raised when a durable audit sink cannot read records."""


def get_audit_sink(local_path: Optional[Path] = None) -> AuditSink:
    """Select the audit sink from AUDIT_SINK (default: local JSONL)."""
    kind = os.environ.get("AUDIT_SINK", "local").lower()
    if kind == "durable":
        client = _build_azure_table_client()
        return EncryptedDurableAuditSink(client=client)
    path = local_path or Path(os.environ.get("ACCESS_AUDIT_DIR", "data/processed")) / "access_audit.jsonl"
    return LocalJsonlAuditSink(path)


def _as_table_entity(record: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a safe audit record to an Azure Table-compatible entity."""
    entity: Dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (dict, list, tuple)):
            entity[key] = orjson.dumps(value).decode("utf-8")
        else:
            entity[key] = value
    partition = str(record.get("record_kind") or record.get("action") or "audit")
    seed = orjson.dumps(record, option=orjson.OPT_SORT_KEYS)
    row_key = str(
        record.get("workflow_run_id")
        or record.get("review_id")
        or record.get("rerun_id")
        or record.get("timestamp_utc")
        or hashlib.sha256(seed).hexdigest()
    )
    entity.setdefault("PartitionKey", partition[:1024])
    entity.setdefault("RowKey", row_key[:1024])
    return entity


def _from_table_entity(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an Azure Table entity back to a safe audit record shape."""
    record: Dict[str, Any] = {}
    partition = entity.get("PartitionKey")
    if partition and "record_kind" not in entity:
        record["record_kind"] = str(partition)
    for key, value in entity.items():
        if key in {"PartitionKey", "RowKey", "etag", "Timestamp"}:
            continue
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed.startswith("{") or trimmed.startswith("["):
                try:
                    record[key] = orjson.loads(trimmed)
                    continue
                except Exception:
                    pass
        record[key] = value
    return record


def _default_audit_read_since_utc() -> str:
    """Default server-side lower bound for durable audit reads."""
    raw_days = os.environ.get("AZURE_AUDIT_READ_LOOKBACK_DAYS", "30")
    try:
        days = max(1, min(int(raw_days), 366))
    except ValueError:
        days = 30
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _audit_read_query_filter(*, record_kind: Optional[str], since_utc: str) -> str:
    clauses = []
    if record_kind:
        safe_kind = str(record_kind).replace("'", "''")
        clauses.append(f"PartitionKey eq '{safe_kind}'")
    else:
        clauses.append("PartitionKey ne ''")
    if since_utc:
        safe_since = str(since_utc).replace("'", "''")
        clauses.append(f"timestamp_utc ge '{safe_since}'")
    return " and ".join(clauses)


def _query_entities_bounded(client: Any, query_filter: str, limit: int):
    try:
        return client.query_entities(query_filter=query_filter, results_per_page=limit)
    except TypeError:
        try:
            return client.query_entities(query_filter=query_filter)
        except TypeError:
            return client.query_entities(query_filter)


def _max_audit_read_scan(limit: int) -> int:
    raw = os.environ.get("AZURE_AUDIT_READ_MAX_SCAN", "")
    try:
        configured = int(raw) if raw else 0
    except ValueError:
        configured = 0
    return max(limit, min(configured or (limit * 5), 5000))


def _take_bounded(iterable, max_items: int) -> List[Any]:
    out: List[Any] = []
    for item in iterable:
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def _build_azure_table_client():
    """Build an Azure TableClient from env when the Azure extras are installed.

    Supported production env:
      - AZURE_AUDIT_TABLE_CONNECTION_STRING + AZURE_AUDIT_TABLE_NAME
      - or AZURE_AUDIT_TABLE_ENDPOINT + AZURE_AUDIT_TABLE_NAME using managed identity
    """
    table_name = os.environ.get("AZURE_AUDIT_TABLE_NAME", "").strip()
    if not table_name:
        return None
    try:
        from azure.data.tables import TableClient
        conn = os.environ.get("AZURE_AUDIT_TABLE_CONNECTION_STRING", "").strip()
        if conn:
            return TableClient.from_connection_string(conn, table_name=table_name)
        endpoint = os.environ.get("AZURE_AUDIT_TABLE_ENDPOINT", "").strip()
        if endpoint:
            from azure.identity import ManagedIdentityCredential
            client_id = os.environ.get("AZURE_CLIENT_ID") or None
            credential = (
                ManagedIdentityCredential(client_id=client_id)
                if client_id else ManagedIdentityCredential()
            )
            return TableClient(endpoint=endpoint, table_name=table_name, credential=credential)
    except Exception:
        return None
    return None
