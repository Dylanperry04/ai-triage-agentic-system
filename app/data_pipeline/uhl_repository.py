from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings
from app.constants import (
    CACHE_SCHEMA_VERSION,
    DATASET_SHA256,
    DATASET_SOURCE,
    EXPECTED_MODEL_ROWS,
    EXPECTED_SOURCE_ROWS,
    EXPECTED_TARGET_COUNTS,
    MEASUREMENT_UNITS,
    MODEL_FEATURE_RANGES,
    OUT_OF_MODEL_SCOPE_COMPLAINTS,
    SOURCE_COLUMNS,
)
from ml_training.uhl_synthetic.serving import UHL_PRESENTING_COMPLAINTS
from app.security.redaction import pseudonymous_case_uid


def _pseudonym_cache_signature() -> str:
    """Non-secret fingerprint of the active pseudonymisation context.

    The derived SQLite cache persists case_uid values. It is therefore valid
    only while the same pseudonym key/context remains active. A keyed UID for a
    fixed sentinel detects key rotation without storing or exposing the secret.
    """
    return pseudonymous_case_uid(
        DATASET_SOURCE,
        "__uhl_cache_pseudonym_context__",
    )


class UhlDatasetContractError(RuntimeError):
    """The configured dataset is absent, stale, or incompatible."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def time_bin_from_hour(hour: int) -> str:
    if hour <= 2:
        return "LATE NIGHT"
    if hour <= 6:
        return "EARLY MORNING"
    if hour <= 11:
        return "MORNING"
    if hour <= 16:
        return "AFTERNOON"
    if hour <= 20:
        return "EVENING"
    return "NIGHT"


def season_from_month(month: int) -> str:
    if month in {12, 1, 2}:
        return "WINTER"
    if month in {3, 4, 5}:
        return "SPRING"
    if month in {6, 7, 8}:
        return "SUMMER"
    return "AUTUMN"


def _open_csv(path: Path):
    if path.name.lower().endswith(".csv.gz"):
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    if path.suffix.lower() == ".csv":
        return path.open("r", encoding="utf-8-sig", newline="")
    raise UhlDatasetContractError("UHL_DATA_PATH must refer to a .csv or .csv.gz file")


def _finite_float(value: object, field: str, row_number: int) -> float:
    try:
        parsed = float(str(value).strip())
    except Exception as exc:
        raise UhlDatasetContractError(
            f"row {row_number}: {field} must be numeric"
        ) from exc
    if not math.isfinite(parsed):
        raise UhlDatasetContractError(f"row {row_number}: {field} must be finite")
    return parsed


def _integer(value: object, field: str, row_number: int) -> int:
    parsed = _finite_float(value, field, row_number)
    if not parsed.is_integer():
        raise UhlDatasetContractError(f"row {row_number}: {field} must be an integer")
    return int(parsed)


def _between(value: float, field: str, low: float, high: float, row_number: int) -> None:
    if value < low or value > high:
        raise UhlDatasetContractError(
            f"row {row_number}: {field} is outside the supported range {low:g}..{high:g}"
        )


class UhlCaseRepository:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._ready_signature: tuple[int, int, str, str] | None = None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.settings.case_cache_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _source_signature(self) -> tuple[int, int, str, str]:
        path = self.settings.data_path
        if not path.is_file():
            raise UhlDatasetContractError(f"UHL dataset file not found: {path.name}")
        stat = path.stat()
        digest = sha256_file(path)
        if digest != self.settings.expected_dataset_sha256:
            raise UhlDatasetContractError(
                "UHL dataset SHA-256 does not match UHL_DATASET_SHA256; refusing stale or incompatible data"
            )
        return (
            stat.st_mtime_ns,
            stat.st_size,
            digest,
            _pseudonym_cache_signature(),
        )

    def _cache_is_current(
        self,
        dataset_sha256: str,
        pseudonym_signature: str,
    ) -> bool:
        if not self.settings.case_cache_path.is_file():
            return False
        try:
            with self._connect() as connection:
                values = {
                    row["key"]: row["value"]
                    for row in connection.execute("SELECT key, value FROM metadata")
                }
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                return (
                    integrity == "ok"
                    and values.get("cache_schema_version") == str(CACHE_SCHEMA_VERSION)
                    and values.get("dataset_sha256") == dataset_sha256
                    and values.get("dataset_source") == DATASET_SOURCE
                    and values.get("source_columns") == json.dumps(SOURCE_COLUMNS)
                    and values.get("pseudonym_signature") == pseudonym_signature
                )
        except (OSError, sqlite3.Error, KeyError):
            return False

    def ensure_cache(self) -> None:
        with self._lock:
            path = self.settings.data_path
            if not path.is_file():
                raise UhlDatasetContractError(f"UHL dataset file not found: {path.name}")
            stat = path.stat()
            pseudonym_signature = _pseudonym_cache_signature()
            if (
                self._ready_signature is not None
                and (stat.st_mtime_ns, stat.st_size) == self._ready_signature[:2]
                and pseudonym_signature == self._ready_signature[3]
                and self.settings.case_cache_path.is_file()
            ):
                return

            signature = self._source_signature()

            if not self._cache_is_current(signature[2], signature[3]):
                self._build_cache(signature[2], signature[3])

            self._ready_signature = signature

    def clear_derived_cache(self) -> None:
        """Remove only the rebuildable case cache; review/audit state is separate."""
        with self._lock:
            target = self.settings.case_cache_path.resolve()
            if target.name != "uhl_cases.sqlite3" or target.parent.name != "cache":
                raise RuntimeError("refusing to clear a case cache outside the configured cache directory")
            if target.exists():
                target.unlink()
            self._ready_signature = None

    def _build_cache(
        self,
        dataset_sha256: str,
        pseudonym_signature: str,
    ) -> None:
        target = self.settings.case_cache_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.building")
        if temporary.exists():
            temporary.unlink()
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA temp_store=MEMORY;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE cases (
                    case_uid TEXT PRIMARY KEY,
                    row_number INTEGER NOT NULL UNIQUE,
                    patient_display_label TEXT NOT NULL,
                    arrival_time TEXT NOT NULL,
                    age INTEGER NOT NULL,
                    presenting_complaint TEXT NOT NULL,
                    temperature REAL NOT NULL,
                    heartrate REAL NOT NULL,
                    resprate REAL NOT NULL,
                    o2sat REAL NOT NULL,
                    sbp REAL NOT NULL,
                    dbp REAL NOT NULL,
                    pain REAL NOT NULL,
                    model_eligible INTEGER NOT NULL CHECK(model_eligible IN (0,1)),
                    pathway TEXT NOT NULL
                );
                CREATE INDEX idx_cases_arrival ON cases(arrival_time, row_number);
                CREATE INDEX idx_cases_complaint ON cases(presenting_complaint);
                """
            )
            allowed_complaints = set(UHL_PRESENTING_COMPLAINTS) | set(OUT_OF_MODEL_SCOPE_COMPLAINTS)
            target_counts = {str(label): 0 for label in range(1, 6)}
            source_rows = 0
            model_rows = 0
            batch: list[tuple[Any, ...]] = []
            seen_identifiers: set[str] = set()
            with _open_csv(self.settings.data_path) as handle:
                reader = csv.DictReader(handle)
                actual_columns = tuple(reader.fieldnames or ())
                if actual_columns != SOURCE_COLUMNS:
                    raise UhlDatasetContractError(
                        "UHL schema mismatch: expected exact ordered columns "
                        f"{list(SOURCE_COLUMNS)}, got {list(actual_columns)}"
                    )
                for row_number, row in enumerate(reader, start=2):
                    source_rows += 1
                    identifier = str(row["AttendanceID"] or "").strip()
                    if not identifier:
                        raise UhlDatasetContractError(
                            f"row {row_number}: AttendanceID must be non-empty"
                        )
                    if identifier in seen_identifiers:
                        raise UhlDatasetContractError(
                            f"row {row_number}: AttendanceID must be unique"
                        )
                    seen_identifiers.add(identifier)
                    try:
                        arrival = datetime.fromisoformat(str(row["DATE"]).strip())
                    except Exception as exc:
                        raise UhlDatasetContractError(
                            f"row {row_number}: DATE must be an ISO-compatible timestamp"
                        ) from exc
                    year = _integer(row["year"], "year", row_number)
                    month = _integer(row["month"], "month", row_number)
                    hour = _integer(row["hour"], "hour", row_number)
                    if (year, month, hour) != (arrival.year, arrival.month, arrival.hour):
                        raise UhlDatasetContractError(
                            f"row {row_number}: year/month/hour do not match DATE"
                        )
                    time_bin = str(row["time_bin"] or "").strip().upper()
                    season = str(row["season"] or "").strip().upper()
                    if time_bin != time_bin_from_hour(hour) or season != season_from_month(month):
                        raise UhlDatasetContractError(
                            f"row {row_number}: time_bin/season do not match month/hour"
                        )
                    complaint = str(row["PresentingComplaint_base"] or "").strip().upper()
                    if complaint not in allowed_complaints:
                        raise UhlDatasetContractError(
                            f"row {row_number}: presenting complaint is not in the fitted contract"
                        )
                    for required_text in (
                        "EdLocationName_base",
                        "PresentingComplaint_token",
                    ):
                        if not str(row[required_text] or "").strip():
                            raise UhlDatasetContractError(
                                f"row {row_number}: {required_text} must be non-empty"
                            )
                    age = _integer(row["Age"], "Age", row_number)
                    _between(age, "Age", *MODEL_FEATURE_RANGES["age"], row_number)
                    for field in ("HoursinEd", "TTT_minutes", "TTC_minutes"):
                        _finite_float(row[field], field, row_number)
                    for field in ("Admitted", "Over6Hours", "Over9Hours", "Over24", "Over75"):
                        binary = _integer(row[field], field, row_number)
                        if binary not in {0, 1}:
                            raise UhlDatasetContractError(
                                f"row {row_number}: {field} must be 0 or 1"
                            )
                    target_label = _integer(
                        row["master_estimated_acuity"], "master_estimated_acuity", row_number
                    )
                    if target_label not in {1, 2, 3, 4, 5}:
                        raise UhlDatasetContractError(
                            f"row {row_number}: master_estimated_acuity must be 1..5"
                        )
                    target_counts[str(target_label)] += 1
                    vitals = {
                        field: _finite_float(row[field], field, row_number)
                        for field in (
                            "temperature",
                            "heartrate",
                            "resprate",
                            "o2sat",
                            "sbp",
                            "dbp",
                            "pain",
                        )
                    }
                    model_eligible = complaint not in OUT_OF_MODEL_SCOPE_COMPLAINTS
                    if model_eligible:
                        for field, value in vitals.items():
                            _between(value, field, *MODEL_FEATURE_RANGES[field], row_number)
                        model_rows += 1
                        pathway = "ML_RESEARCH_ASSESSMENT"
                    else:
                        pathway = "DETERMINISTIC_NON_ML_PATHWAY"
                    # Keep the established 22.4 external identifier contract:
                    # readable dataset prefix + a 24-hex keyed pseudonym.  The
                    # raw AttendanceID is validated, then discarded.
                    case_uid = pseudonymous_case_uid(DATASET_SOURCE, source_rows)
                    batch.append(
                        (
                            case_uid,
                            source_rows,
                            f"UHL Case {source_rows:06d}",
                            arrival.isoformat(sep=" "),
                            age,
                            complaint,
                            vitals["temperature"],
                            vitals["heartrate"],
                            vitals["resprate"],
                            vitals["o2sat"],
                            vitals["sbp"],
                            vitals["dbp"],
                            vitals["pain"],
                            int(model_eligible),
                            pathway,
                        )
                    )
                    if len(batch) >= 5000:
                        connection.executemany(
                            "INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch
                        )
                        batch.clear()
                if batch:
                    connection.executemany(
                        "INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch
                    )
            if dataset_sha256 == DATASET_SHA256:
                if source_rows != EXPECTED_SOURCE_ROWS:
                    raise UhlDatasetContractError(
                        f"pinned UHL dataset row count mismatch: {source_rows} != {EXPECTED_SOURCE_ROWS}"
                    )
                if model_rows != EXPECTED_MODEL_ROWS:
                    raise UhlDatasetContractError(
                        f"pinned UHL model-scope count mismatch: {model_rows} != {EXPECTED_MODEL_ROWS}"
                    )
                if target_counts != EXPECTED_TARGET_COUNTS:
                    raise UhlDatasetContractError(
                        "pinned UHL target distribution does not match training provenance"
                    )
            metadata = {
                "cache_schema_version": str(CACHE_SCHEMA_VERSION),
                "dataset_source": DATASET_SOURCE,
                "dataset_sha256": dataset_sha256,
                "source_columns": json.dumps(SOURCE_COLUMNS),
                "source_rows": str(source_rows),
                "model_rows": str(model_rows),
                "pseudonym_signature": pseudonym_signature,
                "target_counts": json.dumps(target_counts, sort_keys=True),
            }
            connection.executemany(
                "INSERT INTO metadata(key,value) VALUES (?,?)", metadata.items()
            )
            connection.commit()
        except Exception:
            connection.close()
            if temporary.exists():
                temporary.unlink()
            raise
        else:
            connection.close()
            os.replace(temporary, target)

    def status(self) -> dict[str, Any]:
        self.ensure_cache()
        with self._connect() as connection:
            metadata = {
                row["key"]: row["value"]
                for row in connection.execute("SELECT key,value FROM metadata")
            }
        return {
            "dataset_source": DATASET_SOURCE,
            "dataset_revision": "corrected_acuity_vitals_20260810",
            "dataset_ready": True,
            "dataset_sha256": metadata["dataset_sha256"],
            "source_rows": int(metadata["source_rows"]),
            "model_scope_rows": int(metadata["model_rows"]),
            "deterministic_path_rows": int(metadata["source_rows"]) - int(metadata["model_rows"]),
            "target_counts": json.loads(metadata["target_counts"]),
            "cache_schema_version": int(metadata["cache_schema_version"]),
            "measurement_units": MEASUREMENT_UNITS,
        }

    @staticmethod
    def _case_payload(row: sqlite3.Row) -> dict[str, Any]:
        # Adapt the UHL row to the unchanged 22.4 EDTriageCase contract.  The
        # synthetic row number is used only as an internal integer key; the API
        # continues to expose the UHL SHA-derived case_uid and strips raw IDs.
        internal_id = int(row["row_number"])
        triage = {
            "subject_id": internal_id,
            "stay_id": internal_id,
            "chiefcomplaint": row["presenting_complaint"],
            "age": row["age"],
            "temperature": row["temperature"],
            "temperature_unit": "F",
            "heartrate": row["heartrate"],
            "resprate": row["resprate"],
            "o2sat": row["o2sat"],
            "sbp": row["sbp"],
            "dbp": row["dbp"],
            "pain": str(row["pain"]),
            "pain_raw": str(row["pain"]),
        }
        return {
            "case_uid": row["case_uid"],
            "source_dataset": DATASET_SOURCE,
            "stay_id": internal_id,
            "subject_id": internal_id,
            "display_name": row["patient_display_label"],
            "stay_number": f"UHL-{internal_id:06d}",
            "edstay": {
                "subject_id": internal_id,
                "stay_id": internal_id,
                "intime": row["arrival_time"],
            },
            "triage": triage,
            "vitals_timeseries": [],
            "diagnoses": [],
            "medrecon": [],
            "pyxis": [],
            "audit_metadata": {
                "arrival_time": row["arrival_time"],
                "model_eligible": bool(row["model_eligible"]),
                "pathway": row["pathway"],
                "measurement_units": MEASUREMENT_UNITS,
            },
            "retrospective_metadata": {},
            "synthetic_demo": True,
            "demo_data_notice": (
                "UHL synthetic research case. Not real patient data and not for clinical use."
            ),
        }

    def list_cases(self, *, offset: int = 0, limit: int = 100, query: str | None = None) -> dict[str, Any]:
        self.ensure_cache()
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 100_000))
        where = ""
        parameters: list[Any] = []
        if query and query.strip():
            value = f"%{query.strip()}%"
            where = (
                " WHERE case_uid LIKE ? OR patient_display_label LIKE ? "
                "OR presenting_complaint LIKE ?"
            )
            parameters.extend([value, value, value])
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM cases{where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM cases{where} ORDER BY arrival_time, row_number LIMIT ? OFFSET ?",
                [*parameters, limit, offset],
            ).fetchall()
        next_offset = offset + len(rows) if offset + len(rows) < total else None
        return {
            "cases": [self._case_payload(row) for row in rows],
            "pagination": {
                "offset": offset,
                "limit": limit,
                "returned": len(rows),
                "total": total,
                "has_more": next_offset is not None,
                "next_offset": next_offset,
            },
        }

    def get_case(self, case_uid: str) -> dict[str, Any] | None:
        self.ensure_cache()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cases WHERE case_uid = ?", (case_uid,)
            ).fetchone()
        return None if row is None else self._case_payload(row)
