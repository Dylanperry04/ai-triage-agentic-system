"""
AI Triage Agentic System — FastAPI Backend

NOT FOR CLINICAL USE. Research prototype only.

DEPLOYMENT STATUS: This FastAPI service is NOT the deployed application. The
deployed app (GitHub -> Azure App Service) is the Streamlit UI
(frontend/app.py); the Dockerfile launches Streamlit, not this service. This
API is retained for FUTURE programmatic use and is not exposed on Azure. As a
consequence, some routes here are not yet fully dataset-aware for the MIMIC
acuity model (they predate it); since the service is not deployed, that is a
known, accepted limitation rather than a live bug. See
infrastructure/azure_deploy.md (Option A).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.rules.provisional_mts_ruleset import register_provisional_ruleset
from app.api.health_routes import router as health_router
from app.api.triage_routes import router as triage_router
from app.api.review_routes import router as review_router
from app.api.governance_routes import router as governance_router
from app.api.explanation_routes import router as explanation_router
from app.api.chat_routes import router as chat_router
from app.api.followup_routes import router as followup_router

app = FastAPI(
    title="AI Triage Agentic System",
    version="2.0.0-ktas",
    description=(
        "Multi-agent AI triage research workflow using public Kaggle KTAS data. "
        "NOT FOR CLINICAL USE. KTAS is not Manchester; no Manchester mapping configured."
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

@app.get("/")
def root():
    from app.agents.autogen_team import load_azure_config
    from app.rules.manchester_engine import get_approved_ruleset

    ruleset = get_approved_ruleset()
    provisional_active = bool(ruleset) and ruleset.get(
        "validation_status"
    ) != "CLINICALLY_APPROVED"

    return {
        "status": "running",
        "version": "2.0.0-ktas",
        "default_dataset": "MIMIC-IV-ED-Demo-v2.2",
        "datasets_available": ["MIMIC-IV-ED-Demo-v2.2", "Kaggle-KTAS"],
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
        "ktas_to_manchester_mapping": "NOT_IMPLEMENTED",
        "human_review_required": True,
        "chat_agent_orchestration_framework": "autogen-agentchat",
        "chat_agent_status": "configured" if load_azure_config() else "not_configured",
        "docs": "/docs",
    }

app.include_router(health_router)
app.include_router(triage_router)
app.include_router(review_router)
app.include_router(governance_router)
app.include_router(explanation_router)
app.include_router(chat_router)
app.include_router(followup_router)
