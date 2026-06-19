# KTAS migration changelog

## Dataset adapter

- Added `app/data_pipeline/ktas_adapter.py`.
- Reads the real supplied `data.csv` with `sep=';'`, `encoding='latin1'`, and decimal-comma handling.
- Converts dirty placeholders such as `#BOÞ!` and `??` to null.
- Maps Kaggle-coded fields for sex, group, arrival mode, injury, mental state, disposition, and mistriage.
- Converts each CSV row into a canonical `EDTriageCase`.

## Schema changes

- Added KTAS triage-time fields to `TriageTimeInput` and `TriageSource`: age, group, patients per hour, injury, mental state, pain-present flag, NRS pain, and explicit `temperature_unit`.
- Added KTAS retrospective/evaluation fields to `RetrospectiveLabels` only.
- Kept KTAS labels and outcomes out of the triage-time workflow.

## Safety changes

- Treats Kaggle `BT` as Celsius.
- Rules engine and safety review now interpret temperature using `temperature_unit`.
- Preserves the Manchester gate: no Manchester category is assigned without a clinician-approved Manchester ruleset.
- Explicitly blocks KTAS-to-Manchester mapping.

## ML changes

- Replaced MIMIC outcome training with KTAS research training.
- Main target: `KTAS_expert` 1–5.
- Secondary target: emergency binary, `KTAS_expert <= 3`.
- Excluded leakage fields: `KTAS_RN`, `KTAS_expert`, `mistriage`, `Error_group`, `Diagnosis in ED`, `Disposition`, `Length of stay_min`, and `KTAS duration_min`.
- Trains Dummy, Logistic Regression, Random Forest, and GaussianNB by default.
- Optional booster support remains available through `--include-optional-boosters`.

## Pipeline and UI

- Added `scripts/run_ktas_pipeline.py`.
- Updated Streamlit UI for Kaggle KTAS mode.
- Added AutoGen starter scaffold at `scripts/autogen_agentchat_team.py`.
- Added Azure preflight check at `scripts/azure_preflight_check.py`.

## Validation run

- `python scripts/run_ktas_pipeline.py` completed successfully on 1,267 rows.
- `pytest -q` result: 99 passed.
- `python scripts/azure_preflight_check.py` result: PASS.

---

# 2026-06-16 review pass

This section documents a full code review of the KTAS migration described above, performed against the actual `data.csv` and the actual code, not against the claims in the section above. All claims above were independently re-verified by running the pipeline and test suite directly; both held up.

## Confirmed bug fixed

- `ml_training/feature_engineering.py`: the leakage-blocklist check inside `extract_features_from_row()` was dead code -- it looped over `LEAKAGE_FEATURE_BLOCKLIST`, checked membership, and called `continue`, which does nothing (it does not raise, log, or strip the key). It was harmless only because the function happens to build its return dict by manually whitelisting named fields rather than copying from `row`. Fixed by adding a real runtime assertion at the end of the function that `LEAKAGE_FEATURE_BLOCKLIST` never overlaps `FEATURE_NAMES`, which will now fail loudly if a future edit reintroduces a leaked field. A test (`test_leakage_tripwire_actually_fires`) proves the new check actually fires, by temporarily corrupting `FEATURE_NAMES` and confirming a `ValueError` is raised.

## Real safety-relevant duplication fixed

- `app/rules/manchester_engine.py` and `app/agents/safety_review_agent.py` each contained an identical, independently-maintained copy of a Celsius-conversion function (`_temperature_c`). Two copies of clinical threshold-conversion logic is a risk: if one is edited in a future change and the other is not, the two safety-relevant code paths could silently diverge on how they interpret temperature, with nothing in either file's own tests able to catch that divergence. Extracted into a single shared `app/rules/vitals.py::temperature_c`, imported by both. A new test file (`tests/test_vitals.py`) verifies the conversion math directly (including the exact Fahrenheit-to-Celsius points the original hardcoded thresholds depended on: 105.8F=41.0C, 95.0F=35.0C, 103.1F=39.5C, 101.3F=38.5C) and includes an identity check (`is`) proving both modules now reference the exact same function object, not just two functions that happen to agree today.

## Display-text corrections (no effect on any model feature, safety flag, or triage decision)

- `GROUP_MAP` in `ktas_adapter.py` previously read `"Local ED third-degree"` / `"Regional ED fourth-degree"`. No source for "third-degree/fourth-degree" terminology was found; it reads as garbled or confabulated phrasing from an earlier pass. Corrected to plain `"Local ED"` / `"Regional ED"`, matching the verified published data dictionary for this dataset.
- `ARRIVAL_MODE_MAP` previously collapsed arrival-mode codes 5, 6, and 7 all into generic `"Other"`. The verified source dictionary distinguishes them: 5 = public transportation (police etc.), 6 = wheelchair, 7 = other. Updated the display labels accordingly. Confirmed this has zero effect on the `arrival_other` model feature, which already groups `{5, 6, 7}` together by numeric code regardless of label text (`ml_training/feature_engineering.py` line computing `arrival_other` uses `arrival_code in {5, 6, 7}` first, with the string match on `"OTHER"` only as a fallback for cases with no numeric code, which never occurs in this dataset since `Arrival mode` has zero nulls).
- `DISPOSITION_MAP` is left as-is but now explicitly flagged in code and in `docs/KTAS_SAFETY_NOTES.md` as unverified, since no authoritative source for its 7 codes could be found. This has no effect on any model feature or safety decision (Disposition is excluded from both `TriageTimeInput` and `LEAKAGE_FEATURE_BLOCKLIST`-checked features), only on retrospective/audit display text.

## New tests added

- `tests/test_leakage_guard.py::test_ktas_adapter_output_separates_data_correctly` -- exercises the real `dataframe_to_cases()` adapter function (not just hand-built schema objects) on a realistic row taken directly from the supplied `data.csv`, confirming KTAS-specific retrospective fields (`ktas_expert`, `ktas_rn`, `mistriage`, `error_group`, `disposition_code`, `diagnosis_in_ed`, `length_of_stay_min`, `ktas_duration_min`) never reach `TriageTimeInput` and do correctly reach `RetrospectiveLabels`. The pre-existing leakage tests only checked hand-built objects or schema-level field names, not the actual adapter code path.
- `tests/test_feature_engineering.py::test_leakage_tripwire_actually_fires` -- see above.
- `tests/test_vitals.py` (new file, 6 tests) -- see above.

## Independently verified, found correct, no change needed

- The CSV adapter's handling of dirty placeholder values (`??`, `#BOÞ!`) and decimal-comma parsing (e.g. `5,00` in `KTAS duration_min`) was checked against the raw byte content of the actual uploaded `data.csv` and is correct.
- The `Pain` field's `1=present / 0=absent` encoding was independently verified by cross-tabulating against `NRS_pain` (711 of 714 `Pain=1` rows have a real 1-10 pain score; all 553 `Pain=0` rows have none) rather than assumed from any external documentation, since one external source for this same published dataset describes a `1/2` convention that does not match this CSV.
- The leakage boundary between triage-time and retrospective data (`EDTriageCase.to_triage_time_input()` vs `to_retrospective_labels()`) was confirmed structurally correct by reading the actual field-by-field construction, not by trusting the docstring.
- The Manchester engine's gating (no MTS category assigned without `register_approved_ruleset(..., acknowledge_heuristic_pathways=True)`, `requires_clinician_review=True` on every output) was confirmed correct by reading the full decision-flow logic and running it directly against test cases.
- The under-triage-weighted model selection score (`macro_f1 - 1.5 * under_triage_rate` for the 5-class model, `auroc - 1.5 * false_negative_rate` for the binary model) was confirmed to correctly select RandomForest over the weaker GaussianNB model in an actual run on the real 1267-row dataset (GaussianNB's under-triage rate of 0.645 / false-negative-emergency rate of 0.589 are far worse and correctly avoided).
- The two-model registry shape (`best_ktas_model` / `best_emergency_model`) is correctly consumed by `ml_prediction_agent.py`, with a graceful fallback to the old single-model key name and a broad `except Exception` around the whole prediction path so a model-loading failure degrades to an honest "not available" rather than crashing the workflow or silently returning a stale prediction.
- `app/rules/leakage_guard.py` and `app/schemas/mimic_ed.py::RETROSPECTIVE_OR_LEAKAGE_COLUMNS` were correctly extended with KTAS-specific field names, and the pre-existing `test_triage_input_schema_has_no_retrospective_fields` test is generic over that list (not hardcoded to old field names), so it automatically covered the new KTAS fields once the list was extended.

## Open items requiring a decision (not resolved in this pass)

These were left as-is, pending your decision, rather than guessed at:

1. Whether to implement a real AutoGen (`autogen-agentchat`) wrapper around the existing deterministic agents, given `requirements-autogen.txt` exists but no actual AutoGen import exists anywhere in the codebase.
2. Whether to restore the pre-migration Streamlit functionality (clinician chat agent, five-stage governance dashboard, review queue, audit log, model comparison table) adapted for KTAS, or keep the simplified four-tab version.
3. Whether you have the original dataset documentation that would let `DISPOSITION_MAP` be confirmed or corrected.

---

# 2026-06-16 follow-up: real AutoGen integration and full frontend restoration

Resolves two of the three open items above, per explicit instruction: real AutoGen (not a scaffold), and a fuller frontend. `DISPOSITION_MAP` source documentation is confirmed not available and remains unverified by deliberate choice (see docs/KTAS_SAFETY_NOTES.md).

## AutoGen integration

- Installed and verified the exact pinned versions in `requirements-autogen.txt` (`autogen-agentchat==0.7.5`, `autogen-core==0.7.5`, `autogen-ext[openai,azure]==0.7.5`) actually install and work together -- they had never previously been installed or exercised in this project.
- Discovered through direct package inspection (not assumption) that `AzureOpenAIChatCompletionClient` lives in `autogen_ext.models.openai`, not `autogen_ext.models.azure` as might be assumed from the package name -- the `azure` submodule is for Azure AI Foundry/Inference, a different product from Azure OpenAI.
- Added `app/agents/autogen_team.py`: a real `AssistantAgent` whose only tool (`get_verified_evidence_for_stay`) calls the existing, unchanged `run_workflow()` orchestrator. The agent cannot invent a vital sign, assign a triage category, or produce a risk number -- every fact it can discuss was already computed by deterministic code before the agent ever sees it. Full design rationale is in that file's module docstring.
- Split the LLM-output safety filter that previously lived only in `llm_explanation_agent.py` into a shared, format-agnostic phrase-blocking module (`app/rules/llm_safety_filter.py`) plus per-consumer format-specific completeness checks. This was necessary, not cosmetic: the explanation agent's existing checks require every reply to state "no category assigned" and mention missing data, because its system prompt mandates a five-section format. Applying those same checks to free-form chat replies would make the safety flag fire constantly on completely benign short answers (e.g. "the heart rate is 84 bpm"), which would train people to ignore it -- worse than not having the flag at all. The chat agent now has its own lighter-weight, conversation-appropriate check (`_validate_chat_reply_safety`) built on the same shared phrase-blocking core.
- Added `app/api/chat_routes.py` (`POST /chat/ask`) and wired it into `app/main.py`, following the exact existing pattern from `explanation_routes.py` (503 if not configured, 502 if the safety filter blocks the reply, explicit `safety_failures` never hidden).
- Rewrote `scripts/autogen_agentchat_team.py` to support both `--mode deterministic` (unchanged behaviour) and `--mode chat --question "..."` (the real agent).
- Merged AutoGen and `httpx` into the main `requirements.txt`, since AutoGen is now load-bearing rather than optional; `requirements-autogen.txt` is kept as a redundant reference, not deleted, in case anything still points at it.

### Testing approach and its limits

AutoGen ships its own test infrastructure, `ReplayChatCompletionClient`, which drives a real `AssistantAgent` through a scripted sequence of model responses. This means the tool-calling path in the new tests (`tests/test_autogen_team.py`) is genuinely exercised -- the agent really does call the real Python evidence-lookup function and get a real return value back, including for a deliberately critical-vitals fixture case, and a deliberately unsafe scripted reply is genuinely caught by the post-hoc safety filter. What this cannot verify, and is not claimed to verify: how a real Azure OpenAI deployment actually behaves against the system prompt in practice (whether it reliably calls the tool, whether it reliably avoids forbidden phrasing on its own). No live Azure credential was available in this environment. This should be checked manually against a real deployment before relying on this for any demo -- and this exact limitation already applied to the pre-existing single-shot LLM Explanation Agent, which was never tested against a live model in this project either.

A genuine bug was found and fixed during this testing process: an early version of `test_evidence_dict_never_contains_retrospective_fields` did a naive substring search across the entire serialised evidence dict and flagged a false positive on the word "ktas_expert" appearing inside `MLPredictionResult.model_note`'s static, dataset-level disclaimer sentence ("...predicts KTAS_expert from public Kaggle data..."). That is prose naming the prediction target, not a leaked per-patient value. The test was rewritten to check dict *keys* structurally, matching how every other leakage test in this codebase already correctly works, rather than loosening the check to make the false alarm go away.

## Frontend restoration

- Restored all six tabs (Triage Review, Clinician Chat, Governance, Review Queue, Audit Log, Model Performance) that existed before the original KTAS migration, rewritten throughout for KTAS instead of MIMIC and for the current two-model registry shape (`best_ktas_model` / `best_emergency_model`) instead of the old acuity/admission shape.
- The Clinician Chat tab now calls the real AutoGen agent (`run_single_question`) instead of a direct Azure OpenAI call, and shows the same SAFETY_FAIL / NOT_CONFIGURED states as the API and CLI entry points, since all three share the same underlying function.
- The Governance tab's five-stage review gate evidence was rewritten for KTAS facts throughout (dataset name, leakage field list, AutoGen-specific controls) rather than copied with only the dataset name changed.
- Bumped the `streamlit` pin in `requirements.txt` from `1.35.0` to `1.58.0` after discovering, by downloading and inspecting both wheels directly, that `width='stretch'` (the modern non-deprecated parameter) does not exist in 1.35.0 -- `width` there is `int | None`, a pixel value. Using the modern parameter against the old pin would have been a real runtime error on a fresh install. `use_container_width=True` was kept throughout instead, since it works correctly (with only a non-fatal deprecation warning) on both the old and new pinned versions, and switching to `width=` would have required the version bump regardless.

### Testing approach

Streamlit ships its own real test infrastructure, `streamlit.testing.v1.AppTest`, which actually runs the script in a simulated session rather than just checking that it imports. `tests/test_frontend.py` uses this to confirm: the app runs with zero exceptions; real workflow data (not placeholder text) renders in the metrics; selecting the fixture's deliberately critical-vitals case renders the real `CRITICAL_PHYSIOLOGY_FLAGGED` status rather than a softened version of it; and clicking the actual "Save Review" button writes a real, correctly-shaped record to disk through the real UI interaction path. A small isolated fixture file (`tests/fixtures/sample_ktas_cases.jsonl`, two realistic cases derived from the real adapter's actual output shape) is used so these tests do not depend on the large pipeline-generated `data/processed/triage_cases_sample.jsonl` existing or being current.

## Net effect on test count

99 (original) + 8 (first review pass: leakage tripwire, vitals dedup) + 13 (AutoGen team) + 2 (chat route) + 13 (LLM safety filter) + 6 (frontend) = 141 tests, all passing, verified by actually running `pytest -q` against the final code, not assumed from individual file runs.

---

# 2026-06-16, continued: dual-pipeline confirmation, follow-up escalation feature, synthetic walkthrough

This session began by independently re-verifying every claim in this changelog (above) rather than trusting it -- installed the exact pinned AutoGen versions fresh in a new sandbox, re-ran the real `data.csv` through the real adapter, re-derived the Pain encoding cross-tab and the 1267-row/186-mistriage dataset facts directly from the CSV, and ran the full pre-existing test suite (141/141 passed independently). All of it held up; nothing from the prior session was thrown away or redone. One pre-existing issue was fixed in passing: the saved `.pkl` model files had been pickled with a newer scikit-learn (1.8.0) than this environment's installed version (1.5.1), producing an `InconsistentVersionWarning` on every load. Fixed by re-running `scripts/run_ktas_pipeline.py`, which retrained and re-saved the models against the environment's actual installed scikit-learn version.

## Architectural decision: KTAS + MIMIC dual-pipeline

Dylan confirmed (after the alternative of one merged pipeline was presented and rejected) that KTAS and the still-pending MIMIC-IV-ED dataset should share one overall system -- the same agent architecture, AutoGen orchestration pattern, Streamlit shell, safety review layer, leakage guard, audit trail, and governance logic -- while each dataset keeps its own adapter, feature set, model registry, label semantics, evaluation report, and model card, never merged or relabelled as the other. This is documented in full, with every cited file path independently checked to actually exist, in the new `docs/DUAL_PIPELINE_ARCHITECTURE.md`. No MIMIC adapter exists yet (access is still pending); this document is the confirmed design for when it does.

## New feature: same-patient follow-up escalation comparison

Dylan's boss asked: "for the same patient id, if vitals change, the new assignment should be escalation with a note explaining the cause; or if the agent is unsure, a note explaining that clinician intervention is required." Before building this, the actual `data.csv` was checked directly: the public KTAS dataset has no real patient identifier linking separate rows to the same person (`subject_id` in this project's adapter output is a synthetic per-row identifier, not a field from the source CSV). Building automatic "same patient" detection from demographics would mean inventing an identity-matching result with no ground truth anywhere to check it against -- exactly the failure mode this project's leakage guard and governance charter exist to prevent, applied to patient identity instead of a vital sign. Dylan confirmed this reasoning and the resulting design: an explicit, user-declared link ("stay B is a follow-up to stay A"), never automatic matching.

Added:

- `app/schemas/followup.py` -- `FollowUpLinkRequest`, `VitalDelta`, `FollowUpComparisonResult`. Extensive module docstring records why no automatic matching is implemented, so a future editor does not "fix" this by adding it.
- `app/agents/followup_comparison_agent.py` -- the deterministic comparison logic. Calls no rules engine, ML model, or LLM of its own; only reads two already-computed `WorkflowResult` objects.
- `app/storage/followup_repository.py` -- append-only JSONL storage, same pattern as `human_review_repository.py`.
- `app/api/followup_routes.py` -- `POST /followup/link`, `GET /followup/history/{stay_id}`, wired into `app/main.py`.
- A seventh Streamlit tab ("🔄 Follow-Up Comparison"), inserted between Triage Review and Clinician Chat, with an explicit on-screen warning that this is a demonstration capability, not automatic patient matching.
- `scripts/run_synthetic_walkthrough.py` -- per the boss's separate request to "populate test cases for each of the triage indicators... a full walkthrough across all possible scenarios," this runs eight hand-constructed scenarios (one per distinct deterministic status the system can produce, plus three follow-up scenarios including the exact "vitals worsen on return visit" case the boss described) through the real, unmodified orchestrator and comparison agent. Every constructed case is tagged `source_dataset="SYNTHETIC_WALKTHROUGH_CASE"` so it can never be mistaken for real data anywhere downstream (this label is load-bearing: the leakage guard and every governance report key off `source_dataset`).

### A real bug found and fixed during this work (not a pre-existing one)

While manually testing the comparison agent against a constructed "patient deteriorates on return visit" scenario (fever 36.7°C -> 38.9°C, alongside critically worsening heart rate, respiratory rate, SpO2, and blood pressure), the temperature delta was incorrectly reported as `IMPROVED`. Root cause, found by direct investigation rather than guessing:

1. First implementation attempt used a generic `_band(value, crit_low, crit_high, conc_low, conc_high)` helper with uniform strict `<` / `>` comparisons, with the four threshold numbers per vital copied by reading `manchester_engine.py`'s source. This looked rigorous but was still wrong, because the real engine mixes `<`, `<=`, `>`, and `>=` inconsistently across different checks (e.g. concern respiratory rate is inclusive on both ends, `25 <= x <= 29`; critical fever is `>= 41.0` inclusive; critical hypoxia is `< 90` exclusive). A single generic comparison shape cannot represent that, and silently produced wrong band boundaries at several points.
2. The actual fix: called the real `app.rules.manchester_engine._critical_vital_flags` and `._concern_vital_flags` functions directly, swept every vital across its boundary values, and used the resulting empirical (field, value) -> band table to write per-field `if`/`elif` chains whose individual operators were chosen one at a time to reproduce that table exactly. Verified against 43 boundary points across all five vitals (`o2sat`, `resprate`, `heartrate`, `sbp`, `temperature`).
3. Separately, the `direction` field's fallback logic for same-band-rank cases previously used `abs(new_value - 70)` as a distance-from-70 heuristic, which is meaningless for Celsius body temperature (or any vital where 70 is not a relevant reference point) and produced the false `IMPROVED` label in the case that surfaced this. Removed entirely; same-band-rank cases now honestly report `UNCHANGED`, since `clinically_notable` (which is what actually drives `escalation_detected`) was already correctly derived from the band transition regardless of what the `direction` text said.

`tests/test_followup_comparison_agent.py::TestBandMatchesLiveEngine` re-derives the same sweep from the live engine functions at test-run time (not from a frozen copy of the numbers), so this class of bug -- the comparison agent silently disagreeing with the engine it is supposed to be explaining -- will be caught automatically if the engine's thresholds are ever changed without updating this file to match.

## New tests added

- `tests/test_followup_comparison_agent.py` (new file, 61 tests): the live-engine threshold sweep described above; `_compare_vital` edge cases (both missing, newly missing, newly available, normal-to-critical, critical-to-normal, same-band-different-value, the temperature bug case specifically); full `compare_follow_up` scenarios (a real worsening scenario detects escalation, a stable scenario does not, the demonstration-flag text appears/does not appear correctly, the comparison never produces its own `category`/`priority` field, `requires_clinician_review` is always `True`).
- `tests/test_frontend.py`: added `TestFollowUpComparisonTab::test_linking_stable_to_critical_stay_detects_real_escalation`, which clicks the real "Compare" button via `AppTest` (Streamlit's own simulated-session test framework, not a mock) using the fixture's stay 1 (mild) and stay 2 (the fixture's existing deliberately-critical case) and confirms the real stored JSONL record and the real rendered metric both show the real escalation result. Also fixed a pre-existing, misleadingly-named test (`test_six_tabs_present_and_titles_correct`) that only ever asserted the page title and would have passed even if a tab were silently removed -- renamed to `test_seven_tabs_present_and_titles_correct` and rewritten to genuinely check `len(at.tabs)` and every tab's label via `AppTest`'s real tab collection.

## Independently verified, found correct, no change needed (this session)

- The dataset's core facts cited in the prior changelog entries (1267 rows; 186 mistriage split 131 under-triage / 55 over-triage; Pain=1/0 encoding cross-tabulated against `NRS_pain`, 711/714 valid scores at mean 4.1) were all re-derived independently from the raw CSV in a fresh sandbox and matched exactly.
- The specific, non-obvious AutoGen import-path fact (`AzureOpenAIChatCompletionClient` lives in `autogen_ext.models.openai`, not `autogen_ext.models.azure`, which holds a different class for Azure AI Foundry) was reproduced directly by attempting both imports against the real installed package.
- The leakage boundary was re-checked not just at the schema level but by running the real `dataframe_to_cases()` adapter on real CSV rows and confirming `ktas_expert` / `mistriage` land only in `RetrospectiveLabels`, never in `TriageTimeInput`.
- `scripts/azure_preflight_check.py` returns `PASS` in a fresh environment with the exact pinned AutoGen versions installed.

## Open items still requiring a decision (not resolved in this session)

1. The MIMIC-IV-ED adapter itself is not built -- access is still pending PhysioNet approval. `docs/DUAL_PIPELINE_ARCHITECTURE.md` documents the confirmed design for when it is built, but no MIMIC-specific code exists yet in this workspace.
2. Whether the eight scenarios in `scripts/run_synthetic_walkthrough.py` are the complete set the boss meant by "each of the triage indicators," or whether she wants additional scenarios per individual KTAS complaint/discriminator pathway specifically (the current eight cover every distinct deterministic *status* the system can produce, plus the three follow-up cases, but not every individual complaint-pathway keyword match).
3. The Cathal Flatley reference app (`https://portfolioproject-1525124.streamlit.app`) could not be reviewed -- it blocks automated access (`robots.txt` disallow) and no public information about it was found via search. Dylan confirmed proceeding without it, using this project's own UI/governance requirements instead, and will send screenshots later if any specific layout detail needs to be matched.

---

# Session: MIMIC-IV-ED Demo adapter, third-party review fixes, triage indicator matrix, real multi-agent AutoGen team, assessment card UI

This session resolves open item 1 above (the MIMIC-IV-ED Demo adapter is now built, on the publicly-available v2.2 demo dataset -- the full credentialed dataset is still pending PhysioNet approval and remains a separate, future step) and open item 2 (the triage indicator matrix below covers every individual vital threshold direction and every individual complaint pathway, derived from live engine execution rather than guessed).

## MIMIC-IV-ED Demo v2.2 adapter (new)

- Dylan supplied the real, official PhysioNet MIMIC-IV-ED Demo v2.2 zip. Every file's SHA256 checksum was independently recomputed and matched against the dataset's own `SHA256SUMS.txt` before any code was written against it.
- Inspected the real six tables (`diagnosis`, `edstays`, `medrecon`, `pyxis`, `triage`, `vitalsign`) directly -- columns, row counts, dtypes, missingness, and sample values -- before writing the adapter, per explicit instruction not to assume schema from documentation.
- Found, by direct inspection rather than assumption, that `vitalsign.csv` contains **repeated in-ED monitoring** (up to 22 readings for one stay, taken after `intime`, sometimes hours later across 1038 rows spanning only 206 distinct `stay_id`s out of 222 total stays) -- not a single triage-time snapshot. This is deliberately **excluded** from triage-time input for that reason; `triage.csv` (222 rows, confirmed 1:1 with `edstays`, zero orphans either direction) is used as the triage-time vitals source instead.
- Found two real data-quality issues by direct inspection, neither silently corrected:
  - `triage.temperature` has a minimum value of 36.5, implausible as Fahrenheit (would indicate severe hypothermia) but plausible as a single Celsius data-entry error in the source CSV. Kept as-recorded; flagged in the audit report.
  - `triage.pain` contains genuine 0-10 numeric scores plus an out-of-range `"13"` and non-numeric junk (`"unable"`, `"UA"`, `"Critical"`, `"o"`, `"uta"`, `"ett"`). `_parse_pain()` treats anything outside a valid 0-10 numeric as unparseable/missing, never coerced.
- Added `app/data_pipeline/mimic_adapter.py`: `SOURCE_DATASET_LABEL="MIMIC-IV-ED-Demo-v2.2"`, `load_mimic_table`, `validate_mimic_tables`, `dataframe_to_cases`, `load_mimic_demo_cases`. Reuses the existing `app/schemas/internal.py` classes (`EDStaySource`, `TriageSource`, `VitalSignRecord`, `DiagnosisRecord`, `MedReconRecord`, `PyxisRecord`, `EDTriageCase`) and the existing `app/schemas/mimic_ed.py` leakage classification, both of which were found to already exactly match the real MIMIC shapes with zero changes needed.
- Verified end to end on real data: built all 222 real cases, confirmed `acuity`/`disposition`/`outtime` are absent from `to_triage_time_input()`'s output (checked independently against the schema's own `to_triage_time_input()` source, not just trusted by comment), then ran 5 real MIMIC cases through the completely **unmodified** `app/agents/orchestrator.py::run_workflow()` with zero MIMIC-specific code -- confirming the dual-pipeline shared-architecture promise in `docs/DUAL_PIPELINE_ARCHITECTURE.md` holds in practice, not just on paper.
- Added `scripts/audit_mimic_demo.py`: full audit report (tables, columns, row counts, missingness, explicit triage-time-safe vs. retrospective field classification, known data-quality findings). Caught and fixed a real bug in this script during its own first run: `vitalsign.*` vital-sign columns (e.g. `vitalsign.temperature`) were initially misclassified as `TRIAGE_TIME_SAFE` because the generic column-name check matched against `TRIAGE_INPUT_COLUMNS` (which contains names like `"temperature"` that legitimately belong to `triage.csv`) before the table-name check ran. Fixed by checking the table name first, unconditionally, regardless of column-name match.
- New tests: `tests/test_mimic_adapter.py` (17 tests, including a direct leakage-boundary check and a real-`run_workflow()` check) and `tests/test_audit_mimic_demo.py` (6 tests, including a specific regression guard for the `vitalsign` misclassification bug above).
- Real data placed at `data/raw/mimic-iv-ed-demo/2.2/ed/`.

## Bug fixes from a second round of third-party code review (every claim independently reproduced before fixing, none trusted at face value)

1. **Temperature unit mislabeling in LLM evidence package** (`app/agents/orchestrator.py`): the evidence package sent to the LLM Explanation Agent previously used the key `"temperature_F"` unconditionally, even for KTAS cases recorded in Celsius. Reproduced live (a Celsius value was mislabeled as Fahrenheit), then fixed to send `temperature`, `temperature_unit`, and a pre-computed `temperature_c` via the existing shared `app/rules/vitals.py::temperature_c()` utility. New tests: `tests/test_orchestrator.py::TestLlmEvidencePackageTemperatureLabelling` (2 tests, one per unit).
2. **Missing-field extraction bug** (`app/api/explanation_routes.py`): `_extract_missing_fields()` searched for a `"MISSING_TRIAGE_FIELD:"` flag prefix that the safety review agent never actually produces (it produces `"MISSING_CRITICAL_VITAL:{field}"` / `"MISSING_CHIEF_COMPLAINT"`). Reproduced live -- a case genuinely missing `o2sat` returned an empty `missing_required_fields` list. Fixed to read `data_validation.missing_required_fields` directly, which the Data Validation Agent already computes correctly. New file `tests/test_explanation_routes.py` (6 tests; this route had no tests before).
3. **Wrong settings attribute in the demo-download script** (`scripts/download_mimic_ed_demo.py`): used `settings.raw_ed_dir` (the full-dataset path) instead of `settings.raw_demo_dir` (the demo path). Fixed.
4. **Stale hardcoded test count** in the Governance tab (`frontend/app.py`): replaced `"pytest (135+ tests as of the last review pass)"` with a pointer to running `pytest` directly and to this changelog for history, so the string cannot go stale again the same way.
5. **Governance verdict frontend/backend mismatch** (`frontend/app.py`): the tab always hardcoded `NOT_READY_FOR_CLINICAL_USE` as the overall verdict regardless of its own `unreviewed_missing` computation just above it -- the same computation the backend's `app/api/governance_routes.py` already correctly uses to distinguish `READY_FOR_RESEARCH_DEMO_ONLY` from `NOT_READY_FOR_CLINICAL_USE`. Fixed by splitting into two independent, correctly-scoped displays: research-demo readiness (genuinely dynamic, based on `unreviewed_missing` and the presence of `schema_report.json`) and clinical-use readiness (correctly always `NOT_READY`, since that does not depend on review completeness). New tests: `tests/test_frontend.py::TestGovernanceTabDynamicVerdict` (3 tests, covering both the ready and not-ready branches explicitly) -- writing these tests caught and fixed two mistakes in the tests' own fixture setup (a missing `schema_report.json` file made both branches look correct for the wrong reason).
6. **Overstated "Multi-agent...orchestrated with AutoGen" wording** (`frontend/app.py` caption): reworded to "deterministic data validation, safety checks, and ML research estimates, plus an AutoGen-based explanation/chat layer," since AutoGen does not orchestrate the deterministic pipeline.
7. **Ambiguous "Safety review: no flags" wording**: reworded to "No deterministic vital-sign safety flags detected" to avoid implying a broader patient-safety guarantee than the deterministic vital-sign check actually provides.
8. **SpO2 missingness presented without context** (Review Queue tab): added a dynamically-computed (not hardcoded) note -- on real production data, 665 of 706 queued cases (94%) are missing *only* `o2sat` -- labelling this a known limitation of the public KTAS dataset (Choi et al.), not an individual-case review burden, so the queue does not read as if each of those 665 cases independently needs the same depth of attention as a case missing multiple vitals or a chief complaint.

## Safety banner removed (explicit, direct user instruction)

Dylan explicitly instructed removal of the top yellow "NOT FOR CLINICAL USE -- Research Prototype Only" banner, quoting back an earlier refusal. This is recorded here as a direct, explicit user decision -- different from passively following an unverified second-LLM suggestion, which is exactly why it had been refused up to that point. Removed from `frontend/app.py`. Verified via a real `AppTest` run: zero exceptions, the banner block is genuinely gone, and all other safety wording (page title, Governance tab, footer, error messages, the `NOT_FOR_CLINICAL_USE` text throughout) remains intact and untouched.

## `workflow_action` field (new, addresses "reassign triage assignments" request without any new clinical decision)

Added an explicit `workflow_action` field (`ESCALATION_REQUIRED` / `CLINICIAN_INTERVENTION_REQUIRED` / `NO_ESCALATION_DETECTED`) in two places:

- `FollowUpComparisonResult.workflow_action`, derived purely from the already-computed `escalation_detected` and `new_classification_status` (precedence: a new-visit status of `REQUIRES_CLINICIAN_REVIEW` always reports `CLINICIAN_INTERVENTION_REQUIRED`, even if it would also count as "escalated" by rank, since the actionable issue is "go get the missing data," not "this patient is deteriorating"). Computed in `app/agents/followup_comparison_agent.py`, wired into a 4th metric column and the history table in the Follow-Up Comparison tab. 5 new tests in `tests/test_followup_comparison_agent.py::TestWorkflowAction` -- writing these caught and fixed a real class-structure bug introduced during the edit itself (a `str_replace` accidentally merged `TestWorkflowAction` and the pre-existing `TestCompareFollowUp` into one class by deleting the latter's `class` declaration; caught by reading the test file's class list directly, fixed by reinserting the missing declaration, then re-confirmed all 66 tests in the file run as two genuinely separate classes).
- `WorkflowResult.workflow_action` (the single-run equivalent, for the new assessment card below), derived the same way from `decision.classification_status` and `safety_review.is_safe_to_present`. Computed in `app/agents/orchestrator.py::run_workflow()`. 6 new tests in `tests/test_orchestrator.py::TestWorkflowResultWorkflowAction`.

Neither field is an independent decision -- both are pure labels over values already computed elsewhere, and neither overrides `requires_clinician_review`, which stays `True` regardless.

**Later update (see the MIMIC model-gating review-response section below):** `WorkflowResult.workflow_action`'s third value was renamed from `NO_ESCALATION_DETECTED` to `NO_CRITICAL_PHYSIOLOGY_FLAGGED` -- found, during a later review pass, to read in its green-badge UI rendering like a positive clinical safety verdict when the underlying fact is usually an absence of capability (no approved Manchester ruleset exists), not a finding. `FollowUpComparisonResult.workflow_action`'s `NO_ESCALATION_DETECTED` value (the field described in the first bullet above) was deliberately left unchanged, since it answers a narrower, accurate question (did this specific two-visit comparison detect an escalation) where the original wording is not an overclaim. See that later section for the full reasoning.

## Triage indicator test matrix (new, 29 distinct indicators, built only from reason codes confirmed to actually exist)

Per the explicit instruction: "first confirm the exact dispatch logic in `manchester_engine.py` before writing assertions against pathway-specific reason codes. Do not guess reason codes... If a pathway is disabled because `mts_pathway_enabled()` is false, the expected output should clearly say the pathway was detected but no Manchester category was assigned."

Read the full real dispatch logic in `app/rules/manchester_engine.py` (`run_manchester_engine`, `_select_pathway`, `_PATHWAYS`, `_safety_alert`, `_awaiting_ruleset`, `_make_decision`) end to end before writing a single assertion. Two real, non-obvious facts were found this way, not guessed:

1. When no approved ruleset is registered, the eleven pathway functions (`_pathway_chest_pain`, `_pathway_cardiac_arrest`, etc.) are **never called at all**. `run_manchester_engine` builds the gated reason code itself, generically, as `f"PATHWAY_MATCHED_{fn.__name__.replace('_pathway_', '').upper()}"` -- so every pathway's gated reason code has the exact same shape, derived purely from the Python function name, confirmed by running this derivation directly against the real `_PATHWAYS` list and cross-checking against live engine output for all 11 pathways.
2. An **unrecognised** complaint with a concern-level vital produces `AWAITING_APPROVED_CLINICAL_RULESET` (codes: `UNRECOGNISED_COMPLAINT` + the concern flag(s) + `MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET`). A **matched** pathway with a concern-level vital instead produces `PHYSIOLOGY_CONCERN_FLAGGED` (codes: `PATHWAY_MATCHED_X` + the concern flag(s), with no disabled-ruleset suffix at all). These are genuinely different branches in the dispatcher, not the same behaviour with different labels -- verified by direct execution of both cases.

Added `tests/test_triage_indicator_matrix.py` (45 tests: 30 parametrized indicator rows covering every individual vital threshold direction for all 5 vitals -- critical/concern x low/high, including several deliberately-asymmetric bands like resprate/heartrate having no low-side concern band, only a critical-low cutoff -- plus 3 missing-data rows, all 11 real pathway-match rows from `_PATHWAYS`, the unrecognised-complaint row, and one row for a matched pathway co-occurring with a concern-level vital -- a genuinely distinct dispatch branch from every other pathway row, documented in this file's own module docstring but not turned into its own checked row until a later review pass flagged the 29-vs-30 count mismatch and the omission was fixed properly rather than just renamed away; 12 real pathway-match rows now feeding that same check (the 11 original plus the new interaction row), for 13 explicit category-never-assigned safety checks total (the 12 pathway rows plus the pre-existing unrecognised-complaint row, which also has `category=None`); 2 tests confirming missing vitals are correctly surfaced through the broader orchestrator/safety-review path rather than the Manchester engine itself). Every expected `(status, reason_codes)` pair was obtained by directly calling `run_manchester_engine()` and reading its real output. Added `scripts/run_triage_indicator_matrix.py`, which prints the requested readable `Indicator | Expected | Actual | Pass/Fail` table and saves the same data to `data/processed/triage_indicator_matrix_log.json`. All 30 indicators pass.

## Real multi-agent AutoGen team (new): IntakeAgent, ValidationAgent, SafetyReviewAgent, ExplanationAgent

Built `app/agents/autogen_multi_agent_team.py`, **additive to** (not replacing) the existing, working single-`AssistantAgent` `app/agents/autogen_team.py`. Uses a real `RoundRobinGroupChat` (confirmed importable and its constructor signature confirmed directly against the installed `autogen-agentchat==0.7.5` package before writing any code against it) with `TextMentionTermination("TERMINATE") | MaxMessageTermination(max_messages=12)`.

Design, matching the requested strict safety boundary ("AutoGen coordinates and explains only... must not assign Manchester triage, modify vitals, diagnose, recommend treatment, or override clinician review"):

- All four agents share the **exact same single tool** (`get_verified_evidence_for_stay`, reusing the already-tested `_make_evidence_tool` / `_build_evidence_dict` from `autogen_team.py`) -- one source of truth, not four independently-fallible ones.
- Each system message **narrows** the agent's role rather than widening its authority. `IntakeAgent`: facts only. `ValidationAgent`: data completeness only. `SafetyReviewAgent`: explicitly told "you do not perform a safety review yourself... your only job is to accurately RESTATE that already-completed review," to prevent the agent's name from misleading the underlying model into deciding something itself. `ExplanationAgent`: synthesises the previous three agents' turns under the same nine strict rules as the existing single-agent `CHAT_SYSTEM_MESSAGE`, and is instructed to always end with `TERMINATE`.
- The final `ExplanationAgent` message is run through the **same shared** `app/rules/llm_safety_filter.py::check_forbidden_phrases()` core already used by both the single-shot LLM Explanation Agent and the single-agent chat path -- this is the third LLM-facing surface in the project now, and it cannot diverge from the other two on what counts as a forbidden phrase.
- Degrades to a clear `NOT_CONFIGURED` result if Azure OpenAI is not configured, exactly like every other LLM-facing entry point in this project; never crashes the caller and never falls back to inventing an explanation locally (confirmed via a deliberately-broken team builder in the test suite).

Verified live, before writing any formal test, with a fully scripted `ReplayChatCompletionClient` conversation (real AutoGen test client, not a custom mock): all four agents genuinely ran in round-robin order, each one genuinely called the real evidence tool and received real fixture data back (confirmed via `FunctionExecutionResult` content, not assumed), the round-robin correctly terminated on the `TextMentionTermination("TERMINATE")` condition, and messages were correctly attributed by `.source`.

New tests: `tests/test_autogen_multi_agent_team.py` (22 tests) -- static checks that all four system prompts genuinely contain the required safety language (including the `SafetyReviewAgent`-specific "already happened" disclaimer); confirmation the team has exactly four participants with the correct names and that all four share the same single tool (checked via the real `team._participants` / `agent._tools` internal attributes); a fully scripted four-agent conversation proving genuine round-robin order and real tool calls; `run_team_explanation()` tests covering `NOT_CONFIGURED`, a full `PASS` scenario, the initial `user` task message correctly excluded from `agent_turns`, an unsafe final explanation correctly caught by the safety filter, and a team-run exception correctly not crashing the caller; safety-filter unit tests mirroring the single-agent path's equivalents.

Wired into a new API route `/chat/team-explanation` in `app/api/chat_routes.py` (mirroring the existing `/chat/ask` route's exact 503/502/200 error-handling shape), with 4 new route-level tests in `tests/test_chat_routes.py::TestTeamExplanationRoute`. Wired into the Streamlit UI as a new, clearly-labelled, additive section within the existing Clinician Chat tab (a "Run multi-agent team explanation" button, gated behind the same `azure_configured` check as the existing free-form chat) -- the existing single-agent chat remains untouched and fully functional alongside it, confirmed both coexist correctly per direct reconfirmation with Dylan.

### A genuine pre-existing Streamlit-testing-harness limitation found and documented while testing the new UI button (not an application bug)

While writing tests for the new button's `azure_configured=True` render path, every attempt -- using `pytest`'s own `monkeypatch.setattr("frontend.app.X", ...)`, a bare module-attribute assignment, and every variant in between -- triggered a `StreamlitAPIException: Forms cannot be nested in other forms` failure at the pre-existing review-submission form in the Triage Review tab (`frontend/app.py` line 429), a tab entirely unrelated to the change being tested. This was investigated by direct bisection of the real file (not guessed at) and conclusively shown to be a **pre-existing Streamlit 1.56.0 / `AppTest` testing-harness limitation, not a real application bug**:

- Setting genuine `AZURE_OPENAI_*` environment variables (the real-world mechanism this branch is actually keyed on) and running the same file through `AppTest` produces **zero exceptions**.
- The exact same failure reproduces with the entire Clinician Chat tab body replaced by a single `st.write()` stub, proving it is not caused by anything this session's new code, or even the pre-existing single-agent chat code, actually does.
- The existing test suite, before this session, had **never once** tested `frontend/app.py`'s `azure_configured=True` branch via `AppTest` at all -- every prior chat-tab test only exercises the degraded/not-configured state. This is a pre-existing gap in this project's test coverage that simply never surfaced before, not a regression introduced by this session's work.

Rather than work around this silently, `tests/test_frontend.py::TestMultiAgentTeamButtonInChatTab`'s docstring records the full investigation, and its three tests verify what `AppTest` can reliably check in this Streamlit version (the not-configured state correctly suppresses the new section entirely; the new section's source is structurally and correctly gated behind the same `azure_configured` check as the existing chat input, confirmed by source-position checks; the button is genuinely wired to the real `run_team_explanation` function). The underlying `run_team_explanation()` function and the `/chat/team-explanation` API route are both already fully tested via paths that do not depend on `AppTest` rendering the configured branch (see above), so this gap is specifically about full-page UI-render testing of that one branch, not about the underlying logic being untested.

## Trial Matcher-style assessment card (new)

Per the explicit request -- "Each case should show: assessment status, research model output, evidence used, matched indicators/reason codes, missing information, uncertainty, workflow action, clinician review requirement, audit/log reference" -- added `render_assessment_card()` to `frontend/app.py`, called at the top of the Triage Review tab, replacing the previous scattered 4-metric row. Every one of the nine requested fields is read directly from the already-computed `WorkflowResult`; the function adds no new decision logic, only a unified layout for what already existed:

1. **Assessment status** -- `decision.classification_status` and `decision.category` (or the explicit "no Manchester category assigned" wording when `category is None`).
2. **Research model output** -- `ml_prediction.predicted_ktas_class`, `model_name`/`model_version`, `emergency_research_estimate`.
3. **Evidence used** -- the triage-time vitals row, sourced from `triage_input` only (never retrospective fields).
4. **Matched indicators / reason codes** -- `decision.reason_codes`, displayed verbatim as code spans.
5. **Missing information** -- `data_validation.missing_required_fields` and `non_informative_fields`, plus an explicit red alert if `safety_review.is_safe_to_present` is `False`.
6. **Uncertainty** -- `ml_prediction.top_class_confidence`, with an explicit "research-grade model confidence, not a clinical probability" caption.
7. **Workflow action** -- the new `WorkflowResult.workflow_action` field above, shown with a colour-coded badge (🔴 escalation / 🟡 clinician intervention / 🟢 no escalation).
8. **Clinician review requirement** -- `decision.requires_clinician_review`, always shown as "🔒 Always required."
9. **Audit / log reference** -- `audit.workflow_version`, `run_start_utc`, `run_end_utc`, plus a pointer to the full JSON output expander and the Audit Log tab.

The pre-existing detailed "Clinical Safety Assessment" and "ML Research Estimate" sections remain below the card, reworded as "-- full detail" drill-downs rather than appearing as unexplained duplication of the card above them. A genuine bug introduced while restructuring this section (a leftover dangling `),` / `)` from the removed old 4-metric block, which would have been a Python syntax error) was caught by an `ast.parse()` check before any test was run, and fixed immediately. The existing `test_real_case_data_renders_in_metrics` test (which checks for a real `"Stay ID"` metric) initially failed after the restructure because the new card used a markdown header instead of a real `st.metric` for the stay ID -- fixed by adding a genuine `st.metric("Stay ID", ...)` to the card's top row rather than weakening the test. Verified via real `AppTest` runs against both a clean case (showing `NO_ESCALATION_DETECTED`) and the project's existing critical-vitals fixture case (showing `🔴 ESCALATION_REQUIRED` correctly derived even though the underlying `classification_status` is the more benign-sounding `AWAITING_APPROVED_CLINICAL_RULESET`, because `safety_review.is_safe_to_present` is `False` due to a missing critical vital -- confirming the card surfaces the genuinely safety-relevant signal even when the headline status alone would not convey urgency).

## Follow-Up Comparison feature: confirmed kept, unmodified in scope

Per explicit instruction ("Also keep the Follow-Up Comparison feature, since that directly answers Meghana's same-patient escalation request"), this feature was not removed, narrowed, or otherwise changed in scope this session -- only extended with the new `workflow_action` field described above, which is purely additive to its existing output.

## New test count

285 passing before the multi-agent team and assessment card work in this session; 320 passing after all of this session's work (226 after the MIMIC adapter and bug fixes alone; 242 after the SpO2 note and `workflow_action`/follow-up additions; 285 after the triage indicator matrix; 307 after the multi-agent team module and its route; 317 after `WorkflowResult.workflow_action` and the assessment card; 320 after the multi-agent-button UI tests, once correctly scoped around the documented `AppTest` limitation above).

## Open items still requiring a decision (carried forward from this session)

1. MIMIC-specific ML work (label-builder script, feature engineering, model registry keys analogous to KTAS's `best_mimic_model`) has not been started -- only the adapter and audit script exist for MIMIC so far.
2. Minor lower-priority items from the second review round not yet acted on: an audit-log human-readable-summary-before-raw-JSON improvement, and an "ML Model Performance" -> "ML Research Baseline Performance" heading rename.
3. The full, credentialed MIMIC-IV-ED dataset (as opposed to the public demo used this session) remains pending PhysioNet approval and is out of scope until that approval arrives.

---

# Second external review response, and MIMIC cases made visible in the Streamlit UI

This section covers a second-round response to external review of the work above, followed by the explicit, direct user request ("I want to be able to see the mimic data in the app") that the previous session's open items had flagged as still missing. Per instruction, every claim in the review was independently re-verified against the real code before any fix was made -- some were confirmed and fixed, one was investigated and rejected as unsubstantiated, and the MIMIC-in-the-UI work surfaced two further real bugs along the way that had nothing to do with MIMIC specifically.

## Review claims verified and fixed

- **`scripts/build_sample_cases.py --dataset demo` used a legacy path, not the real MIMIC adapter.** Confirmed by direct reproduction, more serious than the review's framing: every MIMIC case built this way was mislabelled `source_dataset: "Kaggle-KTAS"`, which was proven (not assumed) to silently defeat `app/agents/followup_comparison_agent.py`'s cross-dataset consistency warning -- linking a real KTAS stay to a real MIMIC stay produced no mismatch warning at all before the fix, and a real, correct one after. Fixed by routing `--dataset demo` directly through `load_mimic_demo_cases()`; the legacy `loaders`/`validation`/`mapping` path is now reachable only for the full (not-yet-credentialed) MIMIC dataset, and is documented as needing its own `source_dataset` re-verification before that day comes. New `tests/test_build_sample_cases.py` (3 tests). One test originally written for this (a subprocess end-to-end run) was found, after writing it, to destructively overwrite the real `data/processed/triage_cases_sample.jsonl` as a side effect of "passing" -- removed rather than kept, and the real KTAS sample data was restored.
- **29-vs-30 indicator matrix count mismatch.** Resolved properly, not by renaming: added a genuine 30th row to `tests/test_triage_indicator_matrix.py` for the matched-pathway-plus-concern-vital interaction (verified live: `chiefcomplaint="chest pain"` + `o2sat=92.0` -> `PHYSIOLOGY_CONCERN_FLAGGED`, codes `["PATHWAY_MATCHED_CHEST_PAIN", "CONCERN_SPO2_90_TO_94"]`, `category=None`) -- a genuinely distinct dispatch branch the module's own docstring already described in prose but had never turned into an automatically-checked row. `scripts/run_triage_indicator_matrix.py` now correctly prints "30 indicators, ALL PASS: True." Test count for this file: 45 (30 status-check rows + 13 category-never-assigned-check rows, the 12 pathway rows plus the pre-existing unrecognised-complaint row + 2 missing-vitals tests).
- **Dangling reference to a nonexistent `build_mimic_outcome_labels.py`** in `app/data_pipeline/mimic_adapter.py`'s docstring. Confirmed the file does not exist anywhere in the project; fixed the docstring to point only to the real `scripts/audit_mimic_demo.py`, with an explicit note that no MIMIC label-builder script exists yet.
- **Stale `run_manchester_engine` docstring.** Confirmed real: the docstring's old item 5 claimed both "missing chief complaint" and "no pathway match" produce `REQUIRES_CLINICIAN_REVIEW`, but live verification showed "no pathway match" (an unrecognised-but-present complaint) actually produces `AWAITING_APPROVED_CLINICAL_RULESET` -- the same status as a matched-but-gated pathway, just with a different reason-code shape (`UNRECOGNISED_COMPLAINT` vs `PATHWAY_MATCHED_X`). Rewrote the docstring with six clearly-separated branches matching verified real output, pointing to `tests/test_triage_indicator_matrix.py` as the source of truth rather than restating values that could drift again.
- **CORS wide open.** Confirmed live, not just read from the code: `allow_origins=["*"]` combined with `allow_credentials=True` causes FastAPI's `CORSMiddleware` to reflect back literally any `Origin` header sent, including a deliberately made-up one used to test it. Fixed with a new `app.config.settings.cors_allowed_origins`, defaulting to local-development-only origins (`localhost:8501`), overridable via a `CORS_ALLOWED_ORIGINS` environment variable for real deployments, never defaulting to a wildcard. New `tests/test_main_cors.py` (5 tests): confirms an arbitrary origin is rejected by default, the genuine local Streamlit origin still works, the environment-variable override works for a real deployment scenario, the override does not also open the door to other origins, and the default never contains a literal `"*"`.
- **Deployment docs too thin (21 lines).** Rewrote `infrastructure/azure_deploy.md` comprehensively: the two-service architecture (FastAPI backend + Streamlit frontend, confirmed via direct file reads that neither the `Dockerfile` nor `startup.sh` runs Streamlit at all), the real required/optional environment variables (confirmed against the actual `app/agents/autogen_team.py` source rather than guessed), the CORS setting above, and model/data artifact handling. Every place requiring Dylan's own Azure-specific decisions (resource names, Key Vault, App Service vs Container App, log retention) is marked `[DECISION NEEDED]` rather than filled with an invented placeholder. A fabricated README citation invented while drafting this document (a claimed "local/demo only" phrase that does not actually exist anywhere in `README.md`) was caught on re-read and corrected before finalising, citing the real, verified line instead.

## Review claim investigated and rejected

- **"Duplicate `'still required.''still required.'` text"** in `app/agents/autogen_multi_agent_team.py`. Read both real occurrences of the phrase "still required" in full context; neither contains a literal doubled substring within one message, and a project-wide grep for the exact doubled text found in the review found zero matches anywhere. Flagged back as unconfirmed and likely a formatting artifact in the review itself; no edit was made to text that was not actually broken.

## MIMIC cases made visible in the Streamlit UI (the explicit, direct user request)

`frontend/app.py`'s `load_cases()` previously read exactly one file, `triage_cases_sample.jsonl`, holding whichever dataset a script was last run against. Redesigned to load both datasets directly via their real adapters (`load_ktas_cases` + `load_mimic_demo_cases`), merged into one list, with KTAS still fatal-if-missing (the established default) and MIMIC degrading gracefully to KTAS-only via `FileNotFoundError` if the demo files have not been downloaded in a given environment.

Two real bugs, unrelated to the merge itself, were found and fixed along the way:

1. **`cases_path` `UnboundLocalError`.** Found while re-reading the Clinician Chat tab's code to wire in the merged dataset: `cases_path` (the file path the AutoGen evidence-lookup tool reads from) was previously assigned only inside the `if prompt := st.chat_input(...):` block, so clicking "Run multi-agent team explanation" as the very first action in a session -- without ever using free-form chat first -- raised `UnboundLocalError` and crashed the app. Reproduced directly by simulating the exact control-flow pattern before fixing. Fixed by moving the assignment to the top of the `else:` branch, defined once, shared unconditionally by both code paths. New regression test (`tests/test_frontend.py::TestMultiAgentTeamButtonInChatTab::test_cases_path_is_defined_unconditionally_not_only_inside_chat_input_block`) uses Python's `ast` module to statically confirm `cases_path` is a top-level assignment in the `else:` branch; verified the test genuinely catches the bug by temporarily reverting the fix and confirming the test fails with the correct error before restoring it.

2. **A broader Streamlit/`AppTest` testing-harness mechanism than previously documented.** A prior session had documented "`AppTest` cannot reliably render `azure_configured=True`" as the limitation. This session discovered the real, more general mechanism while attempting to write the merge's tests: **importing `frontend.app` for any reason at all -- a bare `import frontend.app`, or merely because a string-path `monkeypatch.setattr` needs to resolve/import that module to find an attribute -- causes its top-level `st.tabs()`/`st.set_page_config()` calls to execute once outside a real Streamlit script-run context, corrupting Streamlit's internal form-tracking state for the rest of the process.** Proven directly: monkeypatching a completely unrelated, trivial `frontend.app` helper function via the exact safe-looking string-path style (zero literal `import frontend.app` anywhere in the test) still triggers the identical "Forms cannot be nested in other forms" failure; monkeypatching anything inside `app.config` (a module with no top-level Streamlit calls) is always safe regardless of style. Practical conclusion: patching anything inside `frontend.app` itself is never safe in this Streamlit version (1.56.0); patching anything inside `app.config` is always safe.

   This broke the existing `isolated_processed_dir` test fixture's premise (it wrote to `triage_cases_sample.jsonl`, which the new `load_cases()` no longer reads at all). Fixed by introducing a `frontend_cases_override.jsonl` file mechanism inside `load_cases()` itself: if this file exists in `settings.processed_dir`, its contents are used directly and neither real adapter is called -- tests write this file via the safe `app.config.settings.processed_dir` monkeypatch pattern, never touching `frontend.app`. `isolated_processed_dir` and two inline fixture-writing blocks were updated to target this new file instead of `triage_cases_sample.jsonl`, with full docstrings explaining the discovery for future maintainers.

3. **A third bug, found while testing the override mechanism itself: a zero-argument `@st.cache_data` function never re-evaluates after its first real call in a process.** The first working version of the merged `load_cases()` cached the entire function with no arguments, reading `settings.processed_dir` from a closed-over global. Confirmed directly: Streamlit caches a zero-argument function's result after its very first call and returns that same result forever in that process, with zero awareness that `settings.processed_dir` might later point somewhere completely different. This silently poisoned test isolation -- an earlier test calling `load_cases()` against real, unpatched settings caused every *later* test in the same pytest process to see that same stale real result, even when the later test had correctly written and patched a `frontend_cases_override.jsonl` override of its own. (This would also have been a real production bug, not just a testing artifact: in a long-running deployed process, the cache would never refresh if MIMIC files were downloaded, or settings changed, after the very first call.) Fixed by splitting the function in two: `_load_cases_cached(override_path, override_mtime, ktas_path, demo_dir)` is the actual cacheable, side-effect-free computation, with every input that should invalidate the cache passed as an explicit parameter (including the override file's modification time, so rewriting the same path with different content -- as several tests do -- also correctly busts the cache); `load_cases()` itself is no longer cached and handles the `st.session_state`/file-write side effects that must run on every call, not just on a true cache miss (a related, smaller issue: the original version's side effects were themselves inside the cached function, so they would silently not run again on a cache *hit* either). New direct regression test (`test_override_takes_effect_even_after_an_earlier_unpatched_real_data_call`) deliberately reproduces the exact ordering that caused the bug inside a single test, rather than relying on incidental execution order; verified it genuinely fails against the original buggy zero-argument design by temporarily reverting the fix and confirming the failure, then restoring it.

`load_cases()` also now writes `streamlit_runtime_cases.jsonl` (a third, distinctly-named file -- deliberately not `triage_cases_sample.jsonl`, which `build_sample_cases.py` owns, and not `frontend_cases_override.jsonl`, the test-injection file above) so the AutoGen evidence-lookup tools, which read from a file path rather than the in-memory `records` list, can correctly find MIMIC cases too, not just whichever dataset `build_sample_cases.py` was last run against. Confirmed via real `AppTest` runs: 1267 KTAS + 222 MIMIC = 1489 total selectable cases, exactly matching the real on-disk row counts.

### Dataset filter control (the literal feature-completion step)

Confirmed that merely merging both datasets into `records` made MIMIC cases technically present but not practically usable -- finding one of 222 MIMIC cases meant scrolling past 1267 KTAS entries with no way to narrow the list. Added `render_dataset_filtered_case_selector()`, a shared helper used by both the Triage Review tab and the Clinician Chat tab (each with its own independent widget-key namespace), rendering a radio control ("All datasets (1489)" / "KTAS only (1267)" / "MIMIC demo only (222)", counts computed live from `records`, never hardcoded) above the case selectbox. Verified live: selecting "MIMIC demo only" narrows the dropdown to exactly 222 options, every one a genuine MIMIC-range `stay_id`; selecting a specific MIMIC case afterward runs the full deterministic triage workflow against it with zero exceptions. New `tests/test_frontend.py::TestDatasetFilterControl` (6 tests, run against real data specifically because the small test fixture is KTAS-only and cannot exercise the MIMIC-narrowing behaviour at all).

### Two stale/incomplete-scope text issues found and fixed as a consequence of MIMIC becoming genuinely visible

- The Governance tab's "1. Intake" stage evidence had hardcoded `"dataset": "Kaggle Emergency Service - KTAS Triage Application (public, 1267 rows)"` -- accurate when written, silently stale the moment MIMIC was merged elsewhere, since this tab never read `records` at all, only separately-generated JSON report files. Fixed to compute the description live from `records` (e.g. `"Kaggle-KTAS (public, 1267 rows) + MIMIC-IV-ED-Demo-v2.2 (public, 222 rows)"`), so it cannot go stale again regardless of which datasets are loaded or how many cases each contains. New `tests/test_frontend.py::TestGovernanceTabDatasetDescriptionIsDynamic` (2 tests: against real merged data, and against the KTAS-only fixture, confirming the dynamism works correctly in both directions, not just when MIMIC happens to be present).
- The Review Queue tab's caption ("Cases with missing triage data that require clinician attention") did not state that the underlying report (`missing_triage_inputs_report.json`, from `scripts/inspect_missing_triage_inputs.py`, confirmed by direct read to only ever process KTAS data) covers KTAS only -- with MIMIC now visible elsewhere via the new filter, a user could reasonably assume this queue covered both. Fixed to read the report's own self-documented `"dataset"` field live and state the scope explicitly ("Scope: **Kaggle-KTAS only** -- this report does not cover MIMIC demo cases..."), so this stays accurate automatically if the underlying script is ever extended to cover MIMIC too, rather than needing a second hardcoded edit. New `tests/test_frontend.py::TestReviewQueueCaptionClarifiesKtasOnlyScope` (3 tests, including a fresh-checkout case where the report does not exist at all).

## New test count

320 passing at the start of this section (carried forward from the prior session). **346 passing at the end**, confirmed by direct `pytest --collect-only` count. Getting from 320 to 346 by narrative running-total (the style used elsewhere in this changelog) initially produced two different wrong intermediate numbers while writing this entry -- both came from doing the addition by recollection rather than by actually summing the real, independently-verified per-class counts. Redone properly this time, by listing each genuinely new test group with its real count (confirmed via `pytest --collect-only` against the actual file/class, not recalled) and summing those programmatically rather than by hand: `tests/test_build_sample_cases.py` (3, entirely new file), the indicator-matrix 30th row (2, one status-check parametrization + one category-never-assigned-check parametrization, reflected in that file's own 43 -> 45 total above), `tests/test_main_cors.py` (5, entirely new file), `tests/test_frontend.py::TestLoadCasesMergesKtasAndMimic` (4, including the cache-poisoning regression test, entirely new class), the `cases_path` regression test added to the pre-existing `TestMultiAgentTeamButtonInChatTab` (1, that class going from 3 -> 4), `tests/test_frontend.py::TestDatasetFilterControl` (6, entirely new class), `tests/test_frontend.py::TestGovernanceTabDatasetDescriptionIsDynamic` (2, entirely new class), `tests/test_frontend.py::TestReviewQueueCaptionClarifiesKtasOnlyScope` (3, entirely new class). These sum to exactly 26, and 320 + 26 = 346, matching the directly-measured total precisely.

## What was NOT done in this section, stated explicitly so it is not mistaken for complete

- The Follow-Up Comparison tab's stay-id pickers (`tab_followup`) remain a plain sorted list of bare integers covering both datasets already (no separate fix was needed there, since it never filtered by dataset to begin with), but did not receive the same chief-complaint-labelled, dataset-filterable treatment as the other two selectors -- finding two specific MIMIC `stay_id`s in that list still means scanning 1489 plain numbers. Lower priority than the other fixes in this section since the tab's own warning text already makes clear this is a manual declaration step, not a browse-and-pick step, and the request that drove this section ("see the MIMIC data") is more directly served by the other two tabs' new filters.
- MIMIC-specific ML work remains not started (carried forward, unchanged, from the prior session's open items above).
- No new zip has been built or delivered as part of this section; the most recently delivered zip predates every fix described above.



## Second review round, continued: items completed, plus a serious test-isolation bug found and fixed

Test suite: 346 (start of round) → 414 (end of round), all passing, confirmed via 3 repeated full-suite runs with no flakiness.

### Completed and verified this round

- **#1 (CRITICAL — KTAS model applied to MIMIC cases)**: `run_ml_prediction()` now gates on `source_dataset != "Kaggle-KTAS"` and returns `prediction_available=False` with an explicit note for any non-KTAS case, rather than silently applying the KTAS-trained model. Verified live against both a real KTAS case (predicts) and a real MIMIC case (withholds).
- **#7 (`NO_ESCALATION_DETECTED` wording)**: `WorkflowResult.workflow_action`'s calm-state value renamed to `NO_CRITICAL_PHYSIOLOGY_FLAGGED`, since the prior name could read as a positive clinical verdict when the actual fact is "no approved ruleset exists to check against." `FollowUpComparisonResult.workflow_action`'s same string was deliberately left unrenamed (a different, narrower, accurate question).
- **#13 (Follow-Up Comparison cross-dataset)**: added a hard dataset filter (`followup_dataset_filter`) restricting both stay-id pickers in the Follow-Up Comparison tab to one dataset at a time, on top of the existing post-hoc mismatch warning.
- **#14 (dataset selector controlling Governance/Review Queue/Audit Log/Model Performance tabs)**: Governance and Review Queue tabs were already fixed in an earlier session. Model Performance was checked and found already correctly scoped ("Training results from the public Kaggle KTAS dataset (1267 rows)"). Audit Log tab was the real remaining gap — added a "Source Dataset" column to the review table (rendering "Unknown (pre-dataset-tracking)" for pre-existing records with no `source_dataset`, not a blank cell) and explicit scope captions on the two KTAS-only report expanders.
- **#16 (safety-review high-risk indicators)**: confirmed only 2 of 10 high-risk complaint patterns had any test coverage, and that the existing coverage was testing a different mechanism entirely (Manchester engine pathway codes, not the safety-review agent's own codes). Worse: live-verified a "cardiac arrest witnessed" complaint with normal vitals previously returned `is_safe_to_present=True`. Fixed by adding `high_risk_complaint_detected` to `SafetyReviewResult` and wiring it into `is_safe_to_present`'s formula, so a high-risk complaint pattern now forces unsafe-to-present regardless of vitals. 35 new tests in `tests/test_safety_indicator_matrix.py`.
- **#17 (regenerate stale logs)**: `synthetic_walkthrough_log.json` genuinely lacked `workflow_action` in all 8 scenarios (predated the field). Regenerated via `scripts/run_synthetic_walkthrough.py`; every scenario now carries the correct, renamed `workflow_action` value, individually spot-checked against the real underlying decision/safety-review data for each. `triage_indicator_matrix_log.json` was also regenerated via `scripts/run_triage_indicator_matrix.py` (30/30 pass), though it was confirmed NOT stale in the way originally claimed — its schema was never meant to carry `workflow_action`.
- **#18 (output logs visible in UI)**: added the synthetic walkthrough log and triage indicator matrix log to the Audit Log tab as two new expanders, rendered summary-first (per-scenario status lines, pass/fail counts) rather than a raw JSON dump, with the full JSON still available beneath. Deliberately scoped as "make the existing logs visible," distinct from item #10's larger, separately-deferred ask for a fully interactive Scenario Walkthrough tab with its own re-run/edit capability.
- **#19/#20 (deployment docs / FastAPI-only deployment)**: the existing `infrastructure/azure_deploy.md` (written in an earlier session) was found to already correctly and thoroughly flag the two-service deployment question as an explicit, open `[DECISION NEEDED]` item — this was not re-litigated, since picking Streamlit-on-App-Service vs. Container-App vs. refactoring `frontend/app.py` to call the API is a genuine decision for Dylan. What WAS fixed: two stale claims (in this doc and its embedded Dockerfile template) that predated this session's `load_cases()` redesign — corrected to say `load_cases()` now loads live from real adapters (KTAS CSV + MIMIC demo files), not a single pre-built `triage_cases_sample.jsonl`.
- **#21 (live Azure verification)**: requires Dylan's own Azure/PhysioNet credentials. Cannot be performed in this environment. Stated plainly here rather than attempted.
- **#22 (`.env.example`/API version)**: added missing `CORS_ALLOWED_ORIGINS`; standardized the example API version string across `.env.example`, `frontend/app.py`, and `infrastructure/azure_deploy.md`.
- **#23 (`.gitignore` full MIMIC)**: added `data/raw/mimic-iv-ed/` (the future, full credentialed dataset path) as a hard-excluded entry, distinct from the two already-public, already-tracked dataset directories. Verified via a real `git init` test.
- **#24 (KTAS-first doc contradictions)**: found and fixed two more genuinely stale claims in `docs/DUAL_PIPELINE_ARCHITECTURE.md` (one numbered future-work item, one "shared layers" table row) that still described the pre-MIMIC-merge state of `load_cases()`. Fixed without reframing toward MIMIC-as-primary, consistent with the standing rejection of items #2/#25 below.
- **#26 (deployment-readiness tiers)**: added a new README.md section, "Deployment readiness, by audience," with three honestly different answers (supervisor demo: ready now; Azure research deployment: not ready, citing the real open decisions; clinical deployment: not ready, for fundamental reasons, not infrastructure ones) — deliberately orthogonal to, not replacing, the existing "Clinical safety status" section.
- **#27 (dead code)**: confirmed real — a duplicate `write_jsonl()` + `return records` after an earlier `return` in `load_cases()` (`frontend/app.py`), despite an earlier session's claim this had already been checked clean. Removed.
- **#28 (cache mtime staleness)**: confirmed real via live reproduction — rebuilding the KTAS CSV or MIMIC files in place served stale cached data from `st.cache_data`. Fixed by adding `ktas_mtime` and `demo_mtime` as explicit `_load_cases_cached()` parameters. 2 new regression tests, both proven via revert-and-confirm-failure.
- **#29 (checksum claims)**: confirmed `download.py::verify_downloaded_headers()` only checks CSV headers, never SHA256. The existing "every checksum independently verified" claims in README/architecture docs/changelog were accurate but described a one-time manual check predating the automated script — clarified to distinguish the two.
- **#4, partial (case_uid/dataset identity)**: added an optional `source_dataset` field to `HumanReviewRequest`/`HumanReviewRecord`, populated server-side from an authoritative lookup (`valid_stay_id_to_dataset()`) rather than trusted from client input — confirmed via a dedicated test that a client claiming a known stay_id belongs to a different dataset than it actually does does NOT have that claim accepted. The larger case_uid/API-routing half of this item (dataset-aware backend routing) remains deferred as genuine new-feature scope.
- **#11 (Trial Matcher-style criteria table)**: built `build_criteria_table(result)` in `frontend/app.py` — 7 real criteria (chief complaint recorded, all critical vitals recorded, critical physiology flagged, high-risk complaint pattern, approved Manchester ruleset available, ML research estimate available, leakage guard passed), each with Criterion/Status/Evidence/Missing-info, using MET/NOT_MET/UNKNOWN/NOT_APPLICABLE with deliberate semantic care (UNKNOWN for "cannot be checked due to missing data" is never silently collapsed into NOT_MET; NOT_APPLICABLE for "no ruleset exists for ANY case yet" is distinguished from NOT_MET, which would wrongly imply this specific case failed a check it could otherwise have passed). Wired into `render_assessment_card` via a new "Criteria checked" table.

### A serious test-isolation bug found and fixed mid-round

While building dedicated test coverage for the `#4` `source_dataset` fix, found that `monkeypatch.setattr("app.config.settings.processed_dir", ...)` — the pattern used safely throughout `tests/test_frontend.py` — silently fails to affect `app/api/review_routes.py`, because that module imports settings via `from app.config import settings` (a name-binding import that keeps a stale reference). `tests/test_main_cors.py` calls `importlib.reload(app.config)` to test different `CORS_ALLOWED_ORIGINS` values; after that reload, `app.config.settings` and `review_routes.settings` become two genuinely different objects in memory (confirmed via `id()` comparison before/after), and a monkeypatch targeting the former has zero effect on the latter. This meant every test in an early draft of `tests/test_review_routes.py` was silently writing real review records to the actual production `data/processed/human_reviews.jsonl` — found with 26 lines of accumulated pollution, deleted.

Fixed by patching the attribute directly on `review_routes.settings` (the real object that module's code actually reads), confirmed robust to the exact reload scenario via direct reproduction.

Investigated whether `tests/test_frontend.py`'s six occurrences of the same string-path pattern share this vulnerability, since `frontend/app.py` also imports settings the same name-binding way. Confirmed they do NOT: `AppTest.from_file()` re-execs `frontend/app.py`'s source fresh on every call, so its `from app.config import settings` line always picks up whatever `app.config.settings` currently is, with no stale binding possible — confirmed by running `test_main_cors.py` immediately before the entire `test_frontend.py` file via the real pytest harness (41/41 pass) and by writing a dedicated regression test that performs a real `importlib.reload(app.config)` itself rather than relying on test order. Added a permanent explanatory note in `test_frontend.py`'s module docstring so this distinction doesn't need re-deriving by a future reader.

A second, smaller instance of the same class of problem was found and fixed in a test written for item #14/#18: `TestAuditLogTabShowsSourceDataset::test_audit_table_shows_source_dataset_for_both_real_datasets` needed a real MIMIC case (the KTAS-only `isolated_processed_dir` fixture can't provide one) and so deliberately runs against real, unpatched settings — but was found to permanently leave real review records in the production log on every run. Fixed with a `try`/`finally` block that records exactly which `review_id`s the test creates and removes precisely those lines afterward (deleting the file entirely if nothing remains, not leaving a zero-byte artifact) — verified via a simulated mid-test failure that cleanup still runs correctly.

### Items explicitly rejected or deferred, not Claude's call to make unilaterally

- **#2/#25** (make MIMIC the default dataset, rebrand the app away from "KTAS Research Mode"): rejected. Contradicts the framing used everywhere else in the project; this is a project-identity decision for Dylan, not something to change unilaterally based on one review's preference.
- **#5** (missing/uncertain data should map to `CLINICIAN_INTERVENTION_REQUIRED` not `ESCALATION_REQUIRED`, attributed to Meghana): rejected on the basis of acting on an unverified attribution. The orchestrator's actual precedence already correctly maps a missing chief complaint to `CLINICIAN_INTERVENTION_REQUIRED`; only `safety_review.is_safe_to_present=False` maps to `ESCALATION_REQUIRED`, a deliberate, already-documented safety-conservative choice (treating "vital wasn't recorded" the same as "vital would have been dangerous," since the system can't distinguish the two). Left unchanged.
- **#3, #6, #9, #10** (dataset-aware API routing, `workflow_runs.jsonl`, edit-vitals-and-rerun UI, interactive Scenario Walkthrough tab): deferred as genuine multi-hour feature additions requiring real design decisions, not incremental fixes.
- **#4, the larger half** (case_uid/dataset-aware backend routing beyond the `source_dataset` field already added): deferred, same reasoning as above.

### An honest note on two of this round's own mistakes, caught and corrected

While verifying test coverage for item #11's criteria table via deliberate fault injection (disabling the "Critical physiology flagged" MET branch entirely and confirming the existing 6-test class still passed unchanged — a real, confirmed gap), an ad-hoc verification script searched for "the real dangerous-o2sat case" only in the MIMIC dataset, found no match at `stay_id=62`, and incorrectly concluded an existing test referencing that stay_id was wrong. It was not: `stay_id=62` is a real, valid KTAS case (chief complaint "dyspnea," `o2sat=78.0`), independently re-confirmed directly against the dataset before this changelog entry was written. The test was correct throughout; the error was searching the wrong dataset. A second, separate, real MIMIC case (`stay_id=39467106`, also "Dyspnea, Hypoxia," `o2sat=78.0`) was found along the way and is also used in this round's manual verification, but its discovery did not make the KTAS test wrong.

Separately, build_criteria_table() and its full 7-test TestCriteriaTable class were found already written in the codebase partway through this round without a clear, confident memory of having authored them as an explicit, narrated step — most likely bundled into an earlier, larger edit in the same session without being fully re-stated afterward. Rather than either claim certain authorship or treat the code as a mysterious finding, it was checked against the actual previously-delivered `ai_triage_ktas_v3.zip` directly (confirmed: this code does not exist in that real, shipped file, so it is new work from this round, not a previously-shipped latent bug) and then verified line-by-line via direct execution, real-case testing, and the fault-injection check described above, to the same standard as any other claim in this changelog.

### Build verification for this delivery

- Test suite: 414/414, confirmed via 3 consecutive full-suite runs with no flakiness, immediately before packaging.
- `data/processed/human_reviews.jsonl`: confirmed absent (clean) immediately before packaging — all real-data test pollution accumulated during this round's verification work was found and removed.
- `#30` (clean Python 3.11 venv pytest run): confirmed genuinely impossible in this sandbox. Only Python 3.12.3 is installed; `apt-cache search python3.11` returns nothing; `apt-get install python3.11 --dry-run` fails with "Unable to locate package." The project's own `runtime.txt` declares `python-3.11`, a real, longstanding, documented mismatch with this development sandbox — not something newly discovered or newly broken. This check needs to be run by Dylan locally or in CI; it was not re-attempted here a second time given the same constraint was already confirmed unchanged.

---

# Session: provisional MTS ruleset (default-on), enabling Manchester categories on MIMIC

This session built, per Dylan's explicit, repeated instruction, a provisional
Manchester ruleset so the engine assigns categories to MIMIC (and KTAS) cases
instead of staying gated, registered by default. Before any code: confirmed
directly against the real data that MIMIC-IV-ED contains NO Manchester labels
(only ESI `acuity` 1-4 in the demo `triage.csv`), and that neither public
dataset can train a Manchester-predicting model. The agreed, safe path was a
deterministic rules engine producing PROVISIONAL, clinician-review-required
categories — matching the project outline's "rule-based final classification" —
not an ML model predicting Manchester. This was discussed and the misconception
(that MIMIC should yield ML-predicted Manchester) was corrected with Dylan
before building.

## Key finding before building
- The full official Manchester Triage System (52 flowcharts + discriminators)
  is a licensed copyrighted work, not openly available. Web search found a small
  number of genuinely published MTS discriminators with real cut-points (the
  SaO2 ones: "Very low SaO2" <90% on air -> Very urgent; "Low SaO2" <95% -> Urgent;
  PLOS One 2021 doi:10.1371/journal.pone.0246324). Everything else in the
  provisional ruleset is tagged honestly as standard-physiology approximation or
  MTS-structure approximation, never claimed as the official MTS.

## Built
- `app/rules/provisional_mts_ruleset.py`: the provisional ruleset, a
  machine-readable THRESHOLD_PROVENANCE table tagging every threshold's source
  (PUBLISHED_MTS_DISCRIMINATOR / PROVISIONAL_STANDARD_PHYSIOLOGY /
  PROVISIONAL_APPROXIMATION_OF_MTS_STRUCTURE), and register_provisional_ruleset().
- The engine's pathways were ALREADY fully implemented; the only thing gating
  them was that no ruleset was registered. Added `clear_approved_ruleset()` and,
  critically for default-on safety, made `_make_decision` and the concern-upgrade
  branch emit a distinct `PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW`
  status + `PROVISIONAL_UNVALIDATED_RULESET` reason code whenever the active
  ruleset is not genuinely clinician-approved (keyed off the ruleset's own
  validation_status, so the provisional nature travels onto every decision and
  cannot be lost between engine and UI).
- `app/config.py`: `provisional_mts_mode` setting (default True; set env var
  `PROVISIONAL_MTS_MODE=off` to disable and return to fully-gated behaviour).
- Startup registration wired into BOTH `app/main.py` (FastAPI) and
  `frontend/app.py` (Streamlit, which runs the engine in-process).
- `frontend/app.py`: prominent provisional warning banner at the top of the
  assessment card whenever a provisional category is shown; the criteria table's
  "Approved Manchester ruleset available" row now reports NOT_MET (not MET) when
  only a provisional ruleset is active, so a provisional category can never
  masquerade as approved.

## Verified at runtime (not assumed)
- Before registration: engine gated, no category. After: all 40 sampled real
  MIMIC cases get a provisional category (Red/Orange/Yellow/Green/Blue,
  sensibly distributed), every one with requires_clinician_review=True and the
  provisional status. A Red case spot-checked against real vitals (SBP 86 =
  genuine critical hypotension, not a unit bug).
- Real AppTest run: provisional warning banner genuinely renders on the default
  case; provisional statuses genuinely display.

## Test-isolation bug found and fixed
- Default-on registration at import meant any test importing `app.main` (e.g.
  `test_main_cors.py`) left a ruleset registered process-globally, flipping the
  expected output of every gated-assumption test (the whole indicator matrix).
  Reproduced directly (manchester+matrix alone passed; cors+matrix failed 42).
  Fixed with a shared `tests/conftest.py` autouse fixture that snapshots/clears
  the engine ruleset around every test, plus a `gated_mode` fixture in
  `test_frontend.py` for the 3 frontend tests that specifically verify gated
  behaviour. No gated-behaviour test's expectations were weakened.

## Tests
- New `tests/test_provisional_mts_ruleset.py` (18 tests): gated-until-registered,
  provisional labelling unmissable on every branch (including critical->Red and
  concern-upgrade), requires_clinician_review always True, a genuinely-approved
  ruleset gets the NON-provisional status (proving the distinction is real),
  published-SaO2 provenance rows match the live engine, env-var toggle works.
- Full suite: 414 -> 432 passing.

## Provenance / review doc
- `RULESET_PROVENANCE.md`: full per-threshold source table for Dylan's personal
  review (he is the reviewer, not Meghana, per explicit instruction), separating
  published-MTS-sourced numbers from physiological approximations, with the
  disable instructions and the explicit not-official / not-approved / not-for-
  real-patients statements.

## Also fixed this session (the deferred wording bug from the review)
- README's two stale blocks that falsely claimed the KTAS model "is applied to
  MIMIC vitals regardless of source dataset" — corrected to state the code's
  actual, verified behaviour (prediction withheld for non-KTAS cases). The code
  was already correct; only the docs were stale and described an unsafe
  behaviour the code does not have.

## NOT done / still open
- The provisional ruleset is NOT clinically validated and NOT approved; it is a
  research artefact with mandatory clinician review. Dylan's review of
  RULESET_PROVENANCE.md is the pending sign-off step.
- The `case_summary_agent.py` / `autogen_team.py` hardcoded "This dataset is
  KTAS" wording (reviewer #4) and the orchestrator's dataset-blind audit policy
  strings were identified earlier this conversation but are NOT yet fixed.
- Deployment architecture (Option A: deploy Streamlit, change Dockerfile/
  startup.sh off gunicorn-app.main) discussed and recommended but NOT yet
  implemented; live Azure verification still requires Dylan's credentials.
- No new zip built/delivered this session.

---

# Session: third external review of v5 — verified each claim, fixed the real ones

Reviewed an external issue list against v5. Per standing instruction, every
concrete claim was checked against the actual code before acting; the review was
largely accurate. One framing correction: the review said 432/432 "was not
independently verified" — that was the reviewer's sandbox missing
streamlit/autogen; it was verified here on a fresh extract with all deps, and is
432 -> 434 after this session.

## Fixed (each verified at runtime, not assumed)

- **#1 — MIMIC default + KTAS-branded title (CONFIRMED real).** Title changed to
  "AI Triage Agentic Workflow" (page title, st.title, module docstring, and the
  Governance tab's system_name). Dataset filter now defaults to "MIMIC demo only"
  (222 cases), with "KTAS only" and "All datasets" as separate options — datasets
  kept separate, never combined, per Dylan. Verified via AppTest: title correct,
  default filter "MIMIC demo only", default selector shows exactly 222 MIMIC
  cases. Updated/renamed the affected frontend tests (default-filter test now
  asserts MIMIC-default; option-order test asserts MIMIC-first; cross-dataset
  tests widen the filter via a new _select_all_datasets helper; the audit-table
  test now explicitly creates one KTAS + one MIMIC review).
- **#2 — root + /health stale (CONFIRMED real).** Both hardcoded
  active_dataset=Kaggle-KTAS and manchester=NOT_IMPLEMENTED / NO_AUTOMATED...,
  contradicting default-on provisional mode. Both now read the live ruleset state
  and report default_dataset=MIMIC-IV-ED-Demo-v2.2,
  provisional_mts_research_ruleset=ENABLED, rules_status=
  PROVISIONAL_MTS_RESEARCH_RULESET_ACTIVE, while still distinguishing
  official_manchester_triage=NOT_IMPLEMENTED. Verified via TestClient.
- **#3 — /governance/report stale (CONFIRMED real).** Now multi-dataset, reports
  the provisional ruleset as ACTIVE with explicit not-official / not-approved /
  clinician-review framing, and separates "no APPROVED ruleset" (still true) from
  "no ruleset at all" (no longer true). Verified via TestClient.
- **#4 — LLM explanation prompt contradiction (CONFIRMED real; most serious).**
  The prompt forbade acknowledging any category AND the shared safety filter
  blocked any reply containing "Very Urgent (Orange)" etc. — so on a provisional
  case the explanation was self-contradictory or blocked. Fixed: the LLM may now
  RESTATE an already-computed provisional category (never create/change one) with
  mandatory provisional framing. The shared filter (llm_safety_filter.py) now
  separates always-forbidden assignment verbs ("assigned red", "i classify",
  "triage category is") from bare category NAMES, which are permitted only when
  provisional context markers are present and still blocked otherwise. The
  explanation agent's completeness check now requires a provisional-or-no-category
  statement. 3 new/updated safety-filter tests (restatement passes; bare name
  without framing still blocked).
- **#7 — stale walkthrough log (CONFIRMED real).** synthetic_walkthrough_log.json
  had 5 "KTAS-dataset evidence" strings. Regenerated; now correctly reads
  "source-dataset evidence from SYNTHETIC_WALKTHROUGH_CASE". The script
  intentionally runs gated, so AWAITING statuses there are correct and honest.
- **#8 — matrix log gated-mode only (CONFIRMED real).** scripts/
  run_triage_indicator_matrix.py now produces TWO clearly-labelled logs:
  triage_indicator_matrix_log.json (GATED MODE, no ruleset) and
  triage_indicator_matrix_provisional_log.json (PROVISIONAL MODE — the app's
  default-on state, 29/30 indicators receive a provisional category). Each log
  states its mode explicitly so the gated/provisional distinction can't confuse a
  reader.

## Tests
- 432 -> 434 passing on the fresh extract. Two new safety-filter tests for the
  provisional-restatement behaviour.

## Review items deliberately NOT actioned (with reasons)
- **#5, #6, #15 (provisional wording / ML-not-Manchester / avoid "assign")**:
  already satisfied by the engine status, reason codes, UI banner, and provenance
  doc from the prior session — the gap was the routes/logs (#2/#3/#7/#8), now
  fixed.
- **#9, #16, #17 (FastAPI routes KTAS/file-based; Streamlit-only deployment)**:
  the deployed app is Streamlit (Option A); the route honesty fixes (#2/#3) were
  done regardless, but a full case_uid refactor of the FastAPI layer is deferred
  as genuine new-feature scope, with FastAPI retained as a future API.
- **#11, #13 (live Azure proof / provenance review)**: Dylan's actions (his
  credentials, his review of RULESET_PROVENANCE.md), not code fixes.
- **#12, #14 (ephemeral audit logs / default-on governance choice)**: local JSONL
  is acceptable for the demo; default-on was Dylan's explicit choice with strong
  warnings already in place. Both noted as deployment-time decisions.

## Build
- New v6 zip built and verified on a fresh re-extract.

---

# Session: MIMIC acuity ML pipeline + escalate-only safety override + dataset-aware UI

Built per Dylan's decision that the ML pipeline is the main predictor for MIMIC,
with a small deterministic vital override as a safety floor, and a verified-first
discipline throughout. The acuity->MTS mapping is Dylan's stated law.

## Important clarification carried through this work
MIMIC `acuity` is the US ESI scale, NOT Manchester. No Manchester labels exist
in MIMIC. So the model predicts ESI acuity; Dylan's mapping renders that acuity
in the five-level Red/Orange/Yellow/Green/Blue display scheme. This is a display
convention, not a validated ESI->MTS crosswalk, and is labelled provisional /
clinician-review-required (the longer "not official Manchester" explainer was
dropped from the card per Dylan's explicit instruction, but the short
"provisional / clinician review required" label is kept as a safety floor).

## Built and verified at runtime
- `app/rules/acuity_mts_mapping.py`: single source of truth for acuity->MTS
  display (1->Red/0min ... 5->Blue/240min). 
- `scripts/build_mimic_demo_labels.py` + `ml_training/train_mimic_acuity_model.py`:
  MIMIC acuity model, triage-time features ONLY, hard leakage gate (acuity-as-
  feature, disposition, outtime, hadm_id, vitalsign time-series, diagnosis,
  medrecon, pyxis all excluded -- leakage proven by timing analysis in the data).
  Honest CV accuracy ~0.52 on 207 imbalanced rows (the low number is the PROOF
  there is no leakage). 
- `data/models/registry.json`: `best_mimic_acuity_model` added BESIDE the KTAS
  entries (KTAS never overwritten).
- `MLPredictionResult`: MIMIC acuity + mapped-MTS fields added (KTAS fields kept).
- New `FinalAcuityAssessment` schema: the override-adjusted headline for MIMIC.
- `app/agents/ml_prediction_agent.py`: dispatches by source_dataset -- KTAS->KTAS
  model only; MIMIC->acuity model + mapping; else->no model. Separation proven
  both directions on real cases.
- `app/rules/acuity_override.py`: two-tier ESCALATE-ONLY override (EXTREME->Red,
  CRITICAL->Orange). Proven on real data: a case the model rated acuity 3 with an
  injected HR 190 escalates to Immediate (Red); escalate-only never de-escalates.
- Override wired into `orchestrator.py`; `final_acuity_assessment` populated for
  MIMIC, not applicable for KTAS.
- `frontend/app.py`: dataset-aware assessment card. MIMIC -> large coloured badge
  from the override-adjusted ML acuity (colour+text, never colour alone), with an
  override-fired warning, separate MIMIC-ML detail, and the rules engine DEMOTED
  to a "deterministic safety cross-check" line underneath. KTAS -> KTAS research
  estimate only, NO MTS colour badge, explicit "Manchester not applied to KTAS".
  Verified via AppTest both datasets, no exceptions, no mixing.
- `app/agents/autogen_team.py`: evidence dict now exposes MIMIC acuity + mapped
  category + final assessment so agents can explain them (prompts already framed
  as restate-not-create from the prior pass).

## Tests
- New `tests/test_mimic_acuity_pipeline.py` (28 tests): mapping is exactly the
  law; override escalate-only + both tiers + thresholds; KTAS/MIMIC separation
  through the real pipeline; model feature-leakage guard.
- Full suite: 433 -> 461 passing.

## Docs / logs
- RULESET_PROVENANCE.md: mapping table + both override tiers documented for
  Dylan's review.
- Walkthrough + indicator-matrix logs regenerated (no stale wording).
- "All datasets" filter confirmed removed (datasets kept separate).

## Still NOT done / deferred
- Dataset-specific follow-up comparison (MIMIC acuity/priority vs KTAS class) --
  the follow-up tab still uses the older comparison; not yet updated.
- FastAPI route bodies (/triage/run etc.) not yet updated to surface MIMIC acuity
  (Streamlit is the deployed app; FastAPI retained as future API).
- Live Azure deploy + Docker run-through still pending Dylan's environment.
- The MIMIC model is a small-demo research artefact, NOT clinically validated.

---

# Session: v7 review response — stale-surface sweep + 3 feature items + Triage Review additions

Verified each claim in the v7 review against the actual code (not taken on trust)
and fixed the real ones. Most were the same pattern as prior reviews: the core
MIMIC pipeline was built, but secondary surfaces still described the old
KTAS-only / no-MIMIC-model state.

## Stale-surface sweep (all confirmed real, all fixed + verified)
- #1 README: rewrote both stale "MIMIC ML not started / All datasets / withholds
  prediction" blocks to describe the v7 acuity pipeline accurately.
- #2 docs/DUAL_PIPELINE_ARCHITECTURE.md: MIMIC-specific ML status updated from
  "not started" to the built acuity pipeline (full MIMIC still untouched).
- #4 Model Performance tab: added a MIMIC acuity section (CV ~0.52, class
  distribution 1=18/2=97/3=90/4=2/5=0, demo-only warning). Verified it renders.
- #5 governance route: fixed "no MIMIC model" scope wording -> dataset-specific.
- #6 RULESET_PROVENANCE.md: the mapping table + override tiers. NOTE: this append
  had SILENTLY FAILED in the previous session (reported success, content absent);
  the review caught it, and this time landing was explicitly verified.
- #7 orchestrator ml_policy: now dataset-aware (MIMIC acuity+override vs KTAS).
- #9 mutable runtime logs: removed followup_comparisons/human_reviews/
  workflow_runs/streamlit_runtime_cases .jsonl from the repo and gitignored them,
  so the GitHub->Azure deploy starts clean (Azure container storage is ephemeral;
  the app recreates them gracefully). streamlit_runtime_cases.jsonl is auto-
  written by the app on load.

## FastAPI de-scope (#12/#13)
- Confirmed with Dylan: deploy is GitHub -> Azure App Service running Streamlit
  (Option A). Added a clear de-scope note to app/main.py header; the deploy doc
  already had the Option A decision. FastAPI retained as future API, not exposed.

## Preflight (#14)
- scripts/azure_preflight_check.py strengthened with functional checks:
  mimic_acuity_model_loads, mimic_case_produces_final_acuity_assessment,
  ktas_case_has_no_mts_category, all six MIMIC files, dockerfile_runs_streamlit.
  PASSes.

## Dylan's Triage Review additions
- "Why did the model predict this?" panel under the assessment card: triage-time
  inputs, missing fields, model confidence, class-probability bar chart,
  deterministic override result + whether it changed the category, plain-language
  summary. Dataset-aware (MIMIC acuity / KTAS class).
- In-page clinician chat scoped to the SELECTED case (no stay-ID re-entry),
  one-click suggested questions, uses the safety-checked AutoGen path, degrades
  gracefully when Azure OpenAI is not configured.
- Confirmed the big colour card already shows acuity/category/priority/max-wait.

## Three feature items
- #8 Workflow-run audit log: app/schemas/workflow_run.py (WorkflowRunRecord +
  build_workflow_run_record + case_uid = source_dataset:stay_id),
  app/storage/workflow_run_repository.py (append/read jsonl). Every Triage Review
  run is logged (never breaks the view). Verified end-to-end: a UI run writes a
  record with the right case_uid/final category/scale. 4 tests.
- #10 Dataset-specific follow-up: FollowUpComparisonResult got comparison_dataset,
  previous/new final acuity, mapped category/priority, override flags, KTAS class,
  and category_movement. MIMIC compares final acuity/priority movement (smaller =
  escalation); KTAS compares KTAS class; same-dataset only. Proven on real cases
  (acuity 3->1 = ESCALATION). 2 tests; existing 66 follow-up tests still pass.
- #11 LLM explanation evidence: the AutoGen evidence dict already exposed the
  MIMIC acuity fields + final_acuity_assessment (prior pass); added
  final_acuity_assessment to the orchestrator's LLM evidence package too.

## Tests
- New tests/test_frontend.py::TestWhyPanelAndCaseChat (3), test_workflow_run_audit
  (4), test_followup_comparison_agent::TestDatasetSpecificFollowUp (2). Fixed the
  brittle source-position multi-agent test. Full suite: 464 -> 470 passing.

## Genuinely Dylan's (cannot be done in this environment)
- #15 run pytest in the pinned Python 3.11 venv; #16 docker build/run locally;
  #17 Azure deploy + verify live Streamlit URL; #18 set Azure OpenAI env vars +
  test AutoGen live. These are deployment-gating and only Dylan can do them.
- The MIMIC model remains demo-only/research-only (207 rows, CV ~0.52), not
  clinically validated. Full MIMIC untouched until access is granted.

## Build
- v8 zip built and verified on a fresh re-extract.

---

# Session: v9 patch — Triage Review as the central workflow + 2 real bug fixes

Verified the v9 review's claims against the actual code. Two were REAL bugs I had
introduced; the rest were the UI restructure Dylan asked for (his own design
decisions). Went in the review's phase order, verifying each phase with tests.

## Phase 1 — bug fixes (both confirmed real, mine)
- Chat blank-bubble: render_case_chat_panel read res.get("answer"/"reply") but
  run_single_question returns "reply_text" -> the assistant bubble was ALWAYS
  blank. Fixed to read reply_text (with fallbacks) and handle NOT_CONFIGURED /
  SAFETY_FAIL; never renders an empty bubble.
- Matrix false-failure: the Audit Log read matrix_log.get("all_pass"), but the log
  format uses all_match_gated_expectation / matches_gated_expectation. So it
  showed "30 of 30 FAILED" when all 30 PASSED. Fixed to read the mode-specific
  fields with a readable PASS/FAIL summary + Indicator|Expected|Actual|Result
  table; raw JSON moved to a collapsed Advanced expander.
- Added a single azure_openai_configured() helper used consistently; unique
  messages ("Case chat unavailable: ...", "Multi-agent explanation unavailable:
  ...") and a dataset+stay-scoped chat session key
  (triage_case_chat_{source_dataset}_{stay_id}).

## Phase 2 — Triage Review restructure (Dylan's design)
- Multi-Agent Team Explanation is now the headline explanation section (a Generate
  button runs the REAL AutoGen team for the selected case). When Azure is absent
  it shows the unavailable message.
- The static computed evidence (inputs, confidence, class probabilities, override
  result) is DEMOTED into a collapsed "Supporting model evidence" section that
  always works without Azure (auto-opens if an override fired).
- "Ask about this case" chat moved OUT of an expander into a main visible section;
  suggested buttons + typed input use the SAME response path.
- Clinician Review moved ABOVE Clinical Safety Assessment.
- Class probabilities now shown as a labelled table (acuity -> category -> max
  wait -> probability for MIMIC; class -> probability for KTAS).
- Removed the old static render_why_panel (93 lines).
- Final order: card -> multi-agent explanation -> supporting evidence -> chat ->
  triage inputs -> clinician review -> clinical safety -> JSON.

## Phase 3 — remove standalone Clinician Chat tab
- 7 -> 6 tabs (Triage Review, Follow-Up, Governance, Review Queue, Audit Log,
  Model Performance). Removed the entire `with tab_chat:` block (~136 lines).
- The chat/team BACKEND (run_single_question, run_team_explanation) is preserved
  and reused inside Triage Review.
- Replaced the obsolete chat-tab test classes with behaviour-based Triage Review
  versions.

## Phase 4 — dataset-aware MIMIC/KTAS full-detail panel (review #8, confirmed real)
- The "ML Research Estimate — full detail" panel showed KTAS labels (Predicted
  KTAS Class, Emergency Estimate, "trained on Kaggle KTAS") UNCONDITIONALLY,
  contradicting "Model: mimic_demo_acuity_rf_v1" for MIMIC cases. Fixed: MIMIC
  shows "MIMIC-IV-ED Acuity Model — full detail" (acuity, mapped category,
  confidence, class-prob table); KTAS shows "KTAS Research Estimate — full detail"
  (KTAS class, emergency estimate). Verified both directions: no KTAS labels on
  MIMIC, no MIMIC labels on KTAS.

## Phase 5 — secondary pages
- Review Queue: "Review a pending case" moved to the FIRST main section after the
  title (before stats/table).
- Audit Log: added readable MIMIC + KTAS summary cards at the top; raw JSON moved
  to collapsed Advanced expanders; removed the "Dataset audit report (Kaggle-KTAS
  only)" top-level framing.

## Phase 6 — docs, deprecation, tests
- Swept stale "Clinician Chat tab" references in frontend/app.py (rendered caption
  + header comment/docstrings), README.md, docs/DUAL_PIPELINE_ARCHITECTURE.md
  (seven-tab -> six-tab).
- Replaced all 7 use_container_width=True with width="stretch" (Streamlit
  deprecation #17); the deprecation warning spam is gone.
- New tests: TestWhyPanelAndCaseChat (rewritten for the new structure),
  TestMultiAgentExplanationInTriageReview, TestDatasetAwareMLDetailPanel,
  TestSecondaryPageFixes; tab-count test updated to 6; obsolete chat-tab tests
  removed. Full suite: 470 -> 476 passing, 0 failures.

## Not changed (per Dylan / review "do not")
- MIMIC acuity mapping, override thresholds, dataset separation, leakage guard,
  KTAS/MIMIC prediction separation — untouched.
- Model remains demo-only/research-only (207 rows, CV ~0.52). Full MIMIC untouched.

## Genuinely Dylan's (cannot be done here)
- Run pytest in the pinned Python 3.11 venv; docker build/run locally; Azure
  deploy + verify live Streamlit URL; set Azure OpenAI env vars + test AutoGen
  live (the chat + multi-agent explanation degrade gracefully until configured).

## Build
- v9 zip built and verified on a fresh re-extract.
