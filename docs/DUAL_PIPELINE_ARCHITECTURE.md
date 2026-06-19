# Dual-Pipeline Architecture: KTAS + MIMIC-IV-ED

## Why this document exists

Dylan's project uses two MIMIC-related datasets at different stages plus
the public Kaggle KTAS dataset: KTAS (available immediately, used to
start building while waiting for approval), MIMIC-IV-ED Demo v2.2 (the
public, no-credential-required demo subset of MIMIC-IV-ED, 222 ED stays
-- **now built**, see "Current build status" below), and the full
MIMIC-IV-ED dataset (~216,000 stays, requires a credentialed PhysioNet
account, **still pending approval** as of this writing). KTAS and the
Manchester Triage Scale used at UHL are different acuity scales, built
from different vital-sign conventions (Celsius vs Fahrenheit, no shared
patient identifier, different missingness patterns), so they cannot be
silently merged into one feature space without losing the ability to say
where any given number came from. The same caution applies to MIMIC.

This document records the architectural decision made for this project:
**one overall system, two separate dataset-specific pipelines**, and
shows exactly which parts of the codebase are shared and which are kept
deliberately apart. It is written to be checked against the real file
paths, not as an aspirational diagram -- every path named below exists
in this codebase today.

## Current build status (read this before assuming anything is "pending")

- **KTAS**: fully built. See the rest of this document and
  `KTAS_CHANGELOG.md`.
- **MIMIC-IV-ED Demo v2.2**: fully built, on the real, official,
  publicly-available demo dataset (no PhysioNet credentials required).
  Every checksum in the dataset's own `SHA256SUMS.txt` was independently,
  manually verified, one time, against the specific zip Dylan supplied,
  before any code was written against it -- this is NOT the same
  guarantee as what `scripts/download_mimic_ed_demo.py` checks on a
  fresh download (column headers only, no hash; see
  `app/data_pipeline/download.py::verify_downloaded_headers()`). Adapter:
  `app/data_pipeline/mimic_adapter.py`. Audit script:
  `scripts/audit_mimic_demo.py`. See the "MIMIC-IV-ED Demo v2.2 adapter"
  entry in `KTAS_CHANGELOG.md` for the full build record, including the
  real data-quality issues found by direct inspection.
- **Full MIMIC-IV-ED** (~216,000 stays, credentialed access): **still
  pending PhysioNet approval**. No code in this repository assumes or
  depends on this being available. When approval arrives, the existing
  demo adapter's design (see below) is intended to extend cleanly to the
  full dataset's identical six-table schema, but this has not been
  attempted yet and should not be assumed to work without re-verification
  against the full dataset's actual files once they are available --
  schema or convention differences between the demo and full releases,
  if any, have not been checked.
- **MIMIC-specific ML** (label builder, feature engineering, model
  registry key analogous to KTAS's `best_ktas_model`): **built for the
  MIMIC-IV-ED Demo release.** `scripts/build_mimic_demo_labels.py` builds
  triage-time features + the `acuity` label (leakage columns excluded);
  `ml_training/train_mimic_acuity_model.py` trains an acuity model
  registered as `best_mimic_acuity_model` (beside, never overwriting, the
  KTAS entries). A MIMIC case predicts the ESI `acuity` level, mapped to a
  five-level colour/priority display via
  `app/rules/acuity_mts_mapping.py`, with a deterministic escalate-only
  vital override (`app/rules/acuity_override.py`). KTAS and MIMIC are
  SEPARATE pipelines and their models are never mixed. The **full**
  MIMIC-IV-ED release remains untouched until credentialed access is
  granted and its real files are inspected -- nothing full-MIMIC-specific
  is assumed. The demo acuity model is a small research artefact (207
  rows, CV ~0.52), not clinically usable.

## The decision

> Keep it as one overall system, but with two separate dataset-specific
> pipelines. KTAS and MIMIC should share the same agent architecture,
> AutoGen/orchestration pattern, Streamlit shell, safety review layer,
> leakage guard, audit trail, and governance logic, but each dataset
> should keep its own adapter, feature set, model registry, label
> semantics, evaluation report, and model card. Do not merge KTAS and
> MIMIC into one modelling pipeline or relabel one as the other.

This was Dylan's explicit decision after the alternative (one merged
pipeline with normalised shared features) was presented and rejected,
because forcing both datasets into one common feature space would either
throw away dataset-specific signal (KTAS's mental-status and
patients-per-hour fields; MIMIC's own diagnosis/medication-reconciliation
tables) or risk silently treating one dataset's acuity label as
equivalent to the other's, which neither this project's governance
charter nor the underlying clinical reality supports.

## What is shared (one copy, used by both datasets)

| Layer | File | What it does for both datasets |
|---|---|---|
| Triage-time vs retrospective schema split | `app/schemas/internal.py` (`TriageTimeInput`, `RetrospectiveLabels`, `EDTriageCase`) | Defines the same leakage boundary shape for any dataset's adapter to populate |
| Leakage guard | `app/rules/leakage_guard.py`, `app/schemas/mimic_ed.py::RETROSPECTIVE_OR_LEAKAGE_COLUMNS` | One blocklist, extended (not replaced) with dataset-specific field names as each adapter is added |
| Vital-sign safety detection (Layer 1) | `app/rules/manchester_engine.py` (`_critical_vital_flags`, `_concern_vital_flags`), `app/rules/vitals.py` (`temperature_c`) | Same physiological danger thresholds applied to any dataset's vitals, via the shared unit-aware temperature conversion |
| Manchester pathway gate (Layer 2) | `app/rules/manchester_engine.py` (`register_approved_ruleset`, pathway functions) | Disabled by default for every dataset; would require the same formal clinician sign-off regardless of which dataset triggered a pathway match |
| Data validation agent | `app/agents/data_validation_agent.py` | Same completeness checks, reading whichever `TriageTimeInput` fields are populated |
| Safety review agent | `app/agents/safety_review_agent.py` | Same critical/concern physiology detection and `is_safe_to_present` logic |
| Orchestrator | `app/agents/orchestrator.py` (`run_workflow`) | Same agent sequencing for any `EDTriageCase`, regardless of `source_dataset` |
| Follow-up comparison agent | `app/agents/followup_comparison_agent.py` | Same deterministic vital-band comparison logic; works on any two `WorkflowResult` objects regardless of which dataset(s) they came from (with an explicit warning if the two linked stays are from different datasets) |
| AutoGen orchestration pattern | `app/agents/autogen_team.py` (single agent), `app/agents/autogen_multi_agent_team.py` (four-agent `RoundRobinGroupChat` team, additive to the single-agent path) | Same `AssistantAgent`/team + evidence-only-tool(s) + post-hoc safety-filter design in both; the shared tool calls `run_workflow()`, which already works for any dataset |
| Streamlit shell | `frontend/app.py` | Same six-tab structure (the standalone Clinician Chat tab was removed; the case chat and multi-agent explanation now live inside the Triage Review tab); `load_cases()` merges real KTAS and MIMIC demo records (loaded live via their respective adapters, not a single pre-built file) into one list, with a dataset filter on the Triage Review and Follow-Up Comparison tabs so a user can narrow to one dataset |
| Audit trail / governance reporting pattern | `app/api/governance_routes.py`, the five-stage review-gate structure | Same reporting shape; dataset-specific facts (leakage field list, model names) are filled in per dataset, not hardcoded once and reused blindly |

## What is dataset-specific (one copy per dataset, never shared)

| Layer | KTAS (built) | MIMIC-IV-ED Demo v2.2 (built) | Full MIMIC-IV-ED (pending access) |
|---|---|---|---|
| Adapter | `app/data_pipeline/ktas_adapter.py` | `app/data_pipeline/mimic_adapter.py` | Not yet built -- access pending. When built, the demo adapter's design is intended to extend, but this must be re-verified against the full dataset's real files first; it will NOT be merged into either existing adapter file. |
| Dataset-specific schema fields | `age`, `group`, `patients_per_hour`, `injury`, `mental_state`, `nrs_pain`, `pain_present`, `temperature_unit='C'` (added to `TriageTimeInput`/`TriageSource` for KTAS) | Reuses the same `TriageTimeInput`/`TriageSource` fields as KTAS (`temperature_unit='F'`); no MIMIC-specific fields needed to be added -- the existing schema, built for KTAS, was found to already exactly match MIMIC's real columns with zero changes | Schema match not yet re-verified against the full dataset's actual files |
| Retrospective/leakage fields | `ktas_expert`, `ktas_rn`, `mistriage`, `error_group`, `diagnosis_in_ed`, `disposition_code`, `length_of_stay_min`, `ktas_duration_min` | `acuity` (MIMIC's own 1-4/5 nurse triage scale, distinct from both KTAS and Manchester), `disposition`, `outtime`, `hadm_id`, all `diagnosis`/`medrecon`/`pyxis` records, and all `vitalsign.csv` rows (excluded as a *table*, not just by field name -- see `app/data_pipeline/mimic_adapter.py`'s module docstring for why) | Not yet re-verified |
| Label builder | `scripts/build_ktas_labels.py` -- `KTAS_expert` (1-5, research target only) and `KTAS_expert <= 3` (binary "emergency" research target) | Not yet built. Will produce its own MIMIC-specific research labels under its own script name, e.g. `scripts/build_mimic_outcome_labels.py` -- never reusing the KTAS label semantics | Not started |
| Feature set | `ml_training/feature_engineering.py` -- KTAS-specific 34-feature set (age, group, injury, mental state, vitals, etc.) | Not yet built | Not started |
| Model registry | `data/models/registry.json` -- keys `best_ktas_model` / `best_emergency_model`, model files named `*_ktas_*.pkl` | Not yet built -- will use distinctly-named keys (e.g. `best_mimic_model`) and distinctly-named model files in the same registry file, or a separate registry file if that proves cleaner, once a MIMIC model is trained -- never overwriting or aliasing the KTAS entries | Not started |
| Evaluation report / model card | `data/processed/model_evaluation_report.json`, `docs/KTAS_SAFETY_NOTES.md` | Not yet built -- will get its own evaluation report and its own safety-notes document (e.g. `docs/MIMIC_SAFETY_NOTES.md`), not a shared one with the dataset name swapped in | Not started |
| Audit / data-quality report | `data/processed/missing_triage_inputs_report.json` (built from `scripts/inspect_missing_triage_inputs.py`) | `data/processed/mimic_demo_audit_report.json`, built by `scripts/audit_mimic_demo.py` -- tables, columns, row counts, missingness, explicit triage-time-safe vs. retrospective field classification, and the real data-quality issues found in `triage.temperature` and `triage.pain` (see `KTAS_CHANGELOG.md`) | Not started |
| `source_dataset` label | Every KTAS case is tagged `"Kaggle-KTAS"` | Every MIMIC demo case is tagged `"MIMIC-IV-ED-Demo-v2.2"` (`app/data_pipeline/mimic_adapter.py::SOURCE_DATASET_LABEL`), checked by the follow-up comparison agent's dataset-mismatch warning | Will need its own distinct label (e.g. `"MIMIC-IV-ED"`, without the `-Demo-v2.2` suffix) to remain distinguishable from the demo subset once built |

## The one explicit rule this whole design exists to enforce

**No model output, no rules-engine output, and no agent output may ever
claim to be a Manchester Triage System category unless
`register_approved_ruleset(..., acknowledge_heuristic_pathways=True)` has
been called after formal clinical governance sign-off -- and this applies
identically regardless of which dataset produced the input.** KTAS
research labels stay labelled as KTAS research labels
(`KTAS_expert`-derived). MIMIC research labels, once built, will stay
labelled as MIMIC research labels. Neither is ever silently presented as
the other, and neither is ever silently presented as Manchester triage.

## What remains when full MIMIC-IV-ED access is approved

The demo adapter above (`app/data_pipeline/mimic_adapter.py`) already
demonstrates that this extends cleanly -- the orchestrator, the safety
rules, the leakage guard, the AutoGen agents, and the Streamlit shell all
worked on real MIMIC demo cases with zero dataset-specific code added to
any of them. The remaining steps when full access arrives:

1. Re-verify the full dataset's actual six-table schema against the demo
   adapter's `EXPECTED_COLUMNS` before assuming it matches -- do not
   assume the full release uses identical column names, units, or
   missingness patterns just because the demo release does.
2. Extend `mimic_adapter.py` (or add a parallel adapter, if the full
   dataset's schema turns out to differ in a way that's cleaner to keep
   separate) to read the full dataset's files, tagging cases with a
   distinct `source_dataset` label so demo-derived and full-dataset-derived
   cases remain distinguishable.
3. Build MIMIC's own label script, feature set, training script entry
   point, and registry keys, following this document's table above --
   none of this has been started for either MIMIC release yet.
4. Everything in the "shared" table above continues to work unmodified --
   already demonstrated true for the demo dataset, and expected (though
   not yet verified) to hold for the full dataset too, because the shared
   layers only ever read the common `EDTriageCase` / `TriageTimeInput` /
   `WorkflowResult` shapes, never a dataset-specific one.
5. The Streamlit UI's case selector will need to be extended to also
   include full-MIMIC cases once that dataset exists in this project --
   **this item is now PARTIALLY DONE, in a later session than the one
   that originally wrote it**: KTAS and MIMIC-IV-ED Demo cases are
   merged into the UI's case selector, with a dataset filter to narrow
   between them, on the Triage Review and Follow-Up
   Comparison tabs (see `README.md`'s "MIMIC-IV-ED Demo v2.2 pipeline"
   section). The original premise of this item -- "currently only KTAS
   cases are loaded into `data/processed/triage_cases_sample.jsonl`, the
   file the Streamlit shell reads from" -- is no longer accurate on
   either count: the Streamlit shell's `load_cases()` no longer reads
   that file at all, and now loads live from both real adapters (KTAS
   CSV + MIMIC demo files) directly. What remains genuinely open is
   specifically the FULL, credentialed MIMIC-IV-ED dataset, which does
   not exist anywhere in this project yet (pending PhysioNet approval)
   -- once it does, the case selector and dataset filter will need a
   third option added.
