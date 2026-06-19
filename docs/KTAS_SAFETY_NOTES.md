# KTAS Safety Notes

This version uses public Kaggle KTAS data only.

## Non-negotiable limits

1. KTAS is not Manchester Triage Scale.
2. No KTAS-to-Manchester conversion is implemented.
3. `KTAS_expert` is used only as a research target.
4. `KTAS_RN` is not used as a main model feature because it is a prior human triage decision.
5. Diagnosis, disposition, mistriage, error group, length of stay, and KTAS duration are retrospective/audit fields and blocked from triage-time model features.
6. Outputs are not for clinical use and require human review.

## Temperature handling

Kaggle `BT` is treated as Celsius. MIMIC temperatures, when used later, should be treated as Fahrenheit. The rules engine converts to Celsius internally using `temperature_unit`.

## Verified against external sources (2026-06-16 review)

The following facts about this dataset were independently confirmed, not assumed:

- This is the dataset published in Choi et al. (PMC6730846), "Triage accuracy and causes of mistriage using the Korean Triage and Acuity Scale" -- 1267 records, 186 mistriage cases (131 under-triage, 55 over-triage), matching this CSV exactly.
- `Sex`, `Group`, `Injury`, `Mental`, `Arrival mode`, `Error_group`, and `mistriage` codes were cross-checked against a published data dictionary for this exact dataset and match.
- `Pain` is encoded as `1=pain present, 0=no pain` in this CSV (confirmed by cross-referencing against `NRS_pain`: of 714 `Pain=1` rows, 711 have a valid NRS score averaging 4.1; of 553 `Pain=0` rows, all 553 have no NRS score). This differs from the `0/2` convention shown in one secondary source for the same published dataset; the CSV's own internal consistency was treated as authoritative over that secondary source.

## Unverified / best-effort (flag before relying on these for any reporting claim)

- `DISPOSITION_MAP` in `app/data_pipeline/ktas_adapter.py` (the 7 numeric `Disposition` codes) has no confirmed source. It is a plausible best-guess for display/audit text only. **Disposition is excluded from `TriageTimeInput` and from all ML model features**, so this mapping cannot affect any triage decision, safety flag, or model prediction -- it only affects how a disposition code is rendered as text in retrospective/audit output. Confirm against the dataset's original documentation before citing these specific labels (e.g. "Admission to ICU") in any report. As of the 2026-06-16 follow-up, no source documentation for these codes was available, so this remains unresolved by deliberate choice rather than oversight.
- The 5-class `KTAS_expert` model's smallest class (KTAS=1, "most critical") has only 26 training examples out of 1267 rows. 5-fold cross-validation is technically valid here but not statistically robust for that class specifically -- treat the per-class metrics for KTAS=1 as indicative only, not as a reliable estimate of real-world performance on critical patients.
- The AutoGen chat agent (see below) has only been exercised against AutoGen's own `ReplayChatCompletionClient` test double, never against a live Azure OpenAI deployment in this environment (no live credential was available). The tool-calling machinery and the safety filter are genuinely verified; the model's actual conversational behaviour against the system prompt in practice is not, and should be checked manually before any demo.

## AutoGen chat agent -- safety design boundary (added 2026-06-16)

`app/agents/autogen_team.py` adds a real `autogen-agentchat` `AssistantAgent`
for the clinician-facing chat tab. The design boundary, enforced in code and
verified by tests (`tests/test_autogen_team.py`), is:

1. The agent's only tool reads already-computed, deterministic evidence
   (it calls the existing `run_workflow()` orchestrator internally). It has
   no tool that can set a triage category, modify a vital sign, or otherwise
   write anything back into the deterministic pipeline.
2. Every reply is checked against a deterministic, phrase-based safety
   filter (`app/rules/llm_safety_filter.py`, shared with the single-shot LLM
   Explanation Agent so the two surfaces cannot silently diverge on what
   counts as forbidden) before being shown to anyone.
3. If Azure OpenAI is not configured, every entry point (CLI script, API
   route, Streamlit tab) degrades to a clear `NOT_CONFIGURED` message rather
   than crashing or silently doing nothing.
4. The same rule as the LLM Explanation Agent applies: this layer explains
   verified evidence, it does not create it, and it must never be given a
   tool that would let it do so.

