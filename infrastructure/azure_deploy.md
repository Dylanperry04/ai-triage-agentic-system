# Azure deployment — reviewed 100-case release, both existing apps

> **Not for clinical use.** This is a synthetic-data research prototype. A
> clinician must review every output. Hospital production controls and clinical
> validation are outside this repository.

## Deployment shape

The authoritative `.github/workflows/deploy-azure.yml` deploys the same source
release to two existing Linux Python 3.11 App Services. FastAPI serves the built
React UI from `frontend-react/dist`.

| App | Resource group | Subscription |
|---|---|---|
| ai-triage-agentic-system | Ai-triaging | 3e06b774-e965-4c81-aa02-897ec6647dcd |
| EDTRIAGE | Triage | 4b5b261f-4692-4c6f-afd6-18874e1a0487 |

Each isolated matrix job tests and packages the pinned release. It authenticates
with its own GitHub OIDC identity and checks the complete Azure resource ID,
including the subscription, before deployment. One app failing does not cancel
the other. Do not reactivate the archived competing deployment workflow.

- Startup command: `bash startup-backend.sh`
- Health path: `/health`
- UHL status path: `/status/uhl`
- Runtime metadata: `/runtime/status`
- Deep deployment readiness: `/ready` (HTTP 503 until assets and writable state are usable)
- Model evidence: `/model/performance`

The package contains 100 unchanged rows selected from the source UHL CSV,
the selected model bundle, and aggregate training reports. Exactly 60 cases
match at least one provisional vital safety check; final acuity counts are
1/59/27/8/5. This curated demonstration is not an unbiased model evaluation.
The full training CSV remains in Git and is excluded from Azure packages.

## Required packaged files

```text
data/uhl_demo_100.json
artifacts/model/uhl_synthetic_acuity_selected.joblib
artifacts/reports/single_seed/uhl_synthetic_training_provenance.json
artifacts/reports/single_seed/uhl_synthetic_feature_schema.json
frontend-react/dist/index.html
startup-backend.sh
```

The GitHub Actions workflow verifies the serving cohort/model SHA-256 values and
loads the model through the exact serving-contract validator before deployment.
It also verifies that the final ZIP still contains both UHL assets.

## App settings

Packaged assets are the defaults, so no UHL path setting is required for the
standard ZIP deployment. The workflow aligns both apps to Python 3.11, sets the
startup command, disables Oryx rebuilding, and selects `UHL_CASE_COHORT=demo_100`.
It sets `WEBSITE_RUN_FROM_PACKAGE=0` for extracted ZIP deployment: App Service
does not support run-from-package for Python. Linux dependencies are installed
into `.python_packages/lib/site-packages` and verified before ZIP creation.

| Setting | Purpose |
|---|---|
| `ALTER_DATA_ROOT` | Durable writable root for workflow state, access audit, monthly exports and notifications |
| `UHL_CASE_COHORT` | `demo_100` by default; `full` is an explicit offline/research option requiring the full CSV |
| `UHL_DEMO_DATA_PATH` | Optional override of the reviewed 100-case JSON |
| `UHL_DATA_PATH` | Full-mode CSV path; not read by the default demo repository |
| `UHL_MODEL_PATH` | Override the packaged selected UHL model |
| `UHL_REPORT_DIR` | Override the packaged single-seed evidence directory |
| `UHL_CASE_CACHE_PATH` | Explicit writable SQLite cache path |
| `UHL_CASE_CACHE_SEED_PATH` | Persistent verified seed copied to the active local cache on Azure recycle |
| `PREWARM_UHL_CACHE_ON_STARTUP` | Workflow sets `false`; only relevant when full mode is explicitly selected |
| `SERVE_REACT_UI` | Defaults to `true`; set `false` for API-only operation |

Keep the default integrity pins unless a new reviewed release is being
promoted:

```text
UHL_DATASET_SHA256=f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c
UHL_MODEL_SHA256=7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b
UHL_FEATURE_SCHEMA_HASH=fd3d1365fe744d5eb75a83b8cfb1ebf9b84695a405c802b633bd2bb78f89debd
```

The serving cohort SHA-256 is
`1fb5aad5fec098da3335065f7932da6e832104988b579d4c991209e5e561f691`.
`scripts/verify_demo_source.py` verifies every selected row against the full CSV.

The startup script supplies these Azure-safe defaults (explicit App Settings
still take precedence):

```text
ALTER_DATA_ROOT=/home/data
UHL_CASE_CACHE_PATH=/tmp/alter-uhl-cache/uhl_cases.sqlite3
UHL_CASE_CACHE_SEED_PATH=/home/data/cache/uhl_cases.sqlite3
UHL_CASE_COHORT=demo_100
ENABLE_OVERDUE_VITALS_SWEEPER=true
ENABLE_MONTHLY_RETRAINING_EXPORTS=true
```

The default 100-case repository loads the small verified JSON into memory. It
does not read the full CSV or build/restore a SQLite patient cache on restart.
Existing workflow and audit state remains under the durable data root. Keep
each demo at one instance and one Uvicorn worker because clinical transition
serialization is process-local.

## Deployment authentication and notification safety

The university app uses the existing `oidc-msi-9ee5` identity. EDTRIAGE uses
`EDTRIAGE-github-deploy`. Each trusts only
`repo:Dylanperry04/ai-triage-agentic-system:ref:refs/heads/main`, with Website
Contributor on its own app and Reader on its resource group for worker
discovery. The workflow matrix names each target's separate GitHub secrets;
these store identifiers, not deployment passwords. Do not print application
settings or export publish profiles into source control.

No notification resources are created by app deployment. If a tagged worker
exists, deploy matching worker code before the web app and verify its build
and heartbeat. If none exists, explicitly disable and verify SMS publication
and submission. Manual rollback also disables SMS before packaging begins.

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
pnpm install --frozen-lockfile
pnpm test
pnpm build
```

Expected API checks after deployment:

```bash
curl -fsS https://<app>.azurewebsites.net/health
curl -fsS https://<app>.azurewebsites.net/status/uhl
curl -fsS https://<app>.azurewebsites.net/ready
curl -fsS https://<app>.azurewebsites.net/model/performance
```

`/ready` must report `ready=true`, a loadable pinned model, 100 source rows,
100 model-scope rows and writable runtime storage. The smoke check also verifies
the exact cohort hash, 60 matched cases, final category counts, deployment build
identity and the ITD escalation/staff audit question.

## Rollback

The workflow creates one `release-*` tag after BOTH deployments pass. To roll
back, run the workflow manually with `git_ref` set to the prior release tag or
commit SHA. The source, model, reports, and UI are deployed as one versioned
unit; do not mix a UHL model from one release with data or schema pins from
another.
