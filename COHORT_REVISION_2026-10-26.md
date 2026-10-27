# Revised 100-case UHL demonstration (v2)

> Current sample and source checks: [60-matched-case revision](COHORT_60_MATCHES_2026-10-27.md). This report records the previous v2 sample.

This revision replaces the initial equal-category showcase with milder,
unchanged rows from the original UHL CSV used by the model release. All code
and data changes are local; GitHub and Azure have not been updated.

## Requirements and result

| Requirement | Implemented |
|---|---|
| Use the supplied UHL training CSV | All 100 cases were copied from the pinned original CSV; age, timestamp, complaint and all seven observations were checked against their source rows. |
| Around seven Acuity 5 cases | Exactly seven. The full displayed and estimated-label mix is 1 / 22 / 63 / 7 / 7 across Acuities 1-5. |
| Fewer extreme cases and fewer Acuity 4 cases | Acuity 1 falls from 20 to 1, Acuity 4 from 20 to 7, and Acuity 5 from 20 to 7. Acuities 2-3 account for 85 cases. Values are ranked closer to source medians and extreme tails are excluded. |
| Show the model working without frequent rule corrections | All 100 chosen model results are the modal class with at least 50% probability; both the model selection threshold and safety override leave the category unchanged. Model, provisional project pathway and CSV estimated acuity agree before any override. |
| Preserve safeguards | Model weights, inference policy, vital thresholds, escalation logic, audit and workflow actions are unchanged. New observations can still trigger escalation. |
| Keep the small runtime and concise evidence UI | Still exactly 100 in-memory cases, no full CSV/cache startup, and the existing short panel with all 17 checks. |

The dataset card explicitly identifies **synthetic vital signs and estimated
acuity labels**. These are genuine unchanged rows from the supplied file, not
verified recorded patient observations. No new vitals were generated and no
inputs, model probabilities or labels were rewritten.

## Why selection changed

The old sample took the highest model confidence and 20 cases per category.
That deliberately overrepresented rare/extreme categories. A replay of the old
100 cases confirmed 20 in each category and zero safety overrides; matching
safety flags were not category changes.

The revised selection scans all 777,176 source rows, considers supported adult
presentations aged 16-74, excludes child-labelled presentations, pre-2020 COVID
presentations, invalid readings and diastolic pressure above systolic pressure.
It requires six physiological readings within their source 1st-99th percentiles,
and pain no higher than the source 90th percentile (8/10). These are statistical
selection limits, **not clinical normal ranges or new triage thresholds**.

Eligible cases must have at least 50% model probability in the selected modal
class and agree with the unchanged project pathway **and the CSV estimated
label**. Selection then minimises the maximum and mean absolute distance from
source medians, scaled by each source interquartile range; higher model
probability is only a tie breaker. No one complaint can fill more than one third
of a category quota, rounded up. The final set covers 20 complaint types.

The preliminary two-way proposal matched the project pathway but only 67 of its
100 CSV estimated labels. It was rejected in favour of the explicit three-way
criterion. This prevents a curated showcase from silently presenting
source-label mismatches as successful predictions.

**This selection uses labels and favourable agreement. It must not be reported
as a model accuracy evaluation, clinical validation, or a representative Irish
ED case mix.** The requested seven Acuity 5 cases also exceed that class's
fraction in the original CSV. Existing model evaluation reports and their
metrics remain unchanged and separate. The interface and manifest disclose
synthetic vitals and curated label/model/rule agreement. The project pathway
remains provisional, not approved MTS clinical content.

## Recorded value ranges

| Observation | Previous 100 | Revised 100 |
|---|---:|---:|
| temperature (F) | 95.4-103.7 | 97-100.1 |
| heartrate (bpm) | 41-149.8 | 80.3-109.6 |
| resprate (/min) | 10.5-42.7 | 13.8-29.1 |
| o2sat (%) | 81.8-99.9 | 94.3-99 |
| sbp (mmHg) | 55-180.1 | 98.9-158.1 |
| dbp (mmHg) | 30.3-107 | 61.3-88.2 |
| pain (/10) | 0-10 | 0-8 |

Temperature in the source/model is Fahrenheit; the interface converts it to
Celsius. The revised temperature range is approximately 36.1-37.8 C.

## Implementation and reproducibility

- Updated `scan_demo_candidates.py` and `build_demo_cohort.py` to implement the
  disclosed filters, three-way agreement, quotas, ranking and complaint cap.
  The estimated label remains selection evidence and is excluded from model
  inputs by the existing case adapter and serving feature contract.
- Replaced `data/uhl_demo_100.json` with cohort `uhl_curated_demo_100_v2` and
  updated its hash pins, category-count contract and packaging checks.
- Updated the safe case notice and README. Retained the original trained model,
  original CSV, clinician workflow and every safety rule.
- Added regression coverage for source scope/data consistency, preference for
  milder values, rejection of obsolete candidate scans, category mix, complaint
  diversity, label evidence, confidence and unchanged inference results.

Rebuild offline using the original full CSV (not included in the small runtime):

```text
python scripts/scan_demo_candidates.py --source /path/to/uhl_dataset_final.csv.gz --output scan.json
python scripts/build_demo_cohort.py scan.json --output rebuilt_demo_100.json
```

Revised cohort SHA-256:
`160f65d2e948ba75435768afe5da73d67c709b5233a8e075fd8d0053251e18ee`.
Training CSV and model hash pins are unchanged. Source row numbers preserve
pseudonymous identities; replacing the sample does not reassign an existing
identity to another row. Old workflow/audit records are retained.

## Verification

- Final source-copy check: all 100 rows match every original input and the CSV
  estimated label. Original CSV hash and unchanged model hash were verified.
- Focused backend/selection/packaging tests: 56 passed, including real inference
  for all 100 cases. No selected case required either policy escalation or
  safety override. Minimum assigned probability is 50.0358%.
- Frontend regression suite: 73 tests passed in 11 files. The built UI code is
  unchanged; the synthetic/curation notice comes from the updated API.
- Local Azure preflight and read-only smoke checks passed. The browser confirms
  100 cases, the short evidence panel and full 17-check dropdown.
- **Final extracted source ZIP, with the full CSV absent: 1276 backend
  tests passed, 21 pre-existing skips and 5 warnings, in 122.77 seconds.**
  Its Azure preflight, Python compile checks and package hygiene passed.
- Two clean process starts from the extracted ZIP each returned exactly 100
  cases, with stable identities and no full CSV or patient SQLite cache.
  Local readiness/list timings were 7.76 and 7.67 seconds; these are not Azure
  performance or billing measurements.
- The final offline candidate scan rebuilt the exact same 64,491-byte cohort
  from its recorded candidates. Source/hash checks, final ZIP integrity,
  credential-value hygiene and patch applicability passed.
- The five Python warnings are the existing test-client deprecations, one
  NumPy compiled-extension size warning, and two SciPy optimizer deprecations.
  The 21 existing skips concern retired full-MIMIC active-source contracts.
  They were not added to accommodate this revision. Docker execution and live
  Azure deployment were not performed.

## Remaining limits

The original CSV is partially synthetic and its estimated labels are not
clinical ground truth. These selected examples demonstrate behaviour; they do
not establish real-world accuracy or calibration. Complete approved MTS content
and missing clinical observations remain external requirements described in
`MTS_REQUIREMENTS_REVIEW.md`. No model retraining, clinical certification, Azure
billing claim, live deployment or cloud storage deletion is part of this change.

The refreshed source ZIP excludes the full CSV, runtime state, dependencies and
Git history. The patch omits `.env.example` so the original removed key cannot
appear in diff context; the sanitized template is supplied separately.
