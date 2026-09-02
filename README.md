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

## Clinical workflow and roles

The active ED workflow is `ED Nurse -> Triage Nurse + AI -> ED Doctor when
escalated`:

- **ED Nurse** records and repeats observations and supplies requested
  information. This role cannot accept/override acuity or resolve escalation.
- **Triage Nurse** runs/reviews the assessment, accepts or overrides the AI
  recommendation, requests information, and escalates to the ED Doctor.
- **ED Doctor** is the final escalation authority and can request information,
  confirm/change final acuity, and resolve the escalation. Routine vitals are
  not part of this role.
- **ITD/security administrator**, **Researcher**, and **Governance Auditor**
  retain their system, analysis, and read-only oversight responsibilities.

`clinical_supervisor` is not assignable to new users or actions. Historical
audit rows that contain that retired role remain readable as immutable history.

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

## Patient trends and monthly retraining data

Analytics derives per-case vital and advisory model-acuity trends from the
append-only workflow audit evidence. The direction label describes movement in
recorded observations/model estimates; it is not a diagnosis or a verified
patient outcome.

On startup and then at the configured interval, deployed profiles check the
previous completed Europe/Dublin calendar month. They prepare a separate
`monthly_retraining_YYYY_MM.csv`, write a checksum-bearing manifest, and send
an in-app ITD notification. Accepted recommendations use the accepted model
acuity as the label; overrides and resolved escalations use the final clinician
acuity. Requests for information, unresolved escalations, missing exact-run
links, incomplete UHL inputs, and invalid provenance are excluded and counted.
An unchanged check is idempotent. If an excluded assessment later receives a
final label, the month is atomically revised and ITD is notified of the new
revision, so late resolutions are not lost. In the non-patient Azure demo, a
clearly labelled current-month preview makes same-day walkthrough evidence
downloadable without changing or persisting the official calendar-month export.

The ITD Console lists reporting month, generation date, eligible/accepted/
overridden/excluded counts and download status. Downloads are checksum-verified.
This boundary ends at **prepare -> notify -> download**: the application never
logs into NVIDIA infrastructure, submits Slurm, retrains, replaces, or promotes
a model. Set `MONTHLY_RETRAINING_EXPORT_DIR` to durable writable storage in
Azure; the default is under `ALTER_DATA_ROOT`. Durable source reads are bounded
from the exact Dublin month start and fail closed if the configured read cap is
reached, so the service does not silently finalise a partial CSV.

## ITD system and audit assistant

The ITD-only assistant can answer natural-language questions that are
expressible from the recorded, redacted audit schema: counts and unique cases,
staff/role/action groupings, percentages, lists, trends, averages and recorded
workflow durations. It also has explicit read-only handlers for roles,
permissions, workflow, notification routing, model inputs and the acuity-scale
convention. When Azure OpenAI/Foundry is configured, the model translates the
question into a bounded JSON query plan; it is not given audit rows, database
access, SQL, files or mutation tools. The backend validates every requested
field/operator/time range, performs the calculation and logs the query outcome.
Common audit questions have a deterministic fallback when Foundry is absent.

This deliberately means “any question supported by what was actually
recorded,” not an unlimited chatbot. If a requested outcome or field was never
stored—for example, a distinct assessment-failure outcome—the assistant says it
cannot be calculated and does not substitute a nearby count. Patient-specific
triage, diagnosis and treatment advice remains refused.

## Useful endpoints

- `/health` — service and active UHL asset state
- `/status/uhl` — UHL dataset/cache/model status
- `/runtime/status` — redacted runtime configuration
- `/ready` — deep UHL model/cache/writable-storage readiness (503 until usable)
- `/cases` — bounded, paginated UHL case list
- `/model/performance` — UHL model evidence in the unchanged 22.4 UI contract
- `/system/meta` — release metadata
- `/notifications` — role-filtered durable in-app notifications
- `/notifications/system/health` — restricted, redacted notification health
- `/system/assistant` — ITD-only validated system/audit question interface
- `/retraining/exports` — ITD-only monthly export list and counts
- `/retraining/exports/generate` — ITD-only idempotent previous-month preparation
- `/retraining/exports/{YYYY-MM}/download` — ITD-only integrity-checked CSV

On Azure, `startup-backend.sh` restores or builds the validated SQLite case
index before Uvicorn starts. Durable workflow state and a verified cache seed
live under `/home/data`; the active high-volume SQLite index lives at
`/tmp/alter-uhl-cache/uhl_cases.sqlite3`. Keep the synthetic demo at one App
Service instance and one Uvicorn worker because clinical transition locks are
process-local.

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
`tests/test_uhl_22_4_swap.py` and `tests/test_uhl_role_retraining_trends.py`.
This integration preserves the newer
pseudonym-aware UHL cache and resolver invalidation fixes already present on
`main`, rather than restoring the archive's deferred cache defect. The merged
Python 3.11 environment completed the backend, role/export, security and API
checks with no failures (1,131 passed and 21 intentionally skipped in the final
scheduler-enabled run), while the React suite passed 52/52 and the production
Vite build transformed 2,076 modules. See the delivery verification report for
the exact run context. The supplied archive's independent validation records
remain documented in the release notes.

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
