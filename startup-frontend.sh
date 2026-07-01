#!/usr/bin/env bash
# Start the Streamlit FRONTEND. It performs every protected action by calling the
# FastAPI backend over HTTP (FASTAPI_BASE_URL). In patient-data mode and local
# credentialed full-MIMIC research mode, FASTAPI_BASE_URL is REQUIRED unless an
# explicit local-dev in-process override is set.
set -e
export PYTHONUNBUFFERED=1
export PORT="${PORT:-8501}"
if [ "${LOCAL_CREDENTIALED_RESEARCH}" = "true" ]; then
  export FRONTEND_BIND_HOST="${FRONTEND_BIND_HOST:-127.0.0.1}"
else
  export FRONTEND_BIND_HOST="${FRONTEND_BIND_HOST:-0.0.0.0}"
fi
if [ "${PATIENT_DATA_MODE}" = "true" ] && [ -z "${FASTAPI_BASE_URL}" ] \
   && [ "${ALLOW_IN_PROCESS_BACKEND_FOR_PATIENT_DATA}" != "true" ]; then
  echo "FATAL: PATIENT_DATA_MODE=true requires FASTAPI_BASE_URL (backend URL)." >&2
  exit 1
fi
if [ "${LOCAL_CREDENTIALED_RESEARCH}" = "true" ] && [ -z "${FASTAPI_BASE_URL}" ] \
   && [ "${ALLOW_IN_PROCESS_BACKEND_FOR_LOCAL_CREDENTIALED_RESEARCH}" != "true" ]; then
  echo "FATAL: LOCAL_CREDENTIALED_RESEARCH=true requires FASTAPI_BASE_URL=http://127.0.0.1:8000 (or an explicit in-process dev override)." >&2
  exit 1
fi
case "${FRONTEND_BIND_HOST}" in
  127.0.0.1|localhost|::1) ;;
  *)
    if [ "${LOCAL_CREDENTIALED_RESEARCH}" = "true" ]; then
      echo "FATAL: LOCAL_CREDENTIALED_RESEARCH=true requires FRONTEND_BIND_HOST to be loopback." >&2
      exit 1
    fi
    ;;
esac
echo "Starting AI Triage frontend (Streamlit) on ${FRONTEND_BIND_HOST}:${PORT}; backend=${FASTAPI_BASE_URL:-<in-process dev>}"
exec streamlit run frontend/app.py \
    --server.port="${PORT}" \
    --server.address="${FRONTEND_BIND_HOST}" \
    --server.headless=true
