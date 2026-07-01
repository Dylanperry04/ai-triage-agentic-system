# Azure deployment notes — full MIMIC-IV-ED research system

> **NOT FOR CLINICAL USE.** This is a research decision-support prototype. The ML
> model predicts acuity; the LLM layer only explains. Clinician review is required
> on every output. Patient-data deployment is gated on hospital controls
> (Entra/MFA/private network/Key Vault/durable audit/governance/clinical-safety
> and security review) that live OUTSIDE this repository and are NOT provided by
> it. The codebase never asserts `patient_data_ready: true`.

## Architecture (current)

Two services:

1. **FastAPI backend** — the sole server-side enforcement boundary (auth,
   fail-closed, redaction, audit). All protected actions go through it. It reads
   the only prediction/training dataset, full MIMIC-IV-ED (credentialed), from
   `MIMIC_FULL_ED_DIR` on an approved environment, and the trained model from
   `MIMIC_FULL_MODEL_PATH`. Without those it fails closed (serves no cases, makes
   no predictions). Demo/KTAS datasets do not exist in this system.

2. **Streamlit frontend** — frontend-only. Every protected action calls the
   backend over HTTP via `FASTAPI_BASE_URL`. It never reads patient data or runs
   the workflow in-process; it obtains full-MIMIC status from the backend
   (`/status/full-mimic`), not its own environment.

Synthetic MIMIC-shaped fixtures are used only for automated tests and the Azure
supervisor demo. No credentialed data or
trained artefact is ever bundled into the repo or a build.

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

Verifies the two-service, full-MIMIC-only configuration. It does not and cannot
verify the hospital controls, real full-MIMIC loading, or trained-model quality —
those are confirmed on the approved environment.
