from fastapi import FastAPI

from app.api.health_routes import router as health_router
from app.api.triage_routes import router as triage_router
from app.api.audit_routes import router as audit_router
from app.api.review_routes import router as review_router
from app.api.governance_routes import router as governance_router
from app.api.explanation_routes import router as explanation_router


app = FastAPI(
    title="AI Triage Agentic System",
    version="0.1.0",
    description=(
        "Schema-first MIMIC-IV-ED Demo pipeline. "
        "Not for clinical use. No final Manchester rules configured."
    ),
)


@app.get("/")
def root():
    return {
        "status": "running",
        "message": "AI Triage MIMIC Pipeline API is running",
        "docs": "http://127.0.0.1:8000/docs",
        "clinical_use": "not_for_clinical_use",
        "automated_manchester_triage": "blocked",
    }


app.include_router(health_router)
app.include_router(triage_router)
app.include_router(audit_router)
app.include_router(review_router)
app.include_router(governance_router)
app.include_router(explanation_router)