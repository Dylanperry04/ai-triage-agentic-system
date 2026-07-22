# Azure deployment notes — full MIMIC-IV-ED research system

> **NOT FOR CLINICAL USE.** This is a research decision-support prototype. The ML
> model predicts acuity; the LLM layer only explains. Clinician review is required
> on every output. Patient-data deployment is gated on hospital controls
> (Entra/MFA/private network/Key Vault/durable audit/governance/clinical-safety
> and security review) that live OUTSIDE this repository and are NOT provided by
> it. The codebase never asserts `patient_data_ready: true`.

## Architecture (current: single service — see next section)

The **current** deployment is a SINGLE service: the FastAPI backend serves the
built React UI itself. See "Single-service React UI (v18)" immediately below for
the authoritative description. The two-service Streamlit shape described here is
RETIRED and kept only for historical reference.

The one constant across both shapes is the **FastAPI backend** — the sole
server-side enforcement boundary (auth, fail-closed, redaction, audit). All
protected actions go through it. It reads the only prediction/training dataset,
full MIMIC-IV-ED (credentialed), from `MIMIC_FULL_ED_DIR` on an approved
environment, and the trained model from `MIMIC_FULL_MODEL_PATH`. Without those it
fails closed (serves no cases, makes no predictions). Demo/KTAS datasets do not
exist in this system.

Legacy (retired) second service: a **Streamlit frontend** that called the
backend over HTTP via `FASTAPI_BASE_URL`. It is no longer part of the deployment;
the React UI below replaces it.

Synthetic MIMIC-shaped fixtures are used only for automated tests and the Azure
supervisor demo. No credentialed data or trained artefact is ever bundled into
the repo or a build.

## Single-service React UI (v18, current deployment shape)

The Streamlit presentation service is retired. The React frontend
(`frontend-react/`, built to `frontend-react/dist`) is served by the FastAPI
backend itself, so ONE Azure App Service runs the whole system:

- Browsers requesting `/` receive the React app; API clients requesting JSON
  still receive the original status payload at `/` and always at
  `/system/meta`. Set `SERVE_REACT_UI=false` to run as a pure API.
- The UI calls the API same-origin, so `CORS_ALLOWED_ORIGINS` is not needed
  for the built-in UI (it remains relevant only for external frontends).
- `frontend-react/dist` is committed, so Azure "Deploy from GitHub"
  (Oryx/Python) needs no Node build. The GitHub Actions workflow
  (`.github/workflows/azure-deploy.yml`) also rebuilds the UI on every push.

### Azure App Service checklist (supervisor demo)

1. App Service: Linux, Python 3.11 (matches `runtime.txt`), startup command
   `bash startup-backend.sh`.
2. Deployment: connect the GitHub repo (Deployment Center) or add the
   `AZURE_WEBAPP_PUBLISH_PROFILE` secret and app name to the workflow.
3. Application settings for the synthetic supervisor demo:

   | Setting | Value |
   |---|---|
   | `AZURE_SUPERVISOR_DEMO_MODE` | `true` |
   | `ALLOW_DEMO_ROLE_SWITCHER` | `true` |
   | `ENABLE_OVERDUE_VITALS_SWEEPER` | `true` (server-side recheck alerts) |
   | `MIMIC_FULL_MODEL_PATH` | `model_outputs/last_training/mimic_full_acuity_selected.joblib` |
   | `MIMIC_FULL_MODEL_REPORT_DIR` | `model_outputs/last_training` |
   | `MIMIC_FULL_MODEL_SHA256` | contents of `model_outputs/last_training/mimic_full_model_sha256.txt` |
   | `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` (Oryx installs requirements.txt) |

   `MIMIC_FULL_MODEL_SHA256` pins the artefact so `/model/performance` serves
   the training artefacts (Model Evidence / System Health pages). Optional:
   the `AZURE_OPENAI_*` settings enable the LLM explanation layer.
4. In this profile `/cases` serves the built-in synthetic supervisor-demo
   cohort (`data/demo/azure_supervisor_demo_cases.jsonl`, regenerable via
   `scripts/generate_supervisor_demo_cases.py`). Assessments show the
   deterministic rules-engine category with an explicit note: ML predictions
   deliberately run only against credentialed full MIMIC (see the
   `real_mimic_azure_demo` and local credentialed research profiles).
5. The demo role selector in the sign-in screen sends `X-Demo-Role` (and a
   synthetic persona display name via `X-Demo-User`) — accepted only in demo
   profiles and ignored under real auth, trusted proxy, patient-data and
   local-research modes.

## Profiles

- **public_demo** (default): no credentialed data; the app fails closed.
- **azure_supervisor_demo** (`AZURE_SUPERVISOR_DEMO_MODE=true` and
  `ALLOW_DEMO_ROLE_SWITCHER=true`): Azure-hosted, synthetic/no-real-patient-data
  walkthrough with a clearly labelled simulated role selector in the sidebar.
  The package includes a tiny synthetic MIMIC-shaped supervisor-demo case source
  for this profile so the demo does not need credentialed MIMIC. This is not real
  authentication. It must not be combined with
  `PATIENT_DATA_MODE`, `LOCAL_CREDENTIALED_RESEARCH`, `TRUSTED_AUTH_PROXY`,
  `AUTH_REQUIRED`, `REAL_PATIENT_DATA`, or real full-MIMIC data unless a separate
  governed non-public demo explicitly sets `ALLOW_FULL_MIMIC_IN_AZURE_DEMO=true`.
- **local_credentialed_research** (`LOCAL_CREDENTIALED_RESEARCH=true`): an
  approved local research machine. Loads the researcher's own credentialed MIMIC
  WITHOUT asserting the full production security posture. Hardened: the backend
  must bind to loopback (`BACKEND_BIND_HOST=127.0.0.1`) or it refuses to start,
  and cloud LLM/AutoGen/W&B egress is OFF by default (opt in only after verifying
  zero-retention/no-training/no-human-review terms). Mutually exclusive with
  production patient-data mode (production wins).
- **production patient-data** (`PATIENT_DATA_MODE=true`): the secured hospital
  deployment. Requires the hospital-provided controls; the startup guard refuses
  to start on an unsafe config.

## Backend deployment

Container runs `uvicorn app.main:app`. Required/served configuration is read at
process start (restart after any change). Azure services do not automatically
inherit a local `.env`; every required variable below must be set in the Azure
App Service / Container App configuration for the target service. On the approved
environment:

```
MIMIC_FULL_ED_DIR=/path/to/mimic-iv-ed/2.2/ed     # edstays.csv.gz, triage.csv.gz, ...
MIMIC_FULL_MODEL_PATH=/path/to/mimic_full_acuity_selected.joblib
MIMIC_FULL_MODEL_SHA256=<sha256 of the artefact>  # required in PATIENT_DATA_MODE
```

`MIMIC_FULL_MODEL_SHA256` is an optional provenance pin for local research. In
`PATIENT_DATA_MODE=true`, it is mandatory: the prediction agent refuses to load a
model artefact without a configured SHA-256 hash.

`CORS_ALLOWED_ORIGINS` must be set to the frontend origin (no wildcard).

For patient-data mode, use `SECRETS_PROVIDER=keyvault` and store
`PSEUDONYM_SECRET` in Key Vault. Do not also set plain env `PSEUDONYM_SECRET`;
the runtime pseudonymisation path refuses that misconfiguration unless an
explicit dev-test override is set.

## Frontend deployment

```
FASTAPI_BASE_URL=https://<backend-host>
```

The repository's CSV/JSONL resolver is intended for public demo and local
credentialed research. In `PATIENT_DATA_MODE`, free-text `/cases?q=...` search is
disabled until the deployment wires a database/search-index-backed case query
layer with bounded performance tests. Outside patient-data mode, unindexed search
is bounded by `MIMIC_CASE_SEARCH_SCAN_LIMIT` and reports `total_is_exact=false`
when the scan window is truncated.

## Model training/comparison (approved environment only)

```
python -m ml_training.full_mimic.train               # simple baseline
python -m ml_training.full_mimic.compare_models      # safety-first comparison
python -m ml_training.full_mimic.compare_models --quick-test   # fast smoke test
```

`compare_models` uses a patient-grouped (or temporal) split, selects on a
validation set among candidates passing an over-triage/specificity constraint,
and reports final metrics once on an untouched test set (AUROC/PR-AUC/CI/
subgroups). Point `MIMIC_FULL_MODEL_PATH` at the produced artefact after review.

## Preflight

```
python scripts/azure_preflight_check.py
```

Verifies the single-service (FastAPI-serves-React), full-MIMIC-only configuration. It does not and cannot
verify the hospital controls, real full-MIMIC loading, or trained-model quality —
those are confirmed on the approved environment.
