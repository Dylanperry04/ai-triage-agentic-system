# Azure deployment notes — KTAS research mode

This project can be deployed as a research demo to Azure, but it is
**not for clinical use**. Read `KTAS_CHANGELOG.md` and `README.md`'s
"Clinical safety status" section before deploying anything.

## What is and is not covered below

This document is honest about its own limits: the architecture below
(two separate services, the environment variables, the CORS setting, the
model/data handling) is concrete and matches the real code in this
repository. What it does NOT do is commit to a specific Azure resource
group, subscription, region, App Service plan tier, or naming
convention -- those depend on UHL/Dylan's own Azure environment and
constraints, which this document has no visibility into. Anywhere a
real decision is needed, it is marked **[DECISION NEEDED]** rather than
filled in with an invented placeholder that might be mistaken for a
verified instruction.

## Architecture: DECISION MADE — Option A (deploy the Streamlit UI)

**Decision (Dylan):** the deployable Azure web app is the **Streamlit UI**.
The `Dockerfile` and `startup.sh` now run `streamlit run frontend/app.py`
(not `gunicorn app.main:app`). This was chosen because the Streamlit UI is
already fully self-contained — it imports the workflow
(`app.agents.orchestrator.run_workflow`) and the engines in-process and makes
**no HTTP calls to the FastAPI backend** (confirmed directly: no
`localhost:8000`/`127.0.0.1:8000`/`API_BASE` reference anywhere in
`frontend/app.py`). So the app shown in the screenshots IS the Streamlit app,
and deploying it requires no API service and no inter-service networking.

The FastAPI service (`app/main.py`) still exists in the repo as a programmatic
API for future use, but it is NOT on the deployment path for Option A.

The Streamlit deployment reads the real KTAS CSV
(`data/raw/kaggle_ktas/data.csv`) and the real MIMIC-IV-ED Demo files
(`data/raw/mimic-iv-ed-demo/2.2/ed/*.csv.gz`) directly via `load_cases()`'s
adapters. The current `Dockerfile` copies both PUBLIC datasets into the image
so the demo runs; the full credentialed MIMIC path (`data/raw/mimic-iv-ed/`)
is gitignored and never copied.

### The other options, for the record (not chosen)

- **Option B — deploy both FastAPI and Streamlit as two services.** More cost
  and maintenance for an integration that does not currently exist (the UI does
  not call the API). Steered away from.
- **Option C — refactor `frontend/app.py` to call the FastAPI API over HTTP,
  then deploy both.** The "correct" production shape IF the API later needs to
  be the single source of truth (multiple clients, hardened service layer). It
  is multi-day work and adds a network failure mode; several UI features
  (follow-up edit-and-rerun, the multi-agent team, the assessment card) pull
  richer objects than the current routes return, so the API would need building
  out first. Documented as the future target, not done now.

### Remaining Option-A deployment decisions (still genuinely open)

The original two-service plan below is superseded by the Option A decision
above, but these sub-decisions remain:

1. **Streamlit on App Service vs. Container App** — different startup-command
   and networking conventions. The provided `Dockerfile` works for either a
   Container App or App-Service-for-Containers; a code-only App Service would
   instead use `startup.sh`.

<details>
<summary>Superseded two-service notes (kept for context)</summary>

The recommended approach was previously:

1. **Deploy the FastAPI backend** using the existing `Dockerfile` /
   `startup.sh`, unchanged, to one Azure App Service (or Container App).
2. **Deploy the Streamlit frontend separately**, to a second Azure App
   Service (or Container App), using a new, separate Dockerfile (a
   template is given below -- not yet built, since it depends on
   **[DECISION NEEDED: do you want Streamlit on App Service, a Container
   App, or something else? They have different startup-command and
   networking conventions]**).
3. Point the Streamlit deployment at the FastAPI deployment's real URL
   (see "Connecting the two services" below). `frontend/app.py`
   currently has no API base URL configuration at all, and does not
   call the FastAPI backend over HTTP anywhere -- confirmed directly,
   there is no `localhost:8000`/`127.0.0.1:8000`/`API_BASE`-style
   reference anywhere in that file. It reads the real KTAS CSV
   (`data/raw/kaggle_ktas/data.csv`) and the real MIMIC-IV-ED Demo files
   (`data/raw/mimic-iv-ed-demo/2.2/ed/*.csv.gz`) directly via
   `load_cases()`'s real adapters, not a single pre-built processed
   file (this changed during a later session that merged MIMIC cases
   into the Streamlit case selector -- `load_cases()` previously read a
   pre-built `triage_cases_sample.jsonl`, but no longer does). This
   means a deployed Streamlit instance, as the code stands today, would
   need its own copy of BOTH raw datasets (see "Model and data artifact
   handling" below) rather than calling a deployed API for case data --
   **[DECISION NEEDED: should frontend/app.py be changed to call the
   deployed API instead, so the two services share one source of truth,
   or is reading its own local copy of the raw data acceptable for this
   phase? README.md's "Run from a clean checkout" section currently only
   documents running Streamlit locally via `streamlit run frontend/app.py`
   against a locally-running API -- it does not yet describe or assume
   any deployed Streamlit configuration, so this decision has not been
   made elsewhere and is being raised here for the first time]**.

If you would rather not split the deployment work in two right now, the
honest alternative is to deploy only the FastAPI backend (which the
existing Dockerfile already does) and run Streamlit locally against it,
exactly as `README.md`'s "Run from a clean checkout" section already
describes -- that is a fully legitimate, smaller-scope deployment for a
research demo, and this document does not assume you must do the
two-service version.

</details>

## FastAPI backend: startup and configuration

The existing `Dockerfile` / `startup.sh` already do the right thing for
this half. To deploy:

```bash
docker build -t ktas-research-api .
```

**Required environment variables** (set these as Azure App Service
"Application settings" / Container App secrets, never baked into the
image -- the Dockerfile already deliberately excludes `.env`):

| Variable | Required? | Purpose |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT` | Only if AutoGen chat/team explanation should be live | Real Azure OpenAI resource endpoint |
| `AZURE_OPENAI_API_KEY` | Only if AutoGen chat/team explanation should be live | Should come from Azure Key Vault, not a plain App Service setting, for anything beyond a quick demo -- **[DECISION NEEDED: does UHL/Dylan's Azure subscription already have a Key Vault set up for this project, or does one need to be provisioned?]** |
| `AZURE_OPENAI_DEPLOYMENT` | Only if AutoGen chat/team explanation should be live | The deployed model name in your Azure OpenAI resource |
| `AZURE_OPENAI_API_VERSION` | Only if AutoGen chat/team explanation should be live | e.g. `2024-08-01-preview` (the same example used in `.env.example`) -- confirm the correct value for your deployed API version, do not assume this example is current |
| `CORS_ALLOWED_ORIGINS` | Yes, before any real deployment | Comma-separated list of allowed frontend origins, e.g. `https://your-streamlit-app-url,http://localhost:8501`. Defaults to local-dev-only origins if unset -- see `app/config.py::_default_cors_origins()`. **Never set this to `*`** when `allow_credentials=True` is in effect (see `app/main.py`); FastAPI's CORSMiddleware reflects the requesting origin back in that combination, which in practice allows credentialed requests from any origin at all. |
| `PORT` | No | `startup.sh` defaults to `8000` if unset; Azure App Service typically sets this automatically |

If none of the four `AZURE_OPENAI_*` variables are set, the app does not
crash -- every AutoGen-facing code path (`app/agents/autogen_team.py`,
`app/agents/autogen_multi_agent_team.py`) returns a clear
`NOT_CONFIGURED` result instead, and the deterministic pipeline
(Manchester engine, leakage guard, safety review, ML research estimate)
is completely unaffected.

**Before deploying**, run:

```bash
python scripts/run_ktas_pipeline.py
python scripts/azure_preflight_check.py
pytest
```

The FastAPI service is NOT the deployed app under Option A (the Streamlit UI
is). If you do deploy FastAPI separately, its `/health` endpoint reports the
live state, e.g.:

```text
clinical_use = not_for_clinical_use
default_dataset = MIMIC-IV-ED-Demo-v2.2
provisional_mts_mode = enabled
official_manchester_triage = not_implemented
human_review_required = true
```

Do not test `/health` for the Streamlit deployment -- Streamlit has no `/health`
route. Use a root-page load as the Streamlit health probe instead.

## Streamlit frontend: not yet covered by an existing deployment artifact

As stated above, no Dockerfile in this repository currently builds or
runs the Streamlit frontend. A starting template, to be adapted once the
**[DECISION NEEDED]** items above are resolved:

```dockerfile
# infrastructure/Dockerfile.streamlit (NOT YET CREATED -- template only)
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY frontend/ ./frontend/
COPY app/ ./app/
# Streamlit needs the same processed-data outputs the API uses for its
# own case selector, OR needs to be pointed at the deployed API instead
# of reading data/raw/ directly -- **[DECISION NEEDED: should the
# deployed Streamlit app read local raw data files baked into its
# own image, or should it call the deployed FastAPI backend for
# everything? The current frontend/app.py code reads the real KTAS CSV
# and MIMIC-IV-ED Demo files directly via load_cases()'s real adapters
# rather than calling the API for case data, so the former is closer to
# today's actual code, but the latter is the more typical separated
# architecture]**.
EXPOSE 8501
CMD ["streamlit", "run", "frontend/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

This is a starting point, not a verified, tested deployment artifact --
it has not been built or run in this session.

## Connecting the two services

Once both are deployed, the Streamlit app needs the FastAPI backend's
real URL, and the FastAPI backend needs the Streamlit app's real URL (for
`CORS_ALLOWED_ORIGINS`). Both are circular dependencies on each other's
final deployed address, so the practical order is usually: deploy both
once with placeholder/localhost values, note the real URLs Azure assigns,
then update each service's settings with the other's real URL and
restart.

## Model and data artifact handling

The existing `Dockerfile` already does the right thing here: it copies
`data/models/` (trained `.pkl` files) and `data/processed/` (the sample
case JSONL files) into the image, and explicitly excludes `data/raw/`
(real patient-derived data, even if currently public/de-identified) and
`.env` (secrets) from ever being baked into the image. For a longer-lived
deployment, the comment already in the Dockerfile is the right next
step: load `data/models/` from Azure Blob Storage at container startup
rather than baking a snapshot into the image, so retraining the model
does not require rebuilding and redeploying the whole container. This
has not been implemented -- **[DECISION NEEDED: is this worth doing for
a research-demo deployment, or is baking the current trained models into
the image acceptable for now?]**.

## Logging

No centralized log storage (e.g. Azure Log Analytics, Application
Insights) has been configured or even discussed in this project so far.
Application logs currently go wherever `gunicorn`/`uvicorn`'s stdout
goes, which Azure App Service captures by default into its own log
stream, but nothing is retained, structured, or queryable beyond that
default behaviour. **[DECISION NEEDED: does this project need real log
retention/querying for the research-demo phase, or is the App Service
default log stream sufficient?]**

## Do not deploy this as a clinical triage system

KTAS is not Manchester, no clinician-approved Manchester ruleset is
registered, and UHL validation has not been performed. None of the
deployment steps above change that. See `docs/KTAS_SAFETY_NOTES.md` and
this repository's `README.md` for the full safety status.
