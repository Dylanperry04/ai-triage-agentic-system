"""
Project-wide configuration.

The live prediction/serving dataset is full MIMIC-IV-ED v2.2, read from
MIMIC_FULL_ED_DIR on a credentialed/approved environment. Retired non-full
dataset paths are not runtime settings.
"""
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


class Settings(BaseModel):
    model_config = {"protected_namespaces": ()}

    project_root: Path = Path(__file__).resolve().parents[1]
    data_root: Path = project_root / "data"

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
    models_dir: Path = data_root / "models"
    model_registry_path: Path = models_dir / "registry.json"

    # The only live dataset.
    default_dataset: str = "mimic_full"

    # Kept for utility scripts that still ask for active_raw_dir.
    active_dataset: str = "mimic_full"

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
        return self.mimic_full_ed_dir or self.raw_ed_dir


settings = Settings()
