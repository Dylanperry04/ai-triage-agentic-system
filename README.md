# AI Triage Agentic Workflow (research prototype)

This project is a **research-only AI triage decision-support system**. The ML
model predicts ED acuity; the LLM/agent layer only **explains** the prediction
(it cannot assign, alter, or override triage). **NOT FOR CLINICAL USE — clinician
review is required for every output.**

## Dataset

The **only** prediction/training dataset is **full MIMIC-IV-ED v2.2
(credentialed)**. It is read from `MIMIC_FULL_ED_DIR` on an approved environment
and is **never bundled** into this repo or a build. The trained model is read from
`MIMIC_FULL_MODEL_PATH`. Without `MIMIC_FULL_ED_DIR` the app **fails closed** and
serves no cases. Without `MIMIC_FULL_MODEL_PATH`, cases can still be listed, but
assessments report the model as unavailable. Synthetic MIMIC-shaped fixtures are
used only for **automated tests and the Azure supervisor demo**. They are never
a clinical source, never a patient-data source, and never real MIMIC records.

There is no real-MIMIC public demo dataset and no KTAS dataset in this system;
earlier dual/triple-dataset designs were removed. The only built-in demo case
source is `data/demo/azure_supervisor_demo_cases.jsonl`, a small synthetic
supervisor-demo fixture clearly labelled: not real MIMIC and not real patient
data. (Historical changelog: `KTAS_CHANGELOG.md`.)

## Architecture

Two services:

- **FastAPI backend** — the sole server-side enforcement boundary (auth,
  fail-closed, redaction, audit, pseudonymous `case_uid` only). All protected
  actions go through it.
- **Streamlit frontend** — frontend-only; every protected action calls the
  backend via `FASTAPI_BASE_URL`. It obtains full-MIMIC status from the backend
  (`/status/full-mimic`), never from its own environment.

The ML model produces an acuity estimate; a deterministic safety layer flags
critical physiology independently of the model; provisional Manchester-style
display categories are research-only (not the official Manchester Triage System,
not clinically approved). Every output requires clinician review.

## Profiles

- **public_demo** (default): no credentialed data; fails closed.
- **azure_supervisor_demo** (`AZURE_SUPERVISOR_DEMO_MODE=true` and
  `ALLOW_DEMO_ROLE_SWITCHER=true`): synthetic/no-real-patient supervisor demo.
  The demo role switcher is allowed only in this fake-auth mode and must remain
  disabled for patient-data, local credentialed research, trusted-proxy, and
  real-auth modes.
- **azure_supervisor_demo with governed full-MIMIC access**: optional, explicit,
  non-public supervisor demo mode for credentialed full MIMIC. Requires all of:
  `AZURE_SUPERVISOR_DEMO_MODE=true`, `ALLOW_DEMO_ROLE_SWITCHER=true`,
  `ALLOW_FULL_MIMIC_IN_AZURE_DEMO=true`, `REAL_MIMIC_DEMO_ACKNOWLEDGED=true`,
  and a backend-readable `MIMIC_FULL_ED_DIR`. If full MIMIC is requested but the
  directory is missing or unreadable, the app must fail closed and must not fall
  back to synthetic demo cases. This is still not hospital SSO, not real UHL/HSE
  patient data, not patient-data deployment readiness, and not clinical use.
- **local_credentialed_research** (`LOCAL_CREDENTIALED_RESEARCH=true`): an
  approved local machine. Loads the researcher's own credentialed MIMIC without
  the full production security posture. Hardened: backend must bind loopback
  (`BACKEND_BIND_HOST=127.0.0.1`) and Streamlit must call it through
  `FASTAPI_BASE_URL`; in-process backend fallback is refused unless explicitly
  overridden for local dev tests. Cloud LLM/W&B egress is OFF by default and
  requires both technical opt-in and documented data-processing approval.
  Mutually exclusive with production (production wins). The sidebar role
  switcher is disabled in this mode. To change local role, set
  `LOCAL_RESEARCH_ROLE` to one of `triage_nurse`, `ed_doctor`,
  `clinical_supervisor`, `researcher`, `security_admin`, or
  `governance_auditor`, then restart FastAPI and Streamlit.
- **production patient-data** (`PATIENT_DATA_MODE=true`): the secured hospital
  deployment, gated on hospital-provided controls (Entra/MFA/private network/
  Key Vault/durable audit/governance) that are NOT in this repo.

## Model training / comparison (approved environment only)

`ml_training/full_mimic/`: `train.py` (baseline) and `compare_models.py`
(safety-first comparison). The comparison uses a patient-grouped (or temporal)
split, selects on a validation set among candidates passing an over-triage/
specificity constraint, and reports final metrics once on an untouched test set
(high-acuity recall, severe under-triage, under/over-triage, macro/weighted F1,
MAE, quadratic weighted kappa, within-one-acuity accuracy, confusion matrix,
per-class recall, AUROC/PR-AUC/CI/calibration/subgroups). Feature engineering
uses only fields present in the real MIMIC-IV-ED schema with exact train/serve
parity: triage vitals, arrival/gender, chief-complaint keyword groups, missingness
indicators, and vital interaction features. TF-IDF text-only and
structured+TF-IDF baselines are reported as experimental, non-serving model
candidates; they are not eligible for runtime selection under the current
structured feature contract.
Synthetic/demo/test fixtures are refused as model-training/model-evaluation
sources. Aggregate report artefacts should be written to an outside-repo folder
and exposed to the app with `MIMIC_FULL_MODEL_REPORT_DIR` (or
`MIMIC_FULL_REPORT_DIR` / `MIMIC_FULL_OUTPUT_DIR`).

## Not deployment-ready

This is a research prototype. Patient-data deployment is gated on the hospital
controls above plus clinical-safety and security review and a model trained and
validated on the approved environment — none of which live in this repository.
The codebase never asserts `patient_data_ready: true`.

The built-in `/cases` resolver is for public demo and local credentialed
research. In `PATIENT_DATA_MODE`, free-text case search is disabled until a real
database/search-index-backed query layer is wired and performance-tested.

## Runtime environments

Keep the app and training dependency environments separate.

```bash
# App venv: FastAPI / Streamlit / AutoGen explanations
python -m venv venv-app
venv-app\Scripts\activate
pip install -r requirements.txt -r requirements-autogen.txt
python scripts/check_autogen_imports.py

# Training venv: full-MIMIC model comparison / W&B / optional ML libraries
python -m venv venv-training
venv-training\Scripts\activate
pip install -r requirements.txt -r requirements-ml.txt
```

Do not install `requirements-ml.txt` into `venv-app`, and do not install
`requirements-autogen.txt` into `venv-training`. This avoids training/W&B
dependency resolution breaking the AutoGen explanation runtime.

## Run from a clean checkout

```bash
python -m venv venv-app
venv-app\Scripts\activate   # Windows PowerShell: venv-app\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-autogen.txt
python scripts/check_autogen_imports.py
pytest

# Backend (terminal 1, PowerShell, local credentialed research)
$env:LOCAL_CREDENTIALED_RESEARCH="true"
$env:BACKEND_BIND_HOST="127.0.0.1"
$env:PSEUDONYM_SECRET="<generate-a-long-random-local-secret>"
$env:LOCAL_CREDENTIALED_OUTPUT_DIR="C:\Users\YOUR_USER\ai-triage-local-output"
$env:ACCESS_AUDIT_DIR="C:\Users\YOUR_USER\ai-triage-local-output\audit"
$env:MIMIC_FULL_ED_DIR="C:\Users\YOUR_USER\Downloads\mimic-iv-ed-2.2\ed"
# Optional, but needed for real model predictions instead of withheld predictions:
# $env:MIMIC_FULL_MODEL_PATH="C:\Users\YOUR_USER\path\to\model.joblib"
# Optional for local research, required for patient-data mode:
# $env:MIMIC_FULL_MODEL_SHA256="<sha256-of-model.joblib>"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Frontend (terminal 2, PowerShell), pointed at the backend
$env:LOCAL_CREDENTIALED_RESEARCH="true"
$env:FRONTEND_BIND_HOST="127.0.0.1"
$env:FASTAPI_BASE_URL="http://127.0.0.1:8000"
streamlit run frontend/app.py --server.address 127.0.0.1 --server.port 8501
```

Without `MIMIC_FULL_ED_DIR`, the app serves no cases. Without `MIMIC_FULL_MODEL_PATH`,
cases are visible but predictions are withheld. If your data is still in
`mimic-iv-ed-2.2.zip`, extract it first and point `MIMIC_FULL_ED_DIR` at the
extracted `ed` folder. In local credentialed mode, `PSEUDONYM_SECRET` and an
outside-repo output/audit directory are required so case identifiers and derived
records do not silently use the development salt or repository-local files.
`MIMIC_FULL_MODEL_SHA256` is optional for local research but mandatory before
patient-data mode will load a model artefact.

## Important outputs

```text
data/models/registry.json                          # full-MIMIC-only model registry
# On the approved environment, compare_models.py writes (to MIMIC_FULL_OUTPUT_DIR):
#   full_mimic_model_comparison.json / .csv
#   mimic_full_model_card.json
#   mimic_full_acuity_selected.joblib
```

## Verification

```bash
# Two-service, full-MIMIC-only preflight check:
python scripts/azure_preflight_check.py

# App venv AutoGen dependency smoke test:
python scripts/check_autogen_imports.py

# Live Azure/runtime endpoint smoke test:
python scripts/azure_smoke_test.py --base-url https://<backend-host> --demo-role researcher

# Full test suite (synthetic MIMIC-shaped fixtures; no credentialed data):
pytest
```

## Target policy

Target (label):

```text
acuity = MIMIC-IV-ED triage.acuity, values 1–5 (1 = most urgent)
```

Blocked from model features (leakage / outcome / identifier columns):

```text
acuity (as input)   disposition   outtime   hadm_id
diagnoses           medrecon      pyxis     full-stay vitalsign
subject_id          stay_id       charttime future
mortality/death     ed_los        length_of_stay admission
```

Features use only fields present at triage time in the MIMIC-IV-ED schema
(`triage` vitals + chiefcomplaint, `edstays` gender/arrival_transport), with
exact train/serve parity. See `ml_training/feature_engineering.py`.

## Explanation integration

The public explanation API is keyed by the **pseudonymous `case_uid`** (never a
raw `stay_id`). It runs the deterministic workflow first, then uses the LLM only
to explain already-computed evidence. It never makes a clinical decision,
modifies a vital sign, or overrides clinician review.

- AutoGen helper modules remain for explicit fixture-based tests, but the
  public app flow uses the case_uid-keyed explanation API above.
- **Multi-agent team**: `app/agents/autogen_multi_agent_team.py`, a real
  four-agent `RoundRobinGroupChat` (IntakeAgent to ValidationAgent to
  SafetyReviewAgent to ExplanationAgent), retained for
  explicit fixture-based tests, not as a raw-ID public route. All four agents
  share the exact same evidence-lookup tool in those tests (one source of
  truth), and each agent's system message narrows its role rather than
  widening its authority -- the `SafetyReviewAgent` is explicitly told it
  does not perform a safety review itself, only restates one already done
  in Python.

The design rationale for both is documented in full at the top of each
file; in short: AutoGen explains already-computed evidence in either form,
it never makes a clinical decision, never assigns a Manchester/KTAS
category, never modifies a vital sign, and never overrides clinician
review. The deterministic Manchester engine, leakage guard, safety review,
and ML prediction are completely unchanged by either integration.

Once `uvicorn app.main:app --reload` is running:

```bash
# List cases (returns pseudonymous case_uids; raw identifiers are never exposed):
curl http://127.0.0.1:8000/cases

# Run a server-side assessment for a case (RBAC-enforced, audited):
curl -X POST http://127.0.0.1:8000/cases/<case_uid>/assessments

# Ask for an explanation of a case (LLM explains only; screened by the gateway):
curl -X POST http://127.0.0.1:8000/cases/<case_uid>/explanations \
  -H "Content-Type: application/json" \
  -d '{"question": "Why this acuity?"}'
```

Legacy raw-ID routes (`/triage/*`, `/chat/*`, etc.) are NOT part of the public API:
they are disabled by default, blocked in patient-data and local credentialed
research mode, and return 410 Gone if explicitly enabled.

The AutoGen integration tests (`tests/test_autogen_team.py`,
`tests/test_autogen_multi_agent_team.py`) use AutoGen's own
`ReplayChatCompletionClient` test infrastructure, which genuinely exercises
the tool-calling and (for the team) round-robin coordination machinery (the
agents really do call the real evidence lookup function, in the real
order, and the real termination condition really fires) without needing a
live Azure credential. What those tests cannot verify is how a real Azure
OpenAI deployment behaves in practice against the system prompts -- that
should be checked manually against a real deployment before relying on
this for any demonstration.

The current Streamlit app displays LLM configuration status only. The
case explanation action goes through the backend `/cases/{case_uid}/explanations`
route.

## Full MIMIC-IV-ED / UHL validation

The live serving/training source is credentialed MIMIC-IV-ED v2.2, supplied at
runtime with `MIMIC_FULL_ED_DIR` and kept outside the repository/build image.
Training artefacts are supplied separately with `MIMIC_FULL_MODEL_PATH` and only
enable prediction; they do not make cases visible in the app. UHL validation
still requires formal governance sign-off and must not be claimed until the
relevant access, data dictionary, validation, and approval work is complete.
