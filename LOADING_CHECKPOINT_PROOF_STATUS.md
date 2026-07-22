# Research checkpoint — loading fix + priorities 1–7 (NOT deployment-ready)

**RESEARCH CHECKPOINT — NOT DEPLOYMENT-READY.**

The test count in this build is the author's local result and is NOT independently
verified. Re-run the suite from this zip before trusting it.

## The two bugs (both fixed in this checkpoint)

1. **Loader/profile (the actual cause of the empty case list).** The full-MIMIC
   loader refused unless `PATIENT_DATA_MODE=true`. In local demo identity mode
   that is false, so `is_full_mimic_available()` returned False, and the resolver
   + frontend swallowed the error and returned `[]` with no explanation.
2. **Orchestrator (the downstream second failure).** `orchestrator.py` built the
   final acuity only for the deleted `MIMIC-IV-ED-Demo-v2.2` dataset, so even
   after loading was fixed, a full-MIMIC case's assessment card would show no
   final acuity. Fixed to `MIMIC-IV-ED-Full-v2.2`.

## Required checkpoint proofs — honest status

| # | Proof | Status |
|---|-------|--------|
| A | `/status/full-mimic` reports a safe diagnostic (no path leaked) | **VERIFIED in this build** (re-run to confirm) |
| B | `/cases` returns pseudonymous summaries | **VERIFIED in this build** (end-to-end test) |
| C | full-MIMIC assessment returns visible final acuity | **VERIFIED in this build** (end-to-end test) |
| D | no loader exception silently converted into an unexplained empty list | **VERIFIED in this build** (resolver logs the reason) |
| E | `LOCAL_CREDENTIALED_RESEARCH` cannot start on a non-loopback bind | **IMPLEMENTED** (priority-1 hardening; `test_local_research_hardening.py`) |
| F | all cloud/LLM calls blocked in that profile | **IMPLEMENTED** (priority-1 hardening; `test_local_research_hardening.py`) |

**UPDATE:** Priority-1 hardening of `LOCAL_CREDENTIALED_RESEARCH` is now done, so
proofs E and F hold:
- The backend refuses to start in this profile unless `BACKEND_BIND_HOST` is set
  to a loopback interface (it fails closed if the bind host is undeclared).
- Cloud egress (Azure OpenAI via `load_azure_config()`, and W&B via
  `wandb_configured()`) is OFF by default in this profile; `load_azure_config()`
  returns None even with Azure credentials present. It re-enables only with the
  explicit `ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH=true` opt-in, which the operator
  must set only after verifying the provider's zero-retention/no-training/
  no-human-review terms.

This is still a RESEARCH CHECKPOINT. Items 2-7 (feature engineering, evaluation
methodology, serving scalability, LLM handling, artefact/input hardening, docs)
remain OUTSTANDING.

## What IS in this checkpoint

- New `LOCAL_CREDENTIALED_RESEARCH` profile (distinct from production
  `PATIENT_DATA_MODE`; mutually exclusive — production wins if both set). It lets
  a credentialed researcher load their own MIMIC locally and see it in the app.
  It already enforces the DATA guards: path set, outside the repo, exists, is a
  directory, and the core tables (edstays.csv.gz, triage.csv.gz) present. It does
  NOT yet enforce loopback bind or block cloud calls (E/F).
- Specific diagnostics instead of silent failure: the loader raises precise,
  non-sensitive reasons (env not set / mode not enabled / dir missing / wrong
  directory level / required table missing); the resolver logs them; a
  `full_mimic_diagnostic()` returns the reason without exposing the path.
- Backend `/status/full-mimic` endpoint; the sidebar reads status FROM THE BACKEND
  (authoritative in two-service mode) instead of the Streamlit container's env.
- Orchestrator final-acuity branch fixed to full-MIMIC.
- End-to-end synthetic test: profile/config -> loader -> /cases -> case selection
  -> assessment -> visible final acuity, with no raw identifiers.

## How to load your data on your approved local machine (after E/F land)

On the BACKEND process, before startup:
```
LOCAL_CREDENTIALED_RESEARCH=true
MIMIC_FULL_ED_DIR=/path/to/mimic-iv-ed/2.2/ed   # the 'ed' folder (edstays.csv.gz, triage.csv.gz, ...)
MIMIC_FULL_MODEL_PATH=/path/to/mimic_full_acuity_selected.joblib
```
Restart BOTH services after changing configuration (env is read at settings
construction). The sidebar will show the backend's status and, if not loadable,
the specific reason.

## OUTSTANDING (NOT in this checkpoint) — see external review

The institutional/environment items remain outside this codebase and are NOT done
here (they cannot be, in this sandbox):
- Real full-MIMIC loading and prediction on the approved environment.
- Actual model metrics on the real cohort (this repo provides the corrected
  evaluation HARNESS, not real numbers).
- Hospital controls: Entra/MFA, private network, Key Vault (real client, not the
  string-satisfying stub), durable audit sink, service-to-service auth.
- DPIA / MDR / EU-AI-Act regulatory review; clinical-safety and security review.

These are tracked as institutional prerequisites and are NOT code-fixable here.

---

## Priorities 1–7 — status in THIS build

All seven agreed priorities are implemented in this build (tests included). They
are NOT independently verified until you re-run the suite from this zip.

1. **LOCAL_CREDENTIALED_RESEARCH hardened** — loopback-only bind enforced
   (BACKEND_BIND_HOST; fails closed if undeclared/non-loopback); cloud LLM/AutoGen/
   W&B egress OFF by default (cloud_egress_allowed); mutually exclusive with
   production; data guards (path outside repo, core tables) enforced.
   Tests: test_local_research_hardening.py.
2. **Feature engineering redesigned for the real MIMIC-IV-ED schema** — KTAS-
   derived/absent features removed (age, patients_per_hour, injury/mental/group
   codes, KTAS arrival categories); correct MIMIC arrival_transport mapping;
   exact train/serve parity (one shared extractor). Tests:
   test_feature_engineering.py.
3. **Evaluation methodology corrected** — patient-grouped split (no subject_id
   spans train/val/test), temporal split that is also patient-grouped, three
   sets, selection on validation only, final reported once on untouched test,
   over-triage/specificity constraint (rejects 'predict everything urgent'),
   AUROC/PR-AUC/bootstrap CI/subgroups. Tests: test_evaluation_methodology.py.
4. **Serving scalability** — triage-time-only load (edstays+triage, not all six
   tables), O(1) cached uid index (not O(n) scan), pagination + bounded results
   (cap 200). Tests: test_serving_scalability.py.
5. **LLM handling** — raw stay_id removed from prompts (pseudonymous ref only),
   evidence free-text redacted, cloud explanation off by default in local
   research, screened clinician question passed into the prompt, structured
   (section-level) output validation. Tests: test_llm_handling.py.
6. **Artefact/input hardening** — model load FAILS CLOSED on compatibility-check
   error; exact feature-name/order parity verified at predict time; optional
   SHA-256 provenance pin; follow-up vitals allow-list + ranges; review-field
   enums/length limits + comment redaction. Tests: test_artifact_input_hardening.py.
7. **Docs/version** — version 14.0.0; azure_deploy.md and README rewritten to the
   implemented two-service full-MIMIC architecture; last KTAS test fixture
   replaced with a MIMIC-shaped one.

### Checkpoint loading proofs (A–F)

| # | Proof | Status |
|---|-------|--------|
| A | /status/full-mimic safe diagnostic (no path leaked) | present (re-run to confirm) |
| B | /cases returns pseudonymous summaries | present (end-to-end test) |
| C | full-MIMIC assessment returns visible final acuity | present (end-to-end test) |
| D | no loader exception silently → unexplained empty list | present (resolver logs reason) |
| E | LOCAL_CREDENTIALED_RESEARCH cannot start on non-loopback bind | IMPLEMENTED (priority 1) |
| F | all cloud/LLM calls blocked in that profile | IMPLEMENTED (priority 1) |
