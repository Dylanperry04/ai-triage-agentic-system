"""
Project-wide configuration.

The default active dataset is the public Kaggle KTAS emergency-service triage
CSV supplied for the current project phase. MIMIC-IV-ED paths are preserved so
that the dataset adapter can be swapped later after access is approved.
"""
import os
from pathlib import Path
from pydantic import BaseModel


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

    # Kaggle KTAS current phase
    raw_ktas_dir: Path = data_root / "raw" / "kaggle_ktas"
    raw_ktas_csv: Path = raw_ktas_dir / "data.csv"

    # MIMIC paths preserved for the later approved-data phase
    raw_ed_dir: Path = data_root / "raw" / "mimic-iv-ed" / "2.2" / "ed"
    raw_demo_dir: Path = data_root / "raw" / "mimic-iv-ed-demo" / "2.2" / "ed"

    processed_dir: Path = data_root / "processed"
    models_dir: Path = data_root / "models"
    model_registry_path: Path = models_dir / "registry.json"

    # The UI default dataset is MIMIC-IV-ED Demo (the frontend loads both KTAS
    # and MIMIC adapters directly and defaults its filter to MIMIC). This field
    # records that for any code/report that wants the canonical default.
    default_dataset: str = "mimic_demo"

    # LEGACY: kept only for the active_raw_dir property below, which is not used
    # by the frontend (it loads both datasets directly). Does NOT represent the
    # app's UI default -- see default_dataset above. "kaggle_ktas" | "demo" | "full"
    active_dataset: str = "kaggle_ktas"

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
        if self.active_dataset == "kaggle_ktas":
            return self.raw_ktas_dir
        return self.raw_demo_dir if self.active_dataset == "demo" else self.raw_ed_dir


settings = Settings()
