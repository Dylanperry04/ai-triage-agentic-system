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
if [ "${PREWARM_UHL_CACHE_ON_STARTUP:-false}" = "true" ]; then
    echo "===== PREPARING UHL CASE CACHE BEFORE SERVER START ====="

    python - <<'PY'
import os
import shutil
import time
from pathlib import Path

from app.config import settings
from app.data_pipeline.uhl_repository import UhlCaseRepository

cache = Path(settings.case_cache_path)
seed = Path(
    os.environ.get(
        "UHL_CASE_CACHE_SEED_PATH",
        "/home/data/cache/uhl_cases.sqlite3",
    )
)

if str(cache).startswith("/home/"):
    raise SystemExit(
        "ERROR: UHL_CASE_CACHE_PATH must remain on local temporary storage."
    )

cache.parent.mkdir(parents=True, exist_ok=True)
seed.parent.mkdir(parents=True, exist_ok=True)

# Remove incomplete local builds/copies left by interrupted startup attempts.
for pattern in (
    f".{cache.name}.*.building",
    f".{cache.name}.*.seedcopy",
):
    for item in cache.parent.glob(pattern):
        try:
            item.unlink()
        except FileNotFoundError:
            pass

# Remove stale temporary seed files. Never delete the actual seed here.
for pattern in (
    f".{seed.name}.*.building",
    f".{seed.name}.*.tmp",
):
    for item in seed.parent.glob(pattern):
        try:
            item.unlink()
        except FileNotFoundError:
            pass

# If Azure recycled /tmp, restore the last verified cache from persistent /home.
if not cache.is_file() and seed.is_file():
    started = time.time()
    staged = cache.with_name(
        f".{cache.name}.{os.getpid()}.seedcopy"
    )

    print(f"Restoring persistent cache seed: {seed} -> {cache}")
    shutil.copyfile(seed, staged)
    os.replace(staged, cache)

    print(
        "Persistent cache seed copied in",
        round(time.time() - started, 2),
        "seconds",
    )

# This verifies the cache against the current dataset/pseudonym context.
# If the restored seed is stale or invalid, the repository rebuilds it.
started = time.time()

repo = UhlCaseRepository(settings)
repo.ensure_cache()
status = repo.status()

if status["source_rows"] != 777176:
    raise RuntimeError(
        f"Unexpected source row count: {status['source_rows']}"
    )

if status["model_scope_rows"] != 777174:
    raise RuntimeError(
        f"Unexpected model-scope row count: {status['model_scope_rows']}"
    )

if status["dataset_ready"] is not True:
    raise RuntimeError("UHL dataset/cache is not ready")

print("SOURCE_ROWS =", status["source_rows"])
print("MODEL_SCOPE_ROWS =", status["model_scope_rows"])
print("DATASET_READY =", status["dataset_ready"])
print(
    "CACHE_PREP_SECONDS =",
    round(time.time() - started, 2),
)

# The live database is closed at this point. Save a persistent immutable
# seed for the next Azure recycle. The application will NOT use this
# /home copy as its active SQLite database.
seed_tmp = seed.with_name(
    f".{seed.name}.{os.getpid()}.tmp"
)

shutil.copyfile(cache, seed_tmp)
os.replace(seed_tmp, seed)

print("LIVE_CACHE =", cache)
print("PERSISTENT_SEED =", seed)
print("UHL_STARTUP_CACHE=PASS")
PY

    echo "===== UHL CACHE READY ====="
fi

echo "Starting AI Triage backend on ${BACKEND_BIND_HOST}:${PORT}"

exec python -m uvicorn app.main:app \
    --host "${BACKEND_BIND_HOST}" \
    --port "${PORT}"
