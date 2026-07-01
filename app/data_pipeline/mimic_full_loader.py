"""
Full credentialed MIMIC-IV-ED loader — DUA-safe seam.

The full MIMIC-IV-ED dataset is credentialed PhysioNet data under a Data Use
Agreement. It must NEVER be copied into this repository, a build artifact, or a
shared sandbox. This loader reads it from MIMIC_FULL_ED_DIR — a path the
credentialed user controls on their own environment — and enforces guards so it
cannot be used carelessly:

  1. FAIL CLOSED if MIMIC_FULL_ED_DIR is not set.
  2. REQUIRE either LOCAL_CREDENTIALED_RESEARCH=true for an approved loopback-only
     local research machine, or PATIENT_DATA_MODE=true for secured deployment.
  3. ANTI-COPY GUARD: refuse if the configured path is inside this repository
     tree (which would mean credentialed data had been copied in).

This module deliberately does the minimum: it validates the seam and lists the
expected files. Actual full-scale feature building / training is a later phase and
runs on the credentialed environment, not here.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from app.config import settings
from app.security.identity import (
    patient_data_mode, local_credentialed_research_mode,
    azure_supervisor_demo_mode, credentialed_data_access_allowed,
    real_mimic_azure_demo_mode,
)


class CredentialedDataError(RuntimeError):
    """Raised when the full-MIMIC seam is used unsafely."""


# The files we expect in a MIMIC-IV-ED 'ed' directory.
EXPECTED_FILES = [
    "edstays.csv.gz", "triage.csv.gz", "vitalsign.csv.gz",
    "diagnosis.csv.gz", "medrecon.csv.gz", "pyxis.csv.gz",
]
EXPECTED_TABLE_NAMES = ["edstays", "triage", "vitalsign", "diagnosis", "medrecon", "pyxis"]

# Repo root (this file is app/data_pipeline/mimic_full_loader.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _configured_ed_dir() -> Path | None:
    import os
    raw = os.environ.get("MIMIC_FULL_ED_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return settings.mimic_full_ed_dir


def _table_file_path(ed_dir: Path, table_name: str) -> Path | None:
    from app.data_pipeline.mimic_adapter import candidate_mimic_table_paths
    for candidate in candidate_mimic_table_paths(ed_dir, table_name):
        if candidate.is_file():
            return candidate
    return None


def _assert_safe_to_use() -> Path:
    """Run all guards; return the validated full-MIMIC directory. Raises
    CredentialedDataError with a SPECIFIC, non-sensitive reason on any failure
    (the reason names the category of problem, never the path contents)."""
    path = _configured_ed_dir()
    if path is None:
        raise CredentialedDataError(
            "MIMIC_FULL_ED_DIR is not set. Full MIMIC-IV-ED is credentialed data "
            "and is read from a path you control on your credentialed environment. "
            "Set MIMIC_FULL_ED_DIR to the 'ed' directory to enable."
        )
    # Either the secured production profile OR the local credentialed-research
    # profile must be active. This lets a credentialed researcher load their own
    # data locally without asserting the full production security posture.
    if not credentialed_data_access_allowed():
        raise CredentialedDataError(
            "Credentialed-data access is not enabled. Set LOCAL_CREDENTIALED_RESEARCH=true "
            "for an approved local research machine, or PATIENT_DATA_MODE=true for the "
            "secured production deployment. Refusing to load full MIMIC-IV-ED otherwise."
        )
    resolved = path.expanduser().resolve()
    # Anti-copy guard: the credentialed data must live OUTSIDE this repo.
    try:
        resolved.relative_to(_REPO_ROOT)
        inside_repo = True
    except ValueError:
        inside_repo = False
    if inside_repo:
        raise CredentialedDataError(
            "MIMIC_FULL_ED_DIR is inside the repository tree. Credentialed MIMIC "
            "data must NEVER be copied into this repo or a build artifact. Point it "
            "at a path OUTSIDE the repo."
        )
    if not resolved.exists():
        raise CredentialedDataError(
            "MIMIC_FULL_ED_DIR does not exist. Check the path and that it is "
            "reachable from the process running the backend."
        )
    if not resolved.is_dir():
        raise CredentialedDataError(
            "MIMIC_FULL_ED_DIR is not a directory. It must point at the MIMIC-IV-ED "
            "'ed' directory (the folder containing edstays.csv.gz, triage.csv.gz, ...)."
        )
    # Schema/level guard: confirm the expected core tables are present, so a wrong
    # directory level (e.g. the dataset root instead of the 'ed' subfolder) gives a
    # clear reason rather than an empty result.
    core = ["edstays", "triage"]
    missing_core = [f"{t}.csv(.gz)" for t in core if _table_file_path(resolved, t) is None]
    if missing_core:
        raise CredentialedDataError(
            f"MIMIC_FULL_ED_DIR is missing required table(s): {missing_core}. "
            "Point MIMIC_FULL_ED_DIR at the 'ed' directory containing edstays/triage "
            "CSV tables (not the dataset root or a parent folder)."
        )
    return resolved


def is_full_mimic_available() -> bool:
    """True only if the seam is configured AND safe to use. Never raises."""
    try:
        _assert_safe_to_use()
        return True
    except CredentialedDataError:
        return False


def full_mimic_diagnostic() -> dict:
    """Return a structured, NON-SENSITIVE diagnostic of why full-MIMIC is or is not
    loadable, for a status endpoint / diagnostic command. Never exposes the path
    value or any patient data; only the category of problem and which mode is
    active. Never raises."""
    import os
    try:
        azure_demo = azure_supervisor_demo_mode()
        real_mimic_demo = real_mimic_azure_demo_mode()
    except Exception:
        azure_demo = False
        real_mimic_demo = False
    mode = (
        "patient_data" if patient_data_mode()
        else "local_credentialed_research" if local_credentialed_research_mode()
        else "azure_supervisor_demo" if azure_demo
        else "public_demo"
    )
    dir_set = _configured_ed_dir() is not None
    model_raw = os.environ.get("MIMIC_FULL_MODEL_PATH", "")
    model_set = bool(model_raw)
    model_exists = False
    if model_raw:
        try:
            model_exists = Path(model_raw).expanduser().exists()
        except Exception:
            model_exists = False
    available = False
    reason = "ok"
    try:
        _assert_safe_to_use()
        available = True
    except CredentialedDataError as e:
        reason = str(e)
    full_mimic_requested_for_azure_demo = (
        azure_demo
        and os.environ.get("ALLOW_FULL_MIMIC_IN_AZURE_DEMO", "").lower() == "true"
    )
    if full_mimic_requested_for_azure_demo and not available:
        reason = (
            "Full MIMIC requested for Azure demo, but MIMIC_FULL_ED_DIR is not "
            "readable by the backend. " + reason
        )
    return {
        "active_profile": mode,
        "data_source_mode": (
            "credentialed_mimic_azure_demo" if real_mimic_demo
            else "synthetic_supervisor_demo" if azure_demo else mode
        ),
        "full_mimic_requested_for_azure_demo": full_mimic_requested_for_azure_demo,
        "real_mimic_demo_acknowledged": real_mimic_demo,
        "mimic_full_dir_env_set": dir_set,
        "mimic_full_model_env_set": model_set,
        "mimic_full_model_file_exists": model_exists,
        "full_mimic_loadable": available,
        "reason": reason,
    }


def full_mimic_status() -> dict:
    """Single source of truth for full-MIMIC configuration status, used by the
    API (/ and /health) and the Streamlit sidebar so they always agree. Reports
    only full MIMIC-IV-ED; never references demo/KTAS. Never raises.

    - mimic_full_dir_configured: MIMIC_FULL_ED_DIR set, outside the repo, exists
      AND safe to use (the credentialed ED directory)
    - mimic_full_model_configured: MIMIC_FULL_MODEL_PATH set and the file exists
    """
    import os
    dir_ok = is_full_mimic_available()
    model_raw = os.environ.get("MIMIC_FULL_MODEL_PATH", "")
    model_ok = False
    if model_raw:
        try:
            model_ok = Path(model_raw).expanduser().exists()
        except Exception:
            model_ok = False
    return {
        "dataset": "MIMIC-IV-ED-Full-v2.2",
        "datasets_available": ["MIMIC-IV-ED-Full-v2.2"],
        "mimic_full_dir_configured": dir_ok,
        "mimic_full_model_configured": model_ok,
        "prediction_model_source": "MIMIC_FULL_MODEL_PATH",
        "synthetic_fixtures": "tests_and_azure_supervisor_demo_only",
        "clinical_use": "NOT_FOR_CLINICAL_USE",
        # Patient-data readiness can only be asserted by the hospital deployment
        # (Entra/MFA/private network/Key Vault/durable audit/governance). The
        # codebase never claims this is true.
        "patient_data_ready": False,
    }


def validate_full_mimic_dir() -> dict:
    """Validate the configured full-MIMIC directory and report which expected
    files are present. Runs all safety guards first."""
    path = _assert_safe_to_use()
    present = [
        p.name for t in EXPECTED_TABLE_NAMES
        if (p := _table_file_path(path, t)) is not None
    ]
    missing = [f"{t}.csv(.gz)" for t in EXPECTED_TABLE_NAMES if _table_file_path(path, t) is None]
    return {
        "path": str(path),
        "present_files": present,
        "missing_files": missing,
        "ready": not missing,
        "note": (
            "Full credentialed MIMIC-IV-ED. Read from outside the repo under a "
            "DUA. Feature building / training for full data runs on the "
            "credentialed environment, not in this sandbox."
        ),
    }


def validate_full_mimic_schema() -> dict:
    """Run the column-schema validation on the full-MIMIC tables (after guards).
    Returns the adapter's validation report so a credentialed user can confirm
    the real columns before training."""
    path = _assert_safe_to_use()
    from app.data_pipeline.mimic_adapter import validate_mimic_tables
    return validate_mimic_tables(path)


def load_mimic_full_cases(n=None):
    """
    Fast full-MIMIC case loader for training/feature building.

    Uses all eligible full-MIMIC ED stays from edstays.csv.gz + triage.csv.gz.
    Does not expand vitalsign/diagnosis/medrecon/pyxis because those are large
    auxiliary/post-triage tables and are not used by the triage-time feature
    builder. validate_full_mimic_schema() still checks all six tables.
    """
    import pandas as pd
    path = _assert_safe_to_use()
    from app.data_pipeline.mimic_adapter import (
        load_mimic_table,
        dataframe_to_cases,
        EXPECTED_COLUMNS,
        SOURCE_DATASET_LABEL_FULL,
    )

    edstays = load_mimic_table(path, "edstays")
    triage = load_mimic_table(path, "triage")

    empty_vitalsign = pd.DataFrame(columns=EXPECTED_COLUMNS["vitalsign"])
    empty_diagnosis = pd.DataFrame(columns=EXPECTED_COLUMNS["diagnosis"])
    empty_medrecon = pd.DataFrame(columns=EXPECTED_COLUMNS["medrecon"])
    empty_pyxis = pd.DataFrame(columns=EXPECTED_COLUMNS["pyxis"])

    return dataframe_to_cases(
        edstays,
        triage,
        empty_vitalsign,
        empty_diagnosis,
        empty_medrecon,
        empty_pyxis,
        n=n,
        source_dataset_label=SOURCE_DATASET_LABEL_FULL,
    )

def load_mimic_full_cases_triage_time(n=None):
    """
    SERVING loader: load only the TRIAGE-TIME tables (edstays + triage), not the
    full-stay tables (vitalsign/diagnosis/medrecon/pyxis), which are not
    triage-time inputs and are expensive to load for every request.

    The heavy tables are passed as empty frames, so the resulting cases have empty
    vitals_timeseries/diagnoses/medrecon/pyxis — appropriate for triage-time
    listing/assessment. Reduces per-request bulk-data exposure and load time.
    """
    import pandas as pd
    path = _assert_safe_to_use()
    from app.data_pipeline.mimic_adapter import (
        load_mimic_table, dataframe_to_cases, SOURCE_DATASET_LABEL_FULL,
    )
    nrows = int(n) if n is not None else None
    edstay_kwargs = {"nrows": nrows} if nrows is not None else {}
    edstays = load_mimic_table(path, "edstays", **edstay_kwargs)
    triage = load_mimic_table(path, "triage")
    if nrows is not None and "stay_id" in edstays.columns and "stay_id" in triage.columns:
        # Do not assume the first n rows of triage.csv.gz correspond to the first
        # n rows of edstays.csv.gz. Select triage rows by stay_id so page serving
        # keeps the correct triage snapshot for each loaded ED stay.
        selected_stays = set(
            pd.to_numeric(edstays["stay_id"], errors="coerce")
            .dropna()
            .astype("int64")
            .tolist()
        )
        triage_stay_ids = pd.to_numeric(triage["stay_id"], errors="coerce")
        triage = triage.loc[triage_stay_ids.isin(selected_stays)].copy()
    empty = pd.DataFrame()
    return dataframe_to_cases(
        edstays, triage, empty, empty, empty, empty,
        n=n, source_dataset_label=SOURCE_DATASET_LABEL_FULL,
    )
