"""
Opt-in loader for the public MIMIC-IV-ED v2.2 demo subset.

This is a view-only convenience for local app inspection of the public PhysioNet
demo zip. It is deliberately separate from the credentialed full-MIMIC loader:
the cases are labelled as MIMIC-IV-ED-Demo-v2.2, so the ML prediction agent will
withhold predictions rather than treating them as full-data cases.
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

import pandas as pd

PUBLIC_MIMIC_ED_DEMO_LABEL = "MIMIC-IV-ED-Demo-v2.2"
MAX_PUBLIC_DEMO_STAYS = 300
PUBLIC_SAMPLE_NOTICE = (
    "Public MIMIC-IV-ED demo subset. View-only sample data, not the credentialed "
    "full MIMIC-IV-ED dataset. ML prediction is withheld for this source."
)


def public_sample_view_allowed() -> bool:
    if os.environ.get("ALLOW_MIMIC_ED_PUBLIC_SAMPLE_VIEW", "").lower() != "true":
        return False
    credentialed = (
        os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        or os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    )
    return not credentialed


def configured_public_sample_path() -> Optional[Path]:
    raw = (
        os.environ.get("MIMIC_ED_PUBLIC_SAMPLE_ZIP")
        or os.environ.get("MIMIC_ED_PUBLIC_SAMPLE_DIR")
        or ""
    ).strip()
    return Path(raw).expanduser() if raw else None


def _safe_extract_zip(zip_path: Path, dest: Path) -> None:
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError("Unsafe path found inside MIMIC public sample zip") from exc
        zf.extractall(dest)


def _has_core_tables(ed_dir: Path) -> bool:
    from app.data_pipeline.mimic_adapter import candidate_mimic_table_paths

    return all(
        any(candidate.is_file() for candidate in candidate_mimic_table_paths(ed_dir, table))
        for table in ("edstays", "triage")
    )


def _find_ed_dir(root: Path) -> Path:
    if _has_core_tables(root):
        return root
    for candidate in root.rglob("ed"):
        if candidate.is_dir() and _has_core_tables(candidate):
            return candidate
    raise FileNotFoundError(
        "Public MIMIC sample path must be a zip or directory containing edstays "
        "and triage CSV tables under an ed/ directory."
    )


def _load_cases_from_ed_dir(ed_dir: Path, n: Optional[int] = None) -> list[dict[str, Any]]:
    from app.data_pipeline.mimic_adapter import load_mimic_table, dataframe_to_cases

    guard_nrows = MAX_PUBLIC_DEMO_STAYS + 1
    edstays_guard = load_mimic_table(ed_dir, "edstays", nrows=guard_nrows)
    triage_guard = load_mimic_table(ed_dir, "triage", nrows=guard_nrows)
    _assert_public_demo_subset(edstays_guard, triage_guard)

    nrows = int(n) if n is not None else None
    edstay_kwargs = {"nrows": nrows} if nrows is not None else {}
    edstays = load_mimic_table(ed_dir, "edstays", **edstay_kwargs)
    triage = load_mimic_table(ed_dir, "triage")
    if nrows is not None and "stay_id" in edstays.columns and "stay_id" in triage.columns:
        selected_stays = set(
            pd.to_numeric(edstays["stay_id"], errors="coerce")
            .dropna()
            .astype("int64")
            .tolist()
        )
        triage_stay_ids = pd.to_numeric(triage["stay_id"], errors="coerce")
        triage = triage.loc[triage_stay_ids.isin(selected_stays)].copy()

    empty = pd.DataFrame()
    cases = dataframe_to_cases(
        edstays,
        triage,
        empty,
        empty,
        empty,
        empty,
        n=n,
        source_dataset_label=PUBLIC_MIMIC_ED_DEMO_LABEL,
    )
    rows: list[dict[str, Any]] = []
    for case in cases:
        row = case.model_dump(mode="json") if hasattr(case, "model_dump") else dict(case)
        row["public_mimic_demo"] = True
        row["demo_data_notice"] = PUBLIC_SAMPLE_NOTICE
        rows.append(row)
    return rows


def _assert_public_demo_subset(edstays: pd.DataFrame, triage: pd.DataFrame) -> None:
    n_edstays = int(len(edstays))
    n_triage = int(len(triage))
    if n_edstays > MAX_PUBLIC_DEMO_STAYS or n_triage > MAX_PUBLIC_DEMO_STAYS:
        raise ValueError(
            "Configured MIMIC public sample contains more rows than expected for "
            "the public demo subset. Refusing to label it as public demo data. "
            "Use MIMIC_FULL_ED_DIR with LOCAL_CREDENTIALED_RESEARCH=true for full "
            "credentialed MIMIC-IV-ED."
        )


def load_public_mimic_ed_cases(n: Optional[int] = None) -> list[dict[str, Any]]:
    if not public_sample_view_allowed():
        return []
    source = configured_public_sample_path()
    if source is None:
        return []
    source = source.expanduser()
    if not source.exists():
        raise FileNotFoundError("MIMIC public sample path does not exist")
    if source.is_file():
        if source.suffix.lower() != ".zip":
            raise ValueError("MIMIC_ED_PUBLIC_SAMPLE_ZIP must point to a .zip file")
        with tempfile.TemporaryDirectory(prefix="mimic_public_sample_") as tmp:
            root = Path(tmp)
            _safe_extract_zip(source, root)
            return _load_cases_from_ed_dir(_find_ed_dir(root), n=n)
    return _load_cases_from_ed_dir(_find_ed_dir(source), n=n)


def count_public_mimic_ed_cases() -> int:
    return len(load_public_mimic_ed_cases(n=None))
