# Deploys the Streamlit research UI (frontend/app.py).
#
# DEPLOYMENT ARCHITECTURE: Option A — Streamlit-only.
# The Streamlit UI in this project is self-contained: it imports the workflow
# (app.agents.orchestrator.run_workflow) and the engines in-process and makes
# NO HTTP calls to the FastAPI backend. So the deployable web app IS the
# Streamlit app. The FastAPI service (app.main:app) still exists in the repo as
# a programmatic API for future use, but it is NOT what this image runs.
# See infrastructure/azure_deploy.md for the full rationale and the other
# options (B: deploy both; C: refactor the UI to call the API).

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY app/ ./app/
COPY frontend/ ./frontend/
COPY ml_training/ ./ml_training/
COPY data/processed/ ./data/processed/

# Trained models (KTAS). For production Azure deployments, prefer loading from
# Azure Blob Storage at startup rather than baking into the image.
COPY data/models/ ./data/models/

# Public datasets the UI loads live via the adapters (KTAS CSV + MIMIC demo).
# These are PUBLIC datasets and are intentionally included so the demo runs.
# The full credentialed MIMIC dataset path (data/raw/mimic-iv-ed/) is NOT
# copied and is gitignored — patient data never goes in the image.
COPY data/raw/kaggle_ktas/ ./data/raw/kaggle_ktas/
COPY data/raw/mimic-iv-ed-demo/ ./data/raw/mimic-iv-ed-demo/

# Do NOT copy .env — secrets (Azure OpenAI keys, CORS origins) come from
# the host environment / Key Vault at runtime.

# Azure App Service / Container Apps provide $PORT; default to 8000 locally.
ENV PORT=8000
EXPOSE 8000

# PROVISIONAL_MTS_MODE defaults to on (provisional Manchester categories shown,
# clinician-review-required). Set PROVISIONAL_MTS_MODE=off to fully gate.

CMD ["sh", "-c", "streamlit run frontend/app.py --server.port=${PORT} --server.address=0.0.0.0 --server.headless=true"]
