#!/usr/bin/env bash
# Start the protected FastAPI backend (app.main:app) — the sole server-side
# enforcement boundary (identity, RBAC, audit, redaction, fail-closed) and the
# ML workflow runner. In patient-data mode the app refuses to start unless the
# secure configuration (Entra/trusted proxy, Key Vault, durable audit, non-
# wildcard CORS) is present (see app/main.py startup guard).
set -e
export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"
if [ "${LOCAL_CREDENTIALED_RESEARCH}" = "true" ]; then
  export BACKEND_BIND_HOST="${BACKEND_BIND_HOST:-127.0.0.1}"
else
  export BACKEND_BIND_HOST="${BACKEND_BIND_HOST:-0.0.0.0}"
fi
echo "Starting AI Triage backend (FastAPI) on ${BACKEND_BIND_HOST}:${PORT}"
exec uvicorn app.main:app --host "${BACKEND_BIND_HOST}" --port "${PORT}"
