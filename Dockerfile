# AI Triage Research System — v18 single-service image.
#
# ARCHITECTURE (v18): the FastAPI backend (app.main:app) is the sole service.
# It is the server-side enforcement boundary (identity, RBAC, audit, redaction,
# fail-closed), runs the ML workflow, AND serves the built React UI
# (frontend-react/dist) at "/". Deploy ONE container/App Service running
# startup-backend.sh. Same-origin UI -> no CORS needed for the built-in UI.
#
# The retired Streamlit source remains in the repository for historical tests,
# but it is not installed or exposed as a container service role.
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
COPY requirements.txt requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Azure deployment extras (Key Vault + durable audit clients). These are REQUIRED
# for the patient-data profile, so the build must fail if they cannot be
# installed (do not mask the failure). For a public-demo-only image, build with
# --build-arg SKIP_AZURE=1 or use a separate demo Dockerfile.
ARG SKIP_AZURE=0
COPY requirements-azure.txt ./
RUN if [ "$SKIP_AZURE" != "1" ]; then \
      pip install --no-cache-dir -r requirements-azure.txt; \
    fi

# Application code
COPY app/ ./app/
COPY frontend/ ./frontend/
COPY frontend-react/dist/ ./frontend-react/dist/
COPY ml_training/ ./ml_training/
COPY data/models/ ./data/models/
# Pinned UHL synthetic cohort, selected serving artifact, and aggregate reports.
COPY data/uhl_dataset_final.csv.gz ./data/uhl_dataset_final.csv.gz
COPY artifacts/ ./artifacts/
COPY startup-backend.sh ./
RUN mkdir -p ./data/processed/ ./data/cache/

# The packaged cohort is synthetic UHL research data. No patient dataset or
# retired MIMIC model artifact is included in this image.

# Do NOT copy .env — secrets come from the host environment / Key Vault.

# Run as a non-root user. The app writes only under ./data/processed (runtime
# state) — owned by the app user; everything else stays root-owned read-only.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app/data/processed /app/data/cache
USER appuser

ENV PORT=8000
EXPOSE 8000

# One image, one service: FastAPI serves the built React application.
CMD ["sh", "startup-backend.sh"]
