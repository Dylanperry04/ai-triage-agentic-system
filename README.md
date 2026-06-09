# AI Triage Agentic System — MIMIC-IV-ED Demo Pipeline

This repository is a production-shaped starter for the AI triage internship project.

It does **not** invent clinical rules. It starts with the verified public
MIMIC-IV-ED Demo v2.2 schema and builds a safe data pipeline:

1. Download MIMIC-IV-ED Demo v2.2 automatically.
2. Verify exact expected headers for all six ED tables.
3. Load all six tables.
4. Build one internal `EDTriageCase` per `stay_id`.
5. Separate triage-time inputs from retrospective outcome/leakage fields.
6. Run a FastAPI backend and Streamlit review UI.
7. Produce an audit-safe workflow record.

Clinical safety note:

- This is not a medical device.
- It must not be used for real triage.
- No Manchester Triage final rules are implemented until a clinician-validated ruleset is supplied.
- The MIMIC original `acuity` field is preserved as source data but is **not** treated as Manchester Triage truth.

## Dataset

This project uses:

- MIMIC-IV-ED Demo v2.2
- Public demo subset: 100 patients
- Same six-table ED structure as MIMIC-IV-ED:
  - `edstays`
  - `triage`
  - `vitalsign`
  - `diagnosis`
  - `medrecon`
  - `pyxis`

## Windows + VS Code setup

Open PowerShell in this folder.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, run:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then activate again.

## Step 1 — Download the dataset

```powershell
python scripts\download_mimic_ed_demo.py
```

This downloads the six `.csv.gz` files into:

```text
data/raw/mimic-iv-ed-demo/2.2/ed/
```

## Step 2 — Verify schemas

```powershell
python scripts\verify_mimic_schema.py
```

This checks the exact headers. If PhysioNet changes a file or the wrong version is downloaded, the script fails.

## Step 3 — Build a small sample while preserving final structure

```powershell
python scripts\build_sample_cases.py --n 25
```

Output:

```text
data/processed/triage_cases_sample.jsonl
data/processed/triage_input_only_sample.jsonl
data/processed/retrospective_labels_sample.jsonl
data/processed/schema_report.json
```

The `triage_cases_sample.jsonl` file preserves source data grouped into internal cases.
The `triage_input_only_sample.jsonl` file contains only fields allowed into a triage-time workflow.

## Step 4 — Run tests

```powershell
pytest
```

## Step 5 — Run API

```powershell
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

## Step 6 — Run UI

In a second terminal:

```powershell
streamlit run frontend\streamlit_app.py
```

## Key safety design

The pipeline separates data into:

| Group | Used for triage? | Notes |
|---|---:|---|
| `triage_inputs` | Yes | Initial chief complaint and triage-time vitals |
| `source_context` | Sometimes | ED stay identifiers and arrival metadata |
| `retrospective_labels` | No | Original acuity, disposition, diagnoses |
| `medication_context` | No by default | Stored but excluded until time-use policy is approved |
| `vitals_timeseries` | No by default | Stored but excluded from first triage-time decision unless time-filtered |
| `audit_metadata` | No | Processing and provenance trace |

## Why no final Manchester classification yet?

The attached internship document says final Manchester classification should be deterministic/rule-based, not LLM-based. However, the official Manchester Triage System discriminator rules are not part of MIMIC-IV-ED. Therefore this repository includes the rules-engine interface but does not fabricate a clinical ruleset.

The next safe step is to obtain or define a clinician-approved Manchester-style ruleset, then implement it in:

```text
app/rules/manchester_engine.py
```
