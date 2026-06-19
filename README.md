# AI Triage Agentic Workflow

This project is a **research-only AI triage workflow** covering two public datasets kept **separate** (never combined): **MIMIC-IV-ED Demo v2.2** is the default dataset (public demo data, 222 ED stays, no credentials required), and the Kaggle Emergency Service **KTAS** dataset is available as a separate option. Full, credentialed MIMIC-IV-ED access remains pending PhysioNet approval. The deployed application is the **Streamlit UI** (`frontend/app.py`); a FastAPI service also exists but is retained for future API work, not the current deployment.

## Clinical safety status

- **Not for clinical use.**
- A **provisional, unvalidated Manchester-style research ruleset is active by default** (set `PROVISIONAL_MTS_MODE=off` to fully gate). The categories it produces are **NOT the official Manchester Triage System** and **NOT clinically approved** — see `RULESET_PROVENANCE.md`.
- **No official Manchester Triage System mapping is implemented**, and no clinician-approved ruleset is registered.
- The KTAS ML model is a research estimate only and is **withheld for MIMIC cases** (it is never applied outside its KTAS training distribution).
- Human clinical review is required for **every** output.

## Deployment readiness, by audience (a separate question from clinical safety)

The clinical safety status above is permanent and by design -- this
project is not becoming clinically deployable by adding more
infrastructure. What CAN change with more work is how widely and
reliably it can be *demonstrated or used as a research tool*:

- **Supervisor / in-person demo**: ready now. Run `streamlit run
  frontend/app.py` (the UI is self-contained and loads both datasets
  directly; it does not require the API). This is the configuration the
  test suite exercises.
- **Azure research deployment (a real, reachable URL)**: close, but not
  yet proven. The `Dockerfile` now runs the Streamlit app
  (`streamlit run frontend/app.py`), so the deployable artifact exists
  (Option A, documented in `infrastructure/azure_deploy.md`). What
  remains: actually deploying it to Azure and confirming it opens, the
  MIMIC default appears, provisional categories render with warnings,
  and the AutoGen layer either works or cleanly shows NOT_CONFIGURED.
  Audit logs are currently local JSONL (fine for a demo; for a hardened
  deployment they would move to Azure Blob/Table/Cosmos).
- **Clinical deployment (real patient triage, in any capacity)**: NOT
  ready, and not close -- a fundamental gap, not an infrastructure one.
  The active Manchester ruleset is **provisional and unvalidated**, not
  clinician-approved. No UHL-specific data or validation exists. No
  full-MIMIC validation exists (only the public demo subset). No
  MIMIC-specific trained model exists (the only model in this project is
  trained on
  KTAS, deliberately withheld from MIMIC cases -- see "MIMIC-IV-ED Demo
  v2.2 pipeline" below). KTAS itself is not Manchester Triage Scale.
  Every output requires human clinical review by explicit design, with
  no override path. None of this is close to resolved by more
  engineering effort alone; it requires clinical and regulatory work
  this project does not attempt.

## Current dataset

Raw file expected at:

```bash
data/raw/kaggle_ktas/data.csv
```

If you have already downloaded `data.csv` somewhere else (e.g. your Downloads folder), copy or move it into that exact path before running the pipeline. On Windows PowerShell, from the project root:

```powershell
Copy-Item "C:\Users\<you>\Downloads\data.csv" "data\raw\kaggle_ktas\data.csv"
```

The supplied Kaggle CSV is semicolon-separated, Latin-1 compatible, and contains decimal commas in `KTAS duration_min`. Dirty placeholders such as `#BOÞ!` and `??` are converted to nulls.

## Main workflow

```text
Kaggle KTAS CSV
→ KTAS adapter and schema validation
→ Processed triage-time case JSONL
→ KTAS label builder
→ Model training: Dummy, Logistic Regression, Random Forest, GaussianNB
→ Deterministic safety review (Manchester engine, leakage guard, safety review agent)
→ ML KTAS research estimate
→ AutoGen clinician chat agent (single AssistantAgent; explains the above; never decides)
→ AutoGen multi-agent team (IntakeAgent → ValidationAgent → SafetyReviewAgent
  → ExplanationAgent, RoundRobinGroupChat; additive to the single-agent chat
  above, same strict evidence-only/explain-only boundary, shares the same
  underlying evidence-lookup tool and safety filter)
→ Follow-up comparison (explicit, user-declared "stay B follows stay A";
  deterministic vital/status diff; escalation note if it crosses a
  notable threshold -- never automatic patient matching)
→ Trial Matcher-style assessment card (assessment status, research model
  output, evidence used, matched indicators/reason codes, missing
  information, uncertainty, workflow action, clinician review
  requirement, audit/log reference -- all read from already-computed
  output, no new decision logic)
→ Human review / governance audit
→ Streamlit dashboard (Triage Review, Follow-Up Comparison, Clinician
  Chat, Governance, Review Queue, Audit Log, Model Performance)
```

A second, independently built pipeline exists for the public MIMIC-IV-ED
Demo v2.2 dataset (`app/data_pipeline/mimic_adapter.py`,
`scripts/audit_mimic_demo.py`), sharing every layer above plus its own ML
acuity model. MIMIC demo cases are selectable via a dataset filter (MIMIC
demo only / KTAS only -- the datasets are kept SEPARATE, with no combined
view) on the Triage Review tab, defaulting to
MIMIC demo. MIMIC now has its own ML pipeline:
`scripts/build_mimic_demo_labels.py` builds triage-time features + the
`acuity` label (leakage columns excluded), and
`ml_training/train_mimic_acuity_model.py` trains an acuity model. For a
MIMIC case, `run_ml_prediction()` predicts the ESI `acuity` level and maps
it to a five-level colour/priority display via
`app/rules/acuity_mts_mapping.py` (1->Red, 2->Orange, 3->Yellow,
4->Green, 5->Blue); a deterministic escalate-only vital override
(`app/rules/acuity_override.py`) can raise -- never lower -- extreme or
critical cases. For a KTAS case, the KTAS model predicts a KTAS class only
and no Manchester/MTS category is shown. The two models are never mixed:
the KTAS model is never applied to MIMIC and vice versa. All outputs are
research-only, not clinically validated, and require clinician review. The
MIMIC model is a small public-demo artefact (207 labelled rows, CV
accuracy ~0.52) -- a pipeline demonstration, not a clinically usable model.
See `docs/DUAL_PIPELINE_ARCHITECTURE.md` for the build status.

This project is designed to run KTAS, MIMIC-IV-ED Demo v2.2, and (once
access is approved) the full MIMIC-IV-ED dataset side by side as separate
dataset-specific pipelines that share the same agent architecture,
orchestration pattern, and safety layer. See
`docs/DUAL_PIPELINE_ARCHITECTURE.md` for the full design, checked against
the real file paths in this repository.

## Run from a clean checkout

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/run_ktas_pipeline.py
pytest
uvicorn app.main:app --reload
streamlit run frontend/app.py
```

## Important outputs

```text
data/processed/triage_cases_sample.jsonl
data/processed/triage_input_only_sample.jsonl
data/processed/retrospective_labels_sample.jsonl
data/processed/ktas_labels.jsonl
data/processed/model_evaluation_report.json
data/processed/dataset_audit_report.json
data/processed/missing_triage_inputs_report.json
data/processed/mimic_demo_audit_report.json
data/processed/triage_indicator_matrix_log.json
data/models/registry.json
```

## Verification

```bash
# Prints a readable Indicator | Expected | Actual | Pass/Fail table covering
# every individual vital threshold direction and every individual complaint
# pathway, derived directly from live execution of the Manchester engine
# (see KTAS_CHANGELOG.md for the exact methodology and the two non-obvious
# dispatch-logic facts this surfaced).
python scripts/run_triage_indicator_matrix.py

# Full data audit for the MIMIC-IV-ED Demo pipeline: tables, columns, row
# counts, missingness, and explicit triage-time-safe vs. retrospective
# field classification.
python scripts/audit_mimic_demo.py
```

## Target policy

Main target:

```text
label_ktas_expert = KTAS_expert, values 1–5
```

Secondary target:

```text
label_ktas_emergency = 1 if KTAS_expert in {1,2,3}, else 0
```

Blocked from triage-support model features:

```text
KTAS_RN
KTAS_expert
mistriage
Error_group
Diagnosis in ED
Disposition
Length of stay_min
KTAS duration_min
```

`KTAS_RN` is preserved for audit but excluded from the main model because it is already a nurse triage decision.

## AutoGen integration

Real `autogen-agentchat` orchestration for the explanation/chat layer, in two
additive forms:

- **Single agent**: `app/agents/autogen_team.py`, one `AssistantAgent` with a
  single evidence-lookup tool, used by the in-page case chat in the Triage Review tab's
  free-form Q&A.
- **Multi-agent team**: `app/agents/autogen_multi_agent_team.py`, a real
  four-agent `RoundRobinGroupChat` (IntakeAgent → ValidationAgent →
  SafetyReviewAgent → ExplanationAgent), used by the same tab's "Run
  multi-agent team explanation" button. All four agents share the exact
  same single evidence-lookup tool as the single-agent path (one source of
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

Try the single agent from the command line:

```bash
# Deterministic baseline (no LLM, works without any credentials)
python scripts/autogen_agentchat_team.py --mode deterministic --stay-id 1

# Real AutoGen chat agent (requires Azure OpenAI credentials in .env;
# without them, prints a clear NOT_CONFIGURED message rather than failing)
python scripts/autogen_agentchat_team.py --mode chat --question "Tell me about stay 1"
```

Or via the API once `uvicorn app.main:app --reload` is running:

```bash
curl -X POST http://127.0.0.1:8000/chat/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Tell me about stay 1"}'

# Multi-agent team, by stay_id rather than a free-form question:
curl -X POST http://127.0.0.1:8000/chat/team-explanation \
  -H "Content-Type: application/json" \
  -d '{"stay_id": 1}'
```

Or from the Streamlit UI's Triage Review tab (in-page "Ask about this case" chat), which has both the
free-form chat input and the multi-agent team button.

The single-agent path is backed by `run_single_question()`; the multi-agent
path by `run_team_explanation()`. Both share the same underlying evidence
tool and the same `app/rules/llm_safety_filter.py::check_forbidden_phrases()`
core, so neither can silently diverge from the other on what counts as a
forbidden phrase.

The AutoGen integration tests (`tests/test_autogen_team.py`,
`tests/test_autogen_multi_agent_team.py`) use AutoGen's own
`ReplayChatCompletionClient` test infrastructure, which genuinely exercises
the tool-calling and (for the team) round-robin coordination machinery (the
agents really do call the real evidence lookup function, in the real
order, and the real termination condition really fires) without needing a
live Azure credential. What those tests cannot verify is how a real Azure
OpenAI deployment behaves in practice against the system prompts -- that
should be checked manually against a real deployment before relying on
this for any demo.

**A known testing-harness limitation** (not an application bug, found and
documented in `tests/test_frontend.py::TestMultiAgentTeamButtonInChatTab`'s
docstring and in `KTAS_CHANGELOG.md`): Streamlit's `AppTest` (version
1.56.0) cannot reliably render `frontend/app.py`'s `azure_configured=True`
branch in this test environment, for either the existing single-agent chat
input or the new multi-agent team button -- this was conclusively shown to
be a pre-existing harness limitation (reproducible even with the entire
chat tab stubbed to one line, and absent entirely when using real
environment variables instead of any `monkeypatch`), not a flaw in the
application. The underlying functions and API routes for both AutoGen
paths are fully tested through routes that do not depend on `AppTest`
rendering that branch.

## MIMIC-IV-ED Demo v2.2 pipeline (built)

A second, independently built pipeline exists for the public MIMIC-IV-ED
Demo v2.2 dataset (222 ED stays, no PhysioNet credentials required). The
copy of this data currently in this repository was supplied directly by
Dylan as the real, official PhysioNet zip; every file's SHA256 checksum
was independently, manually recomputed and matched against that
specific zip's own `SHA256SUMS.txt` ONE TIME, before any adapter code
was written against it (see `KTAS_CHANGELOG.md`'s "MIMIC-IV-ED Demo v2.2
adapter" entry for the full record). This is NOT the same thing as what
`scripts/download_mimic_ed_demo.py` checks on a fresh download -- that
script (`app/data_pipeline/download.py::verify_downloaded_headers()`)
only compares each file's CSV column-header row against an expected
list; it does not compute or compare a SHA256 hash. If you re-download
this data yourself rather than using the copy already in this repo, you
are getting the header-schema check, not a fresh checksum re-verification
-- if you want that stronger guarantee, recompute the hashes yourself
against PhysioNet's published `SHA256SUMS.txt`:

```bash
data/raw/mimic-iv-ed-demo/2.2/ed/   # six real .csv.gz tables
```

```bash
python scripts/audit_mimic_demo.py   # full audit report: tables, columns,
                                      # row counts, missingness, triage-time-
                                      # safe vs. retrospective classification
```

The adapter (`app/data_pipeline/mimic_adapter.py`) reuses the exact same
`EDTriageCase` / `TriageTimeInput` / `WorkflowResult` schema and the exact
same orchestrator, safety rules, leakage guard, and AutoGen agents as the
KTAS pipeline -- confirmed by running real MIMIC cases through
`run_workflow()`. MIMIC now has its own ML pipeline (label builder, feature
engineering, registered acuity model): selecting a MIMIC case DOES produce
an ML estimate -- `run_ml_prediction()` checks `source_dataset` and, for a
MIMIC case, predicts the ESI `acuity` level and maps it to a five-level
colour/priority display, with a deterministic escalate-only vital override.
For a KTAS case it predicts a KTAS class only; the two models are never
mixed. This is verified directly against both a real KTAS case (KTAS class)
and a real MIMIC case (acuity → mapped category). The Model Performance tab
reports both the KTAS models and the MIMIC acuity model, each labelled
research-only.

## Full MIMIC-IV-ED / UHL future phase

The full, credentialed MIMIC-IV-ED dataset (~216,000 stays) and UHL
validation remain pending PhysioNet approval and formal governance
sign-off respectively. The project keeps the relevant paths in
`app/config.py` (`raw_ed_dir`, distinct from the demo's `raw_demo_dir`),
but full MIMIC-IV-ED and UHL validation must not be claimed until the
relevant access, governance, data dictionary, and validation approvals
are actually in place. See `docs/DUAL_PIPELINE_ARCHITECTURE.md`'s "Current
build status" section for the exact, current state of every MIMIC-related
component.
