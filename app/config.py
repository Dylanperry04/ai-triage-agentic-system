"""Project-wide configuration for the 22.4 runtime with UHL data/model assets."""
import os
from pathlib import Path
from pydantic import BaseModel


def load_local_dotenv_if_present() -> None:
    """Load a repo-local .env for local development only.

    Azure App Service / Container Apps expose App Settings as real environment
    variables; they do not automatically inherit this file. ``load_dotenv``
    does not override values that are already set by the hosting environment.
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_path, override=False)
    except Exception:
        return


load_local_dotenv_if_present()


def _default_cors_origins() -> list[str]:
    """
    Reads CORS_ALLOWED_ORIGINS from the environment as a comma-separated
    list (e.g. "https://my-streamlit-app.azurewebsites.net,http://localhost:8501").
    Falls back to local-development-only origins if unset, so a real
    deployment must explicitly set this rather than silently inheriting a
    wildcard. See infrastructure/azure_deploy.md for how to set this for
    an actual Azure deployment.
    """
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return ["http://localhost:8501", "http://127.0.0.1:8501"]


def _provisional_mts_enabled() -> bool:
    """
    Whether to register the provisional MTS research ruleset at startup so the
    engine assigns (clearly-labelled, clinician-review-required) provisional
    Manchester categories.

    Defaults to True so the research demo shows working categories. Set the
    environment variable PROVISIONAL_MTS_MODE to "off"/"0"/"false" to disable
    it and return the engine to its fully-gated "no category without an
    approved ruleset" behaviour. The categories this produces are NOT the
    official Manchester Triage System and are NOT clinically approved; see
    app/rules/provisional_mts_ruleset.py and RULESET_PROVENANCE.md.
    """
    raw = os.environ.get("PROVISIONAL_MTS_MODE", "").strip().lower()
    if raw in {"off", "0", "false", "no", "disabled"}:
        return False
    return True


def _parse_high_acuity_threshold(raw: str) -> float | None:
    """Validate the high-acuity operating point, failing loudly on nonsense.

    An unvalidated float() here is dangerous in a way that is easy to miss,
    because every bad value fails SILENTLY at the comparison rather than at
    startup:

      * "nan"  -> every `high_prob >= nan` is False, so the safety escalation is
                  switched off entirely and every case falls back to argmax.
      * "-1"   -> every case crosses the threshold; everything becomes acuity 1-2.
      * "1.5"  -> no case can ever cross it; the rule is dead code.
      * "abc"  -> raises a bare ValueError during module import, taking the whole
                  service down with an error that names neither the setting nor
                  the offending value.

    None of those would surface as an error in the UI: the app would just quietly
    triage differently. Rejecting them at startup with a message that names the
    variable is the only way this is diagnosable.
    """
    import math

    text = (raw or "").strip()
    if text.lower() == "artefact":
        return None            # defer to whatever the model artefact ships
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ValueError(
            f"MIMIC_HIGH_ACUITY_THRESHOLD must be a number in [0, 1] or the word "
            f"'artefact'; got {raw!r}."
        )
    if not math.isfinite(value):
        raise ValueError(
            "MIMIC_HIGH_ACUITY_THRESHOLD must be finite. A NaN or infinite "
            "threshold silently disables the high-acuity safety escalation "
            f"instead of failing; got {raw!r}."
        )
    if not (0.0 <= value <= 1.0):
        raise ValueError(
            "MIMIC_HIGH_ACUITY_THRESHOLD is a probability and must be in [0, 1]. "
            f"Got {value!r}, which would make the rule fire on every case or on "
            "none of them."
        )
    return value


class Settings(BaseModel):
    model_config = {"protected_namespaces": ()}

    project_root: Path = Path(__file__).resolve().parents[1]

    # Runtime-writable data root. On Azure App Service the app is deployed with
    # WEBSITE_RUN_FROM_PACKAGE, which mounts /home/site/wwwroot READ-ONLY, so the
    # app cannot create data/processed under the project and every workflow-state
    # write (accept/escalate/override/review) fails with a read-only-filesystem
    # OSError. Point ALTER_DATA_ROOT at a writable location to redirect all runtime
    # writes there. On Azure use /home/data (writable AND persisted across restarts);
    # /tmp works too but is ephemeral. Unset => original in-repo default (local dev).
    data_root: Path = (
        Path(os.environ["ALTER_DATA_ROOT"]).expanduser()
        if os.environ.get("ALTER_DATA_ROOT")
        else project_root / "data"
    )

    # Conventional local full-MIMIC path for schema utility scripts. Runtime
    # full-MIMIC serving uses mimic_full_ed_dir from MIMIC_FULL_ED_DIR instead.
    raw_ed_dir: Path = data_root / "raw" / "mimic-iv-ed" / "2.2" / "ed"

    # FULL credentialed MIMIC-IV-ED lives OUTSIDE this repo, on the credentialed
    # user's own environment, and is read from MIMIC_FULL_ED_DIR. It must NEVER be
    # copied into this repo, a build artifact, or a shared sandbox (PhysioNet DUA).
    # Unset by default => full-MIMIC features are disabled and fail closed.
    mimic_full_ed_dir: Path | None = (
        Path(os.environ["MIMIC_FULL_ED_DIR"]).expanduser()
        if os.environ.get("MIMIC_FULL_ED_DIR") else None
    )

    processed_dir: Path = data_root / "processed"

    # Active UHL synthetic dataset/model/report assets.  These immutable files
    # ship with the deployment; only the derived SQLite cache is written under
    # ALTER_DATA_ROOT so Azure package deployments remain read-only safe.
    uhl_data_path: Path = Path(
        os.environ.get(
            "UHL_DATA_PATH",
            str(project_root / "data" / "uhl_dataset_final.csv.gz"),
        )
    ).expanduser()
    uhl_model_path: Path = Path(
        os.environ.get(
            "UHL_MODEL_PATH",
            str(project_root / "artifacts" / "model" / "uhl_synthetic_acuity_selected.joblib"),
        )
    ).expanduser()
    uhl_report_dir: Path = Path(
        os.environ.get(
            "UHL_REPORT_DIR",
            str(project_root / "artifacts" / "reports" / "single_seed"),
        )
    ).expanduser()
    uhl_case_cache_path: Path = Path(
        os.environ.get(
            "UHL_CASE_CACHE_PATH",
            str(data_root / "cache" / "uhl_cases.sqlite3"),
        )
    ).expanduser()
    expected_dataset_sha256: str = os.environ.get(
        "UHL_DATASET_SHA256",
        "f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c",
    ).strip().lower()
    expected_model_sha256: str = os.environ.get(
        "UHL_MODEL_SHA256",
        "7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b",
    ).strip().lower()

    # Compatibility aliases used by the transplanted, narrowly scoped UHL
    # repository.  Keeping these as properties avoids replacing the 22.4
    # Settings model with the rewritten 23.x configuration system.
    @property
    def data_path(self) -> Path:
        return self.uhl_data_path

    @property
    def model_path(self) -> Path:
        return self.uhl_model_path

    @property
    def report_dir(self) -> Path:
        return self.uhl_report_dir

    @property
    def case_cache_path(self) -> Path:
        return self.uhl_case_cache_path

    # Operating point for the artefact's high_acuity_threshold decision rule.
    # The artefact ships 0.05, which flags ~93% of all cases as acuity 1-2
    # (specificity 0.117). 0.25 keeps 0.902 high-acuity recall while cutting the
    # urgent rate to ~59% and raising specificity to ~0.603, per the model's own
    # threshold-tuning report. Set MIMIC_HIGH_ACUITY_THRESHOLD to change it, or
    # to "artefact" to fall back to whatever the artefact ships. Every
    # prediction records which value was used and what the artefact's own value
    # was, so the operating point is never invisible.
    high_acuity_threshold_override: float | None = _parse_high_acuity_threshold(
        os.environ.get("MIMIC_HIGH_ACUITY_THRESHOLD", "0.25")
    )
    # Model assets are IMMUTABLE and ship inside the deployment; runtime state
    # is MUTABLE and must live on writable, persistent storage. Deriving both
    # from data_root forced one setting to serve both purposes: pointing
    # ALTER_DATA_ROOT at /home/data (which is what makes writes durable on Azure
    # App Service) moved the registry lookup to /home/data/models/registry.json,
    # which does not exist, so preflight and the SHA-256 integrity check failed.
    # Leaving it unset kept the registry but wrote operational state under the
    # deployed app tree, which is read-only under WEBSITE_RUN_FROM_PACKAGE.
    # Neither option was correct, so they are now separate roots: models default
    # to the bundled in-repo location regardless of ALTER_DATA_ROOT, and
    # ALTER_MODEL_ROOT overrides that only if assets are genuinely relocated.
    models_dir: Path = (
        Path(os.environ["ALTER_MODEL_ROOT"]).expanduser()
        if os.environ.get("ALTER_MODEL_ROOT")
        else project_root / "data" / "models"
    )
    model_registry_path: Path = models_dir / "registry.json"

    # The only live dataset.
    default_dataset: str = "uhl"

    # Kept for utility scripts that still ask for active_raw_dir.
    active_dataset: str = "uhl"

    # Whether the provisional MTS research ruleset is registered at startup.
    # Default-on so the demo shows working (provisional, review-required)
    # categories; set PROVISIONAL_MTS_MODE=off to keep the engine fully gated.
    provisional_mts_mode: bool = _provisional_mts_enabled()

    # CORS origins allowed to call this API. Defaults to local-dev-only
    # values; set the CORS_ALLOWED_ORIGINS environment variable (comma-
    # separated) for a real deployment. Never defaults to a wildcard.
    cors_allowed_origins: list[str] = _default_cors_origins()

    @property
    def active_raw_dir(self) -> Path:
        return self.uhl_data_path.parent


settings = Settings()
