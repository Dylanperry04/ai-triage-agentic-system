"""
AI Triage Research System — FastAPI Backend

NOT FOR CLINICAL USE. Research prototype only. Clinician review required for every
output. The only prediction/training dataset is full MIMIC-IV-ED (credentialed),
used only on an approved environment; raw identifiers never appear in the API.

ARCHITECTURE: this FastAPI service is the SERVER-SIDE ENFORCEMENT BOUNDARY in a
two-service deployment (Streamlit frontend + FastAPI backend). The Streamlit
frontend (frontend/app.py) performs every protected action by calling this
service over HTTP (FASTAPI_BASE_URL); the same image runs as either service via
SERVICE_ROLE. Every protected route enforces authentication + app-level RBAC via
app/api/auth_dependencies.py (reads the Entra-injected X-MS-CLIENT-PRINCIPAL,
fails closed in patient-data mode, audits every decision). On startup the service
refuses to run in patient-data mode unless the security preconditions hold
(AUTH_REQUIRED, non-demo AUTH_PROVIDER, TRUSTED_AUTH_PROXY, keyvault secrets,
durable audit). See docs/SECURITY_ARCHITECTURE.md and
docs/DEPLOYMENT_SECURITY_CHECKLIST.md.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.version import APP_VERSION, PACKAGE_CHECKPOINT
from app.rules.provisional_mts_ruleset import register_provisional_ruleset
from app.api.health_routes import router as health_router
from app.api.governance_routes import router as governance_router

app = FastAPI(
    title="AI Triage Research System",
    version=APP_VERSION,
    description=(
        "Research decision-support API for ED acuity prediction. The ML model "
        "predicts acuity; the LLM layer only explains. NOT FOR CLINICAL USE — "
        "clinician review is required for every output. The only prediction/"
        "training dataset is MIMIC-IV-ED Full (credentialed), used only on an "
        "approved environment and read from MIMIC_FULL_ED_DIR; the trained model "
        "is read from MIMIC_FULL_MODEL_PATH. Synthetic MIMIC-shaped fixtures are "
        "used only for automated tests and the Azure supervisor demo; they are "
        "never a clinical or patient-data source. The external API uses a "
        "pseudonymous case_uid; raw identifiers never appear in the public API."
    ),
)

# CORS origins come from settings.cors_allowed_origins, which defaults to
# local-development-only values (localhost:8501) and must be set via the
# CORS_ALLOWED_ORIGINS environment variable for a real deployment -- this
# project never defaults to a wildcard. See infrastructure/azure_deploy.md.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the provisional MTS research ruleset at startup (default-on; set
# PROVISIONAL_MTS_MODE=off to disable). This makes the engine assign
# provisional, clinician-review-required Manchester categories instead of
# staying gated. The categories are NOT the official Manchester Triage System
# and are NOT clinically approved -- see app/rules/provisional_mts_ruleset.py.
if settings.provisional_mts_mode:
    register_provisional_ruleset()


# ── Startup security guard (fail closed on unsafe patient-data config) ───────
# If the service is started in patient-data mode, the security preconditions must
# hold; otherwise we refuse to start rather than serve patient data unsafely.
def _enforce_patient_data_security_invariants() -> None:
    """Refuse to start in patient-data mode with an unsafe configuration.

    Startup checks are allowed to run readiness probes that have side effects
    (for example, a harmless durable-audit write/read probe). The status endpoint
    reads the cached probe result instead of performing those writes on GET.
    """
    from app.security.security_status import unsafe_combinations
    problems = unsafe_combinations()
    # CORS wildcard is unsafe in any mode; the patient-data ones only fire in
    # patient-data mode (encoded in unsafe_combinations()).
    if problems:
        raise RuntimeError(
            "Refusing to start the API with unsafe security config:\n  - "
            + "\n  - ".join(problems)
            + "\nSee docs/DEPLOYMENT_SECURITY_CHECKLIST.md."
        )


def _unused_old_guard() -> None:
    return


def _enforce_local_research_invariants() -> None:
    """In the LOCAL_CREDENTIALED_RESEARCH profile, refuse to start unless the
    backend is bound to a loopback interface (BACKEND_BIND_HOST). This profile
    handles credentialed data on an approved local machine and must never be
    exposed on a network interface."""
    from app.security.identity import assert_local_research_bind_is_loopback
    assert_local_research_bind_is_loopback()


_enforce_patient_data_security_invariants()
_enforce_local_research_invariants()

@app.get("/")
def root():
    from app.agents.autogen_team import load_azure_config
    from app.rules.manchester_engine import get_approved_ruleset

    ruleset = get_approved_ruleset()
    provisional_active = bool(ruleset) and ruleset.get(
        "validation_status"
    ) != "CLINICALLY_APPROVED"

    from app.data_pipeline.mimic_full_loader import full_mimic_status
    _fm = full_mimic_status()

    return {
        "status": "running",
        "version": APP_VERSION,
        "package_checkpoint": PACKAGE_CHECKPOINT,
        "default_dataset": "MIMIC-IV-ED-Full-v2.2",
        "datasets_available": ["MIMIC-IV-ED-Full-v2.2"],
        "mimic_full_dir_configured": _fm["mimic_full_dir_configured"],
        "mimic_full_model_configured": _fm["mimic_full_model_configured"],
        "prediction_model_source": "MIMIC_FULL_MODEL_PATH",
        "patient_data_ready": False,
        "patient_data_ready_requires": [
            "PATIENT_DATA_MODE=true",
            "real Entra/SSO and trusted auth proxy",
            "Key Vault secret read and Managed Identity",
            "durable audit write/read",
            "private ingress/network controls",
            "non-wildcard CORS",
            "model artefact hash verification",
            "governance, DPIA, and security approval evidence",
        ],
        "clinical_use": "NOT_FOR_CLINICAL_USE",
        "official_manchester_triage": "NOT_IMPLEMENTED",
        "provisional_mts_research_ruleset": "ENABLED" if provisional_active else "DISABLED",
        "official_mts_ruleset": False,
        "clinically_approved_ruleset": False,
        "rules_status": (
            "PROVISIONAL_MTS_RESEARCH_RULESET_ACTIVE"
            if provisional_active
            else "NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED"
        ),
        "human_review_required": True,
        "chat_agent_orchestration_framework": "autogen-agentchat",
        "chat_agent_status": "configured" if load_azure_config() else "not_configured",
        "docs": "/docs",
    }

app.include_router(health_router)
# Canonical case_uid-keyed API (the public surface) + status endpoints.
from app.api.case_routes import router as case_router
from app.api.status_routes import router as status_router
from app.api.session_routes import router as session_router
app.include_router(case_router)
app.include_router(status_router)
app.include_router(session_router)
app.include_router(governance_router)

# ── Legacy raw-ID routers (triage/review/explanation/chat/followup) ──────────
# These expose stay_id in URLs and are NOT part of the final public API. They are
# registered ONLY when explicitly enabled for backward-compatibility AND never in
# patient-data mode. Default: disabled. Streamlit protected actions never use them.
import os as _os
_allow_legacy = _os.environ.get("ALLOW_LEGACY_RAW_ID_ROUTES", "false").lower() == "true"
_patient_mode = _os.environ.get("PATIENT_DATA_MODE", "").lower() == "true"
_local_research_mode = (
    _os.environ.get("LOCAL_CREDENTIALED_RESEARCH", "").lower() == "true"
    and not _patient_mode
)
if _allow_legacy and not _patient_mode and not _local_research_mode:
    from app.api.triage_routes import router as triage_router
    from app.api.review_routes import router as review_router
    from app.api.explanation_routes import router as explanation_router
    from app.api.chat_routes import router as chat_router
    from app.api.followup_routes import router as followup_router

    app.include_router(triage_router)
    app.include_router(review_router)
    app.include_router(explanation_router)
    app.include_router(chat_router)
    app.include_router(followup_router)
