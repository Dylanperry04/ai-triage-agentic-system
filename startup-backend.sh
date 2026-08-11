#!/usr/bin/env bash
set -e

export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"
export PYTHONPATH="/home/site/wwwroot/.python_packages/lib/site-packages:/home/site/wwwroot:${PYTHONPATH:-}"

if [ -n "${WEBSITE_SITE_NAME:-}" ] || [ -n "${WEBSITE_INSTANCE_ID:-}" ] || [ -n "${WEBSITES_PORT:-}" ]; then
    IS_AZURE_APP_SERVICE=true
else
    IS_AZURE_APP_SERVICE=false
fi

if [ -z "${BACKEND_BIND_HOST:-}" ]; then
    if [ "${LOCAL_CREDENTIALED_RESEARCH:-false}" = "true" ] && [ "${IS_AZURE_APP_SERVICE}" != "true" ]; then
        export BACKEND_BIND_HOST="127.0.0.1"
    else
        export BACKEND_BIND_HOST="0.0.0.0"
    fi
fi

echo "Python: $(command -v python)"
if [ "${LOCAL_CREDENTIALED_RESEARCH:-false}" = "true" ] && [ "${IS_AZURE_APP_SERVICE}" = "true" ]; then
    echo "ERROR: LOCAL_CREDENTIALED_RESEARCH=true is local-only and must not be used on Azure App Service."
    echo "For the packaged UHL synthetic research deployment, unset LOCAL_CREDENTIALED_RESEARCH."
    echo "For a secured tenant deployment, use PATIENT_DATA_MODE=true with App Service Authentication / Microsoft Entra."
    exit 78
fi
echo "Starting AI Triage backend on ${BACKEND_BIND_HOST}:${PORT}"

exec python -m uvicorn app.main:app \
    --host "${BACKEND_BIND_HOST}" \
    --port "${PORT}"
