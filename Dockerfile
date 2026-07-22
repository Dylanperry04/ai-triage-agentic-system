# AI Triage Research System — v18 single-service image.
#
# ARCHITECTURE (v18): the FastAPI backend (app.main:app) is the sole service.
# It is the server-side enforcement boundary (identity, RBAC, audit, redaction,
# fail-closed), runs the ML workflow, AND serves the built React UI
# (frontend-react/dist) at "/". Deploy ONE container/App Service running
# startup-backend.sh. Same-origin UI -> no CORS needed for the built-in UI.
#
# LEGACY: the retired Streamlit frontend (frontend/app.py, startup-frontend.sh,
# SERVICE_ROLE=frontend) is kept in the image for reference and can still be
# run as the old two-service shape if ever needed. It is not the deployment
# target. See infrastructure/azure_deploy.md ("Single-service React UI").
#
# Patient-data mode (PATIENT_DATA_MODE=true) additionally requires: Entra auth
# via a trusted proxy, Key Vault secrets, a durable audit sink, and a
# non-wildcard CORS allow-list. The full credentialed MIMIC dataset is NEVER
# copied into this image.

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
# AutoGen explanation/chat runtime deps. These routes are part of the app demo
# surface, so the container image must match the local app venv rather than
# silently degrading only after deployment.
COPY requirements-autogen.txt .
RUN pip install --no-cache-dir -r requirements-autogen.txt

# Application code
COPY app/ ./app/
COPY frontend/ ./frontend/
COPY frontend-react/dist/ ./frontend-react/dist/
COPY ml_training/ ./ml_training/
COPY data/models/ ./data/models/
# Synthetic supervisor-demo cohort + bundled training artefacts (model card,
# curves, confusion matrix, the pinned selected model). Required by the
# documented Azure supervisor-demo configuration (MIMIC_FULL_MODEL_PATH /
# MIMIC_FULL_MODEL_REPORT_DIR point inside the image).
COPY data/demo/ ./data/demo/
COPY model_outputs/last_training/ ./model_outputs/last_training/
COPY startup-backend.sh startup-frontend.sh ./
RUN mkdir -p ./data/processed/

# NO datasets are baked into the image. The only prediction dataset is full
# MIMIC-IV-ED (credentialed), read at runtime from MIMIC_FULL_ED_DIR on an
# approved environment — never copied into the image. The retired demo/KTAS
# datasets are not part of the live system and are not included.

# Do NOT copy .env — secrets come from the host environment / Key Vault.

# Run as a non-root user. The app writes only under ./data/processed (runtime
# state) — owned by the app user; everything else stays root-owned read-only.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data/processed
USER appuser

ENV PORT=8000
ENV SERVICE_ROLE=backend
EXPOSE 8000

# Dispatch on SERVICE_ROLE. Default is the backend (the enforcement boundary).
CMD ["sh", "-c", "if [ \"$SERVICE_ROLE\" = \"frontend\" ]; then exec sh startup-frontend.sh; else exec sh startup-backend.sh; fi"]
