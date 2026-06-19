#!/usr/bin/env bash
# Startup for the Streamlit research UI (Option A — Streamlit-only deployment).
# The UI is self-contained and does not call the FastAPI backend; the deployable
# web app is the Streamlit app. See infrastructure/azure_deploy.md.
set -e

export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"

echo "Starting KTAS/MIMIC research UI (Streamlit) on port ${PORT}"
exec streamlit run frontend/app.py \
    --server.port="${PORT}" \
    --server.address=0.0.0.0 \
    --server.headless=true
