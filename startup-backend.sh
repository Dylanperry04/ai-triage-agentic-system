#!/usr/bin/env bash
set -e

export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"
export PYTHONPATH="/home/site/wwwroot/.python_packages/lib/site-packages:/home/site/wwwroot:${PYTHONPATH:-}"

if [ "${LOCAL_CREDENTIALED_RESEARCH:-false}" = "true" ]; then
    export BACKEND_BIND_HOST="${BACKEND_BIND_HOST:-127.0.0.1}"
else
    export BACKEND_BIND_HOST="${BACKEND_BIND_HOST:-0.0.0.0}"
fi

echo "Python: $(command -v python)"
echo "Starting AI Triage backend on ${BACKEND_BIND_HOST}:${PORT}"

exec python -m uvicorn app.main:app \
    --host "${BACKEND_BIND_HOST}" \
    --port "${PORT}"
