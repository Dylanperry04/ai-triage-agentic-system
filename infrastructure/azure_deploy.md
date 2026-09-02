# Azure deployment — 22.4 UHL data/model-swap release

> **Not for clinical use.** This is a synthetic-data research prototype. A
> clinician must review every output. Hospital production controls and clinical
> validation are outside this repository.

## Deployment shape

Deploy one Linux Python 3.11 App Service. FastAPI is the enforcement boundary,
runs the unchanged 22.4 agent workflow, and serves the built React UI from
`frontend-react/dist`.

- Startup command: `bash startup-backend.sh`
- Health path: `/health`
- UHL status path: `/status/uhl`
- Runtime metadata: `/runtime/status`
- Deep deployment readiness: `/ready` (HTTP 503 until assets, cache and writable state are usable)
- Model evidence: `/model/performance`

The package contains the synthetic UHL cohort, selected model bundle, and
aggregate training reports. It contains no patient dataset and no active MIMIC
model.

## Required packaged files

```text
data/uhl_dataset_final.csv.gz
artifacts/model/uhl_synthetic_acuity_selected.joblib
artifacts/reports/single_seed/uhl_synthetic_training_provenance.json
artifacts/reports/single_seed/uhl_synthetic_feature_schema.json
frontend-react/dist/index.html
startup-backend.sh
```

The GitHub Actions workflow verifies the UHL dataset/model SHA-256 values and
loads the model through the exact serving-contract validator before deployment.
It also verifies that the final ZIP still contains both UHL assets.

## App settings

Packaged assets are the defaults, so no UHL path setting is required for the
standard ZIP deployment. Use the following only when mounting assets or a
durable cache elsewhere:

| Setting | Purpose |
|---|---|
| `ALTER_DATA_ROOT` | Durable writable root for workflow state, access audit, monthly exports and notifications |
| `UHL_DATA_PATH` | Override the packaged compressed UHL cohort |
| `UHL_MODEL_PATH` | Override the packaged selected UHL model |
| `UHL_REPORT_DIR` | Override the packaged single-seed evidence directory |
| `UHL_CASE_CACHE_PATH` | Explicit writable SQLite cache path |
| `UHL_CASE_CACHE_SEED_PATH` | Persistent verified seed copied to the active local cache on Azure recycle |
| `PREWARM_UHL_CACHE_ON_STARTUP` | Validate/restore/build the case cache before serving; defaults to `true` on Azure |
| `SERVE_REACT_UI` | Defaults to `true`; set `false` for API-only operation |

Keep the default integrity pins unless a new reviewed release is being
promoted:

```text
UHL_DATASET_SHA256=f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c
UHL_MODEL_SHA256=7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b
UHL_FEATURE_SCHEMA_HASH=fd3d1365fe744d5eb75a83b8cfb1ebf9b84695a405c802b633bd2bb78f89debd
```

The startup script supplies these Azure-safe defaults (explicit App Settings
still take precedence):

```text
ALTER_DATA_ROOT=/home/data
UHL_CASE_CACHE_PATH=/tmp/alter-uhl-cache/uhl_cases.sqlite3
UHL_CASE_CACHE_SEED_PATH=/home/data/cache/uhl_cases.sqlite3
PREWARM_UHL_CACHE_ON_STARTUP=true
ENABLE_OVERDUE_VITALS_SWEEPER=true
ENABLE_MONTHLY_RETRAINING_EXPORTS=true
```

The active SQLite index stays on fast instance-local `/tmp`; a verified copy in
durable `/home` avoids a full rebuild after normal App Service recycles. Uvicorn
does not start until this cache passes the pinned row-count checks. Keep this
demo at one App Service instance and one Uvicorn worker because its clinical
transition serialization is process-local.

## Optional LLM layer

Configure `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, API version, and
deployment/model values in App Settings or Key Vault. Do not put credentials in
the repository or deployment ZIP. The core safety workflow and UHL prediction
remain available when the LLM layer is not configured.

## Pre-deployment checks

Run from the repository root:

```bash
python scripts/azure_preflight_check.py
python -m pytest tests -q
```

For the React application:

```bash
cd frontend-react
npm ci
npm test -- --run
npm run build
```

Expected API checks after deployment:

```bash
curl -fsS https://<app>.azurewebsites.net/health
curl -fsS https://<app>.azurewebsites.net/status/uhl
curl -fsS https://<app>.azurewebsites.net/ready
curl -fsS https://<app>.azurewebsites.net/model/performance
```

`/ready` must report `ready=true`, a loadable pinned model, 777176 source rows,
777174 model-scope rows and writable runtime storage. The deployment workflow
also exercises the exact ITD escalation/staff audit question.

## Rollback

The workflow creates `release-*` tags after successful deployments. To roll
back, run the workflow manually with `git_ref` set to the prior release tag or
commit SHA. The source, model, reports, and UI are deployed as one versioned
unit; do not mix a UHL model from one release with data or schema pins from
another.
