# AI Triage Agentic System 22.4 — UHL model with durable ACS notifications

> **Research prototype — not for clinical use.** Every assessment requires
> clinician review. The project does not implement an official or clinically
> validated Manchester Triage System ruleset.

This release deliberately keeps the working 22.4.0 application as the base. It
switches only the active dataset, model-serving contract, model evidence, and
the dataset-dependent parts of the React UI to the supplied UHL synthetic
release.

The broad 23.1.1 rewrite was not imported. Authentication, RBAC, audit,
redaction, workflow agents, deterministic safety layer, API contracts, React
application, and single-service Azure shape remain the 22.4 implementation.
See `UHL_SWAP_NOTES.md` for the detailed boundary.

## Active release assets

| Asset | Packaged path | SHA-256 |
|---|---|---|
| UHL synthetic cohort (777,176 rows) | `data/uhl_dataset_final.csv.gz` | `f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c` |
| Selected UHL CatBoost bundle | `artifacts/model/uhl_synthetic_acuity_selected.joblib` | `7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b` |
| Single-seed evidence | `artifacts/reports/single_seed/` | See its manifest/provenance files |
| Five-seed stability evidence | `artifacts/reports/five_seed/` | See `SHA256_MANIFEST.txt` |

The live model accepts exactly these 13 fields: age, month, hour, time bin,
season, presenting complaint, temperature, heart rate, respiratory rate,
oxygen saturation, systolic pressure, diastolic pressure, and pain. Derived
month/hour/time-bin/season values use the timestamp policy embedded in the
model bundle. Leakage and post-outcome fields are blocked.

The live recommendation starts with the modal class. A more urgent class
replaces it only when that individual class has probability at least 25%; when
more than one urgent class qualifies, the most urgent qualifying class is used.
The existing deterministic vital-sign override can still escalate further.

## Run locally

Python 3.11 is the deployment target.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The FastAPI service serves the built React UI and
the API from the same process.

The packaged defaults work without path configuration. These settings are only
needed when assets or writable data are mounted elsewhere:

```text
UHL_DATA_PATH=/path/to/uhl_dataset_final.csv.gz
UHL_MODEL_PATH=/path/to/uhl_synthetic_acuity_selected.joblib
UHL_REPORT_DIR=/path/to/single_seed
UHL_CASE_CACHE_PATH=/writable/path/uhl_cases.sqlite3
ALTER_DATA_ROOT=/writable/path
```

Do not change the default `UHL_DATASET_SHA256`, `UHL_MODEL_SHA256`, or
`UHL_FEATURE_SCHEMA_HASH` pins unless a separately reviewed UHL release is being
promoted. The application fails closed when these contracts do not match.

Azure OpenAI is optional. Set the `AZURE_OPENAI_*` values through Azure App
Settings or Key Vault; never commit them. Without those values, the core
workflow and ML model still run while the LLM explanation layer reports that it
is not configured.

## Durable notifications and ACS SMS

The notification bell now reads persistent, role-filtered records from the
backend. Escalation and 210-minute overdue-vitals alerts are committed before
any SMS work is published. Azure Communication Services SMS is a secondary
prompt; notification creation and acknowledgement continue to work if SMS,
Service Bus, Functions, or a carrier is unavailable.

Live SMS is disabled by default. The infrastructure template, worker, managed
identity roles, hard 100-attempt UTC-day cap, 90-day retention, privacy-safe
templates, deployment sequence, and rollback steps are documented in
`docs/ACS_SMS_OPERATIONS.md`. The data flow is in
`docs/NOTIFICATION_ARCHITECTURE.md`. Creating these Azure resources requires a
separate approval and the manual guarded workflow; pushing application code
does not enable or send SMS.

## Useful endpoints

- `/health` — service and active UHL asset state
- `/status/uhl` — UHL dataset/cache/model status
- `/runtime/status` — redacted runtime configuration
- `/cases` — bounded, paginated UHL case list
- `/model/performance` — UHL model evidence in the unchanged 22.4 UI contract
- `/system/meta` — release metadata
- `/notifications` — role-filtered durable in-app notifications
- `/notifications/system/health` — restricted, redacted notification health

The first case request builds a validated SQLite index from the compressed UHL
cohort. Later paging and case resolution use that index. Set
`ALTER_DATA_ROOT` or `UHL_CASE_CACHE_PATH` to a durable writable location on
Azure if the application directory is read-only or ephemeral.

## Verification

```powershell
python scripts\azure_preflight_check.py
python -m pytest tests -q
cd frontend-react
pnpm test
pnpm build
```

The deployment manifest contains only the active FastAPI/UHL/notification
runtime. Retired Streamlit and model-training tools are isolated in separate
legacy/training manifests and are not installed into the App Service package.

The test configuration explicitly skips 21 archived assertions whose sole
purpose was to require full MIMIC as the active deployment source. They are
kept for source history; UHL-specific replacement coverage lives in
`tests/test_uhl_22_4_swap.py`. This GitHub integration preserves the newer
pseudonym-aware UHL cache and resolver invalidation fixes already present on
`main`, rather than restoring the archive's deferred cache defect. The merged
Python 3.12 environment completed 1,106 backend checks (1,085 passed and 21
skipped, with no failures or XFAILs), while the React suite passed 47/47 and the
production Vite build transformed 2,076 modules. The supplied archive's
independent validation records remain documented in the release notes.

## Azure deployment

The current shape is one Linux App Service running `bash startup-backend.sh` on
Python 3.11. The checked-in workflow packages the built React UI, the pinned UHL
cohort, the selected UHL model, the reports, and Python runtime dependencies.
It verifies both asset hashes and the UHL serving contract before deploying,
enforces package-size limits, fails closed when the notification worker is
absent, and runs post-deployment web, notification, Function, build-identity,
and worker-heartbeat checks before creating a release tag.
See `infrastructure/azure_deploy.md` for the deployment checklist.

Historic MIMIC loaders, training utilities, fixtures, and tests remain in the
repository as inactive 22.4 compatibility/reference code. The case resolver,
health surface, model evidence endpoint, and prediction dispatcher expose UHL
as the only active source in this release.
