# AI-MTS local review · 8 November 2026

This release is a local research application for the supplied synthetic UHL dataset. The ZIP contains source, the compiled React interface, the existing model and the files required to run the application. The review folder contains the audit disposition register, requirement matrix, verification evidence and engineering decisions.

## Run the bundled application on Windows

1. Extract the ZIP into a new folder. Install Windows x64 CPython 3.11 if needed.
2. Open PowerShell in the extracted application folder.

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Run-Local-Review.ps1
   ```

3. Open **http://127.0.0.1:8770/**. Use `-Port 8771` if another process is using 8770. Use `-PythonExecutable 'C:\path\to\python.exe'` to select a specific Python installation.
4. Stop the server with **Ctrl+C**.

The first launch verifies and installs the locked Windows wheels from PyPI in `.venv-local-review`. Later launches reuse the environment. The bundled interface runs without Node.js. The launcher binds to loopback and configures local role selections and storage. It disables SMS and optional cloud-generated case explanations. The recorded decision explanation runs locally. The review starts with no clinical reference annotations. The launcher selects the balanced 100-case cohort and loads the small pinned JSON directly. Local observations and reviews persist under `.local-review-data/`.

## Review the workflow

- **Triage Nurse → Triage Queue.** Open a case, check the complaint and chart proposal, and confirm the chart. The visible checklist adapts to the chosen chart. Enter assessed findings, or mark an observation unavailable or not yet assessed. Use fictional practice entries only. The checklist keeps its draft when you cancel navigation.
- **Recommendation.** A valid saved assessment is restored on opening a case. For a new or changed case, use Run assessment or save and reassess the checklist. Positive assessed MTS findings or recorded vital safeguards can raise urgency. A recommendation can exist while the chart is still incomplete. Accepting with an incomplete assessment requires a recorded reason. Inspect the recorded decision explanation, then expand decision evidence and model details for the exact assessment.
- **ED Doctor → Review Queue.** Open a nurse-reviewed case to see the advisory and the evidence from that saved assessment.
- **Governance Auditor → Audit Log.** Inspect the event history and complete-journey export. The recorded decision remains tied to the observations present when it was made.
- **Researcher → Model Evidence.** View separate ML, completed MTS and advisory evaluation sections by presenting complaint. The empty dataset of independently obtained references yields unavailable metrics. Reference records already collected remain available to the evidence APIs and exports; there is no Research Review screen.

## Rebuild and test from source

Use the bundled application for local review. For development, install the runtime and test requirements in an isolated Python 3.11 environment and run the backend checks. Install Node.js and use the committed npm lock for the frontend.

```powershell
py -3.11 -m venv .venv-dev
& .\.venv-dev\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
& .\.venv-dev\Scripts\python.exe -m pytest -q
Push-Location frontend-react
npm ci
npm test -- --run
npm run build
Pop-Location
```

Then run the local launcher to serve the rebuilt frontend with the backend. To regenerate the Windows x64 wheel lock after a reviewed change to the runtime dependency list, resolve on Windows x64 CPython 3.11, check the version and wheel changes, then test a clean installation.

```powershell
py -3.11 -m pip --isolated install --dry-run --ignore-installed --only-binary=:all: --report runtime-resolution.json -r requirements-runtime.txt
py -3.11 scripts/lock_local_runtime.py runtime-resolution.json requirements-local-win64-py311.lock
```

## Identify the data, model and rules

The selected model is `artifacts/model/uhl_synthetic_acuity_selected.joblib`. Its trusted SHA-256 is `7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b`. The source CSV archived with the project is `data/uhl_dataset_final.csv.gz`. The supplied corrected CSV was inspected separately. See the cohort metadata and validation receipts for the exact input digest and selection method. Health and Model Evidence report the active artifact and rule information. Logged assessments bind the chart, rule outcomes, recommendation policy, model and source observations.

The model consumes triage-time inputs and was trained with synthetic vitals and estimated acuity. Clinical discriminator findings come from assessed observations and supported measurement predicates. A model replacement requires a compatible serving artifact, verified feature and label contracts, a new pin and independent evaluation. See [study gates](docs/LOCAL_REVIEW_R3_STUDY_GATES.md) and [hospital data handoff](docs/HOSPITAL_DATA_HANDOFF.md).
