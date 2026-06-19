# Provisional MTS Ruleset — Provenance & Review Document

**Status: DRAFT — pending Dylan's personal review. NOT clinician-approved. NOT the official Manchester Triage System.**

This document exists for one purpose: so that the person reviewing the
provisional Manchester ruleset can see, for every threshold the engine uses,
exactly where the number came from — and in particular which numbers rest on a
real published MTS source versus which are a reasoned physiological
approximation standing in for the licensed MTS content that is not openly
available.

Ruleset ID: `provisional_mts_research_v0`
Defined in: `app/rules/provisional_mts_ruleset.py`
Live thresholds in: `app/rules/manchester_engine.py`
(`_critical_vital_flags`, `_concern_vital_flags`, and the `_pathway_*` functions)

---

## Why this is "provisional" and not "the Manchester Triage System"

The official MTS is 52 presentation flowcharts, each with its full ranked
discriminator set, published as a licensed, copyrighted work by the Manchester
Triage Group ("Emergency Triage", Wiley/BMJ). That full content is **not freely
reproducible** and is not in this project. What this ruleset provides instead is:

1. The small number of MTS discriminators that **are** published in the open
   peer-reviewed literature, with their real cut-points.
2. Standard adult physiological danger thresholds (the same ones this engine
   already used before any ruleset existed) standing in for the licensed
   discriminator detail.
3. Pathway-routing logic that mirrors the documented **shape** of the MTS
   (presentation → ranked discriminators → category) but whose keyword lists and
   category assignments are this project's own heuristic.

Because of (2) and (3), this must never be presented as the official MTS or as a
clinically approved ruleset. Every category it produces is stamped
`PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW`, carries the reason code
`PROVISIONAL_UNVALIDATED_RULESET`, and has `requires_clinician_review = True`.

---

## Source tags

| Tag | Meaning |
|-----|---------|
| `PUBLISHED_MTS_DISCRIMINATOR` | Cut-point taken directly from an open peer-reviewed publication describing a real MTS discriminator. |
| `PROVISIONAL_STANDARD_PHYSIOLOGY` | NOT from the official MTS. A widely-used adult physiological danger range, matching this engine's pre-existing bands. A reasonable approximation pending the licensed MTS detail and clinical sign-off. |
| `PROVISIONAL_APPROXIMATION_OF_MTS_STRUCTURE` | Routing / discriminator-keyword logic mirroring the documented MTS structure, but whose specifics are this project's own heuristic. |

---

## Threshold provenance table

### Oxygen saturation — the one set anchored to a real published MTS source

| Engine rule | MTS intent | Source | Citation |
|---|---|---|---|
| `o2sat < 90` → `CRITICAL_HYPOXIA_SPO2_BELOW_90` | Very Urgent (Orange)+ | **PUBLISHED_MTS_DISCRIMINATOR** | "Very low SaO2" (<90% on air) → Very urgent. PLOS One 2021, doi:10.1371/journal.pone.0246324 (Zachariasse/van Veen et al., MTS vital-sign work). |
| `90 ≤ o2sat < 95` → `CONCERN_SPO2_90_TO_94` | Urgent (Yellow) | **PUBLISHED_MTS_DISCRIMINATOR** | "Low SaO2" (<95% on air) → Urgent. Same source. |

### Heart rate

| Engine rule | MTS intent | Source | Citation |
|---|---|---|---|
| `heartrate > 130` → `CRITICAL_HEART_RATE_ABOVE_130` | Very Urgent+ | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard adult tachycardia danger range. The same published source uses "HR ≥120" as a Very-urgent discriminator example; the exact >130 cut here is approximation. |
| `100 < heartrate ≤ 130` → `CONCERN_HEART_RATE_101_TO_130` | Concern | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard adult tachycardia range; not an official MTS cut. |
| `heartrate < 40` → `CRITICAL_HEART_RATE_BELOW_40` | Very Urgent+ | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard adult bradycardia danger range; not an official MTS cut. |

### Respiratory rate

| Engine rule | MTS intent | Source | Citation |
|---|---|---|---|
| `resprate > 29` → `CRITICAL_RESPIRATORY_RATE_ABOVE_29` | Very Urgent+ | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard adult tachypnoea danger range; not an official MTS cut. |
| `resprate < 8` → `CRITICAL_RESPIRATORY_RATE_BELOW_8` | Very Urgent+ | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard adult bradypnoea danger range; not an official MTS cut. |
| `25 ≤ resprate ≤ 29` → `CONCERN_RESPIRATORY_RATE_25_TO_29` | Concern | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard adult tachypnoea range; not an official MTS cut. |

### Blood pressure (systolic)

| Engine rule | MTS intent | Source | Citation |
|---|---|---|---|
| `sbp < 90` → `CRITICAL_HYPOTENSION_SBP_BELOW_90` | Very Urgent+ (shock) | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard adult hypotension/shock threshold; not an official MTS cut. |
| `sbp > 220` → `CRITICAL_HYPERTENSION_SBP_ABOVE_220` | Very Urgent | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard adult severe-hypertension threshold; not an official MTS cut. |
| `90 ≤ sbp < 100` → `CONCERN_SBP_90_TO_99` | Concern | PROVISIONAL_STANDARD_PHYSIOLOGY | Borderline adult hypotension; not an official MTS cut. |

### Temperature (evaluated in Celsius)

| Engine rule | MTS intent | Source | Citation |
|---|---|---|---|
| `temp_c ≥ 41.0` → `CRITICAL_HYPERPYREXIA_TEMP_ABOVE_41C` | Very Urgent | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard hyperpyrexia threshold; not an official MTS cut. |
| `temp_c < 35.0` → `CRITICAL_HYPOTHERMIA_TEMP_BELOW_35C` | Very Urgent | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard hypothermia threshold; not an official MTS cut. |
| `39.5 ≤ temp_c < 41.0` → `CONCERN_HIGH_FEVER_39_5_TO_41C` | Concern / Urgent via fever pathway | PROVISIONAL_STANDARD_PHYSIOLOGY | Standard high-fever threshold; not an official MTS cut. |

### Pain & complaint routing

| Engine rule | MTS intent | Source | Citation |
|---|---|---|---|
| `pain ≥ 7` (severe) / `4–6` (moderate) / `<4` (mild) banding in pathways | Pain-severity general discriminators | PROVISIONAL_APPROXIMATION_OF_MTS_STRUCTURE | MTS does use pain-severity general discriminators, but the official 0–10 cut-points are licensed. This banding is approximation. |
| `_PATHWAYS` keyword lists → pathway selection (chest pain, dyspnoea, stroke, trauma, fever, abdominal, altered consciousness, overdose, anaphylaxis, cardiac arrest, generic pain) | Presentation flowchart selection | PROVISIONAL_APPROXIMATION_OF_MTS_STRUCTURE | Mirrors the documented MTS structure but keyword lists and category assignments are this project's own heuristic, not licensed flowchart content. |

---

## How the engine combines these

1. **Critical vital** present → `Immediate (Red)` (provisional). Highest urgency, regardless of complaint.
2. Otherwise, **complaint** selects a pathway; the pathway's discriminators choose a category.
3. A **concern-level vital** co-occurring with a matched pathway upgrades an Urgent-or-lower result to `Very Urgent (Orange)`.
4. **Missing chief complaint** → no category, `REQUIRES_CLINICIAN_REVIEW`.
5. Every output: `requires_clinician_review = True`, provisional status, `PROVISIONAL_UNVALIDATED_RULESET` reason code.

The keyword lists and per-pathway category logic are in
`app/rules/manchester_engine.py` (`_pathway_*` functions and `_PATHWAYS`). They
are the live source of truth; this document records where their numbers come
from. `tests/test_provisional_mts_ruleset.py` checks the published-source SpO2
rows actually match the live engine.

---

## Reviewer action

To **change** a threshold: edit it in `app/rules/manchester_engine.py`, update
its row here, and run `pytest tests/test_provisional_mts_ruleset.py`.

To **disable** provisional mode entirely (return the engine to fully gated, no
category for any case): set the environment variable `PROVISIONAL_MTS_MODE=off`.

This ruleset has **not** been clinically validated. It is a research artefact for
demonstrating the workflow on retrospective public datasets, with mandatory
clinician review on every output. It must not be used to triage real patients.


---

# MIMIC acuity model, acuity->MTS mapping, and the deterministic override

This section documents the MIMIC-IV-ED Demo acuity pipeline and the deterministic
vital override. Dylan is the reviewer of record on these numbers; none are
clinically approved.

## The acuity -> MTS-display mapping (Dylan's decision)

app/rules/acuity_mts_mapping.py (acuity_to_mts_display_v1):

| MIMIC acuity (ESI) | Display category | Colour | Priority | Max wait |
|---|---|---|---|---|
| 1 | Immediate | Red | 1 | 0 min |
| 2 | Very Urgent | Orange | 2 | 10 min |
| 3 | Urgent | Yellow | 3 | 60 min |
| 4 | Standard | Green | 4 | 120 min |
| 5 | Non-Urgent | Blue | 5 | 240 min |

This is a project DISPLAY convention for rendering a predicted MIMIC acuity in the
familiar five-level scheme. MIMIC acuity is the US ESI scale; this mapping is not a
validated ESI->MTS crosswalk. Not clinically approved; clinician review required.

## The MIMIC acuity model

Trained by ml_training/train_mimic_acuity_model.py on the PUBLIC MIMIC-IV-ED Demo
subset (207 labelled stays). Target: acuity. Features: triage-time ONLY
(chiefcomplaint, temperature, heartrate, resprate, o2sat, sbp, dbp, pain, gender,
race, arrival_transport). CV accuracy ~0.52 on this tiny, class-imbalanced sample
(acuity 5 absent, acuity 4 ~2 rows) -- illustrative only, NOT clinical evidence.

LEAKAGE EXCLUDED (proven by timing in the data): acuity-as-feature; disposition,
outtime, hadm_id (recorded ~6 h after triage); the entire vitalsign.csv in-stay
time-series (~203 min after triage); diagnosis.csv (post-visit ICD);
medrecon.csv/pyxis.csv. Enforced by a hard gate in the label builder and trainer
and asserted by tests/test_mimic_acuity_pipeline.py.

## The deterministic escalate-only vital override

app/rules/acuity_override.py (acuity_override_v1). The ML acuity is the main
predictor; this override can ONLY escalate (never de-escalate). Final category =
the more urgent of (ML, floor). All thresholds are PROVISIONAL_STANDARD_PHYSIOLOGY
(standard physiological values, not from the licensed official MTS):

EXTREME tier -> force Immediate (Red), priority 1 (peri-arrest ranges):
  SpO2 < 85 ; HR > 150 or < 35 ; RR > 35 or < 6 ; SBP < 80 ; temp >= 41.5C or < 32C

CRITICAL tier -> force Very Urgent (Orange), priority 2 (mirrors the engine's
existing critical thresholds; SpO2 < 90 has a published MTS source above):
  SpO2 < 90 ; HR > 130 or < 40 ; RR > 29 or < 8 ; SBP < 90 or > 220 ; temp >= 41C or < 35C

Rule: escalate-only; never de-escalate. Reviewer action: edit acuity_override.py
and update this section, then run pytest tests/test_mimic_acuity_pipeline.py.

## Review metadata
- Reviewer of record: Dylan (intern). NOT clinician-reviewed, NOT clinically approved.
- Version: acuity_to_mts_display_v1 + acuity_override_v1
- Clinical-use status: NOT FOR CLINICAL USE; clinician review required on every output.
- Known limitations: 207-row demo, class 5 absent, class 4 ~2 rows, CV accuracy ~0.52,
  no UHL/full-MIMIC/prospective/clinician validation. Demo/research only.
