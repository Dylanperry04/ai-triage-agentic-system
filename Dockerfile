# AI Triage Research System — two-service image.
#
# FINAL ARCHITECTURE: Streamlit frontend + protected FastAPI backend.
#   * Streamlit (frontend/app.py) is the FRONTEND ONLY. It performs every
#     protected action by calling the FastAPI backend over HTTP via
#     frontend/api_client.py (FASTAPI_BASE_URL).
#   * FastAPI (app.main:app) is the SOLE server-side enforcement boundary
#     (identity, RBAC, audit, redaction, fail-closed) and runs the ML workflow.
#
# This one image can run as EITHER service, selected by SERVICE_ROLE:
#   SERVICE_ROLE=backend   -> uvicorn app.main:app    (the protected API)
#   SERVICE_ROLE=frontend  -> streamlit frontend/app.py (the UI)
# Deploy it twice (two Azure Container Apps / App Services in one private
# environment): one backend, one frontend, with the frontend's FASTAPI_BASE_URL
# pointing at the backend's private URL. See infrastructure/azure_deploy.md.
#
# Patient-data mode (PATIENT_DATA_MODE=true) additionally requires: Entra auth
# via a trusted proxy, FASTAPI_BASE_URL on the frontend (no in-process fallback),
# Key Vault secrets, a durable audit sink, and a non-wildcard CORS allow-list.
# The full credentialed MIMIC dataset is NEVER copied into this image.

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# App runtime deps only. (requirements-ml.txt / requirements-azure.txt are layered
# in the environments that need them: ML training on the credentialed box, and
# Key Vault/durable-audit clients in the Azure deployment.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Azure deployment extras (Key Vault + durable audit clients). These are REQUIRED
# for the patient-data profile, so the build must fail if they cannot be
# installed (do not mask the failure). For a public-demo-only image, build with
# --build-arg SKIP_AZURE=1 or use a separate demo Dockerfile.
COPY requirements-azure.txt .
RUN pip install --no-cache-dir -r requirements-azure.txt

# Application code
COPY app/ ./app/
COPY frontend/ ./frontend/
COPY ml_training/ ./ml_training/
COPY data/models/ ./data/models/
COPY startup-backend.sh startup-frontend.sh ./
RUN mkdir -p ./data/processed/

# NO datasets are baked into the image. The only prediction dataset is full
# MIMIC-IV-ED (credentialed), read at runtime from MIMIC_FULL_ED_DIR on an
# approved environment — never copied into the image. The retired demo/KTAS
# datasets are not part of the live system and are not included.

# Do NOT copy .env — secrets come from the host environment / Key Vault.

ENV PORT=8000
ENV SERVICE_ROLE=backend
EXPOSE 8000

# Dispatch on SERVICE_ROLE. Default is the backend (the enforcement boundary).
CMD ["sh", "-c", "if [ \"$SERVICE_ROLE\" = \"frontend\" ]; then exec sh startup-frontend.sh; else exec sh startup-backend.sh; fi"]
