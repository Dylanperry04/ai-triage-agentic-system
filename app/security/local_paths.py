"""Path guards for credentialed local research outputs.

Credentialed MIMIC workflow artefacts are derived health-data records even after
redaction. In LOCAL_CREDENTIALED_RESEARCH they must not silently land inside the
repository tree; use a researcher-controlled output directory instead.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def patient_data_mode() -> bool:
    return os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"


def local_credentialed_research_mode() -> bool:
    return (
        os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
        and not patient_data_mode()
    )


def repo_local_output_override_allowed() -> bool:
    return (
        os.environ.get("ALLOW_REPO_LOCAL_OUTPUTS_FOR_LOCAL_RESEARCH", "").lower()
        == "true"
    )


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def assert_safe_local_credentialed_output(path: Path, *, purpose: str) -> Path:
    """Return a resolved path, or raise if local credentialed mode would write it
    under the repo tree. Public demo/test mode is unaffected."""
    resolved = path.expanduser().resolve()
    if (
        local_credentialed_research_mode()
        and not repo_local_output_override_allowed()
        and _is_relative_to(resolved, project_root())
    ):
        raise RuntimeError(
            f"LOCAL_CREDENTIALED_RESEARCH requires {purpose} outside the repo. "
            f"Refusing path: {resolved}. Set LOCAL_CREDENTIALED_OUTPUT_DIR or "
            "an explicit outside-repo path, or set "
            "ALLOW_REPO_LOCAL_OUTPUTS_FOR_LOCAL_RESEARCH=true only for tests."
        )
    return resolved


def credentialed_output_dir() -> Path | None:
    raw = os.environ.get("LOCAL_CREDENTIALED_OUTPUT_DIR", "").strip()
    base = Path(raw) if raw else _default_local_credentialed_output_base()
    scoped = _scoped_local_output_dir(base)
    return assert_safe_local_credentialed_output(
        scoped, purpose="LOCAL_CREDENTIALED_OUTPUT_DIR"
    )


def _default_local_credentialed_output_base() -> Path:
    docs = Path.home() / "Documents"
    return docs / "ai_triage_local_credentialed_output"


def _runtime_scope() -> str:
    raw = os.environ.get("LOCAL_CREDENTIALED_OUTPUT_SCOPE", "").strip()
    if raw:
        scope = raw
    else:
        scope = project_root().name
    scope = re.sub(r"[^A-Za-z0-9._-]+", "-", scope).strip(".-")
    return scope or "local-runtime"


def _scoped_local_output_dir(base: Path) -> Path:
    if os.environ.get("LOCAL_CREDENTIALED_OUTPUT_DIR_EXACT", "").lower() == "true":
        return base
    scope = _runtime_scope()
    if base.name == scope:
        return base
    return base / scope


def credentialed_artifact_path(default_path: Path, *, purpose: str) -> Path:
    """Redirect repo-local default output paths to LOCAL_CREDENTIALED_OUTPUT_DIR
    when present; otherwise guard the supplied path."""
    if local_credentialed_research_mode() and not repo_local_output_override_allowed():
        base = credentialed_output_dir()
        if base is not None:
            base.mkdir(parents=True, exist_ok=True)
            return base / default_path.name
    return assert_safe_local_credentialed_output(default_path, purpose=purpose)
