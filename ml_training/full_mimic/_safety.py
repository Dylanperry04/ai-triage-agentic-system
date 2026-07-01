"""
Shared safety guards for the full-MIMIC scaffolding.

EVERY script in ml_training/full_mimic/ calls require_safe_environment() FIRST. It
enforces the DUA rules so these scripts cannot be run unsafely:

  - MIMIC_FULL_ED_DIR must be set and exist (the credentialed data path).
  - The path must be OUTSIDE this repository (anti-copy guard).
  - Either LOCAL_CREDENTIALED_RESEARCH=true (approved local machine) or
    PATIENT_DATA_MODE=true (secured deployment) must be active.
  - Outputs go to a caller-chosen directory that must ALSO be outside the repo
    (so aggregate artefacts are never written into the committed tree by default).

These scripts must NEVER print or save raw patient rows. Helpers here only ever
emit aggregate/de-identified summaries.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

SYNTHETIC_OR_DEMO_PATH_PARTS = {
    "data/demo",
    "tests/fixtures",
}
SYNTHETIC_OR_DEMO_NAME_MARKERS = (
    "azure_supervisor_demo_cases",
    "sample_mimic_cases",
    "sample_mimic_full_cases",
    "mimic-iv-ed-demo",
    "synthetic",
    "fixture",
)


class UnsafeEnvironmentError(RuntimeError):
    pass


def _resolve_outside_repo(path: Path, what: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(REPO_ROOT)
        inside = True
    except ValueError:
        inside = False
    if inside:
        raise UnsafeEnvironmentError(
            f"{what} ({resolved}) is inside the repository. Credentialed MIMIC data "
            "and its derived artefacts must live OUTSIDE the repo (DUA)."
        )
    return resolved


def assert_not_synthetic_demo_path(path: Path, what: str = "MIMIC_FULL_ED_DIR") -> None:
    """Refuse paths that are clearly bundled synthetic/demo/test fixtures.

    This guard is intentionally path-based. Real full MIMIC lives outside the repo
    under a credentialed user's control; bundled demo/test JSONL/CSV fixtures must
    never become a model-training or model-evaluation source.
    """
    resolved = path.expanduser().resolve()
    norm = str(resolved).replace("\\", "/").lower()
    name = resolved.name.lower()
    if any(marker in norm for marker in SYNTHETIC_OR_DEMO_PATH_PARTS) or any(
        marker in norm or marker in name for marker in SYNTHETIC_OR_DEMO_NAME_MARKERS
    ):
        raise UnsafeEnvironmentError(
            "Refusing to train/evaluate model from synthetic/demo/test fixture path. "
            "Model training must use approved full MIMIC-IV-ED only."
        )


def require_safe_environment(output_dir: Optional[str] = None) -> dict:
    """Validate the environment and return resolved, safe paths. Raises
    UnsafeEnvironmentError if anything is unsafe."""
    raw = os.environ.get("MIMIC_FULL_ED_DIR")
    if not raw:
        raise UnsafeEnvironmentError(
            "MIMIC_FULL_ED_DIR is not set. Point it at the credentialed MIMIC-IV-ED "
            "'ed' directory on your approved environment (outside this repo)."
        )
    patient_mode = os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
    local_research = (
        os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        and not patient_mode
    )
    if not (patient_mode or local_research):
        raise UnsafeEnvironmentError(
            "Set LOCAL_CREDENTIALED_RESEARCH=true for an approved local research "
            "machine, or PATIENT_DATA_MODE=true for secured deployment, before "
            "processing full MIMIC."
        )
    ed_dir = _resolve_outside_repo(Path(raw), "MIMIC_FULL_ED_DIR")
    assert_not_synthetic_demo_path(ed_dir, "MIMIC_FULL_ED_DIR")
    if not ed_dir.exists():
        raise UnsafeEnvironmentError(f"MIMIC_FULL_ED_DIR does not exist: {ed_dir}")

    out = output_dir or os.environ.get("MIMIC_FULL_OUTPUT_DIR")
    if not out:
        raise UnsafeEnvironmentError(
            "Set MIMIC_FULL_OUTPUT_DIR (or pass --output-dir) to a directory OUTSIDE "
            "the repo where aggregate artefacts (metrics, cards) will be written."
        )
    out_dir = _resolve_outside_repo(Path(out), "output dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    return {"ed_dir": ed_dir, "output_dir": out_dir}


def assert_no_raw_rows(obj) -> None:
    """Defensive guard: refuse to write anything that looks like raw patient rows.
    Aggregate artefacts are dicts of numbers/strings; a list of per-patient records
    is rejected."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        suspicious = {"subject_id", "stay_id", "hadm_id"}
        if suspicious & set(obj[0].keys()):
            raise UnsafeEnvironmentError(
                "Refusing to write what looks like raw patient rows. Only aggregate/"
                "de-identified summaries may be saved."
            )
