# ML-first triage, 60 matched cases, and source verification

27 October 2026. This v3 sample supersedes the prior 100-case selections.
Changes are local and packaged for review; no GitHub push or Azure deployment
has been performed.

## Requested changes and results

| Requirement | Result |
|---|---|
| Use Git data first, then the supplied CSV with vitals | The Git-tracked file at `data/uhl_dataset_final.csv.gz` is byte-for-byte identical to the new attachment. It remains unchanged. |
| Use existing patient values only | All 100 ages, timestamps, complaints, seven readings and source estimated labels were checked directly against their original CSV records. No readings were generated, adjusted, rounded to fit a rule, or imputed. |
| 60 of 100 have a matching discriminator | Exactly 60 have one or more matched existing vital checks. The other 40 have none. This counts patients, not individual flags. |
| ML remains primary | The unchanged ML pipeline supplies the baseline. Only a strictly more urgent matched vital safety floor can raise the final category. The parallel keyword pathway does not decide the headline result. |
| Matched check should change triage when needed | 22 recommendations are raised (21 from 3 to 2 and one from 4 to 2). The other 38 matched patients already have an ML result as urgent as the required floor. All 40 unmatched cases retain their ML result. |
| Reduce Acuity 5 | Exactly five source cases finish at Acuity 5. The safety layer does not downgrade cases or create Acuity 5 results. |
| Investigate false Acuity 5 displays | Fixed a confirmed frontend parser defect: the string `None` was interpreted as Non-urgent because it contained `non`. Missing/status text now stays unassigned. Invalid fractional and contradictory category text also stays unassigned. This is a real defect; its contribution to the user's particular live screen was not independently established. |
| Preserve interface and controls | The concise evidence panel, all 17 expandable checks, model probabilities, clinician actions, original model artifact, original rules and source data remain. The compiled React assets include the parser fix. |

## What the recommendation now does

For the current vital-only layer, a check matches only when its measured value
crosses that check's high/low boundary. Missing observations are not negative
findings. A matched rule sets a maximum numerical acuity (1 or 2), and the final
result is the more urgent of the model result and that floor:

```text
final_acuity = min(model_acuity, matched_safety_floor)
```

If no check matches, the model result remains. If the model is already as urgent
or more urgent, it remains. A patient at Acuity 1 is never moved to 2 by a matched
Orange rule. A patient at Acuity 3 with an Orange floor becomes 2. Acuity 5 can
remain only when it is the ML result and no more urgent floor applies. Clinician
review/override remains a separate recorded action.

The 60 matched patients must finish in Acuities 1-2 under these existing rules;
therefore the earlier mostly-Acuity-3 sample cannot also retain 60 matched vital
checks. The revised results are:

| Acuity | ML baseline | Final recommendation |
|---|---:|---:|
| 1 | 1 | 1 |
| 2 | 37 | 59 |
| 3 | 48 | 27 |
| 4 | 9 | 8 |
| 5 | 5 | 5 |
| Total | 100 | 100 |

The ML result is unchanged for 78 patients. Exactly 22 are raised; no patient is
moved to a less urgent category. The 25% ML selection policy itself is unchanged;
it did not alter the modal class in these selected examples.

## Provenance evidence

Checked GitHub repository: `Dylanperry04/ai-triage-agentic-system`, default branch
`main`, remote HEAD `dfaaa75f9e39566542d338bf9b4658412c55396e` at the check.
The checked-out commit is the same. Its tracked
[source data](https://github.com/Dylanperry04/ai-triage-agentic-system/blob/dfaaa75f9e39566542d338bf9b4658412c55396e/data/uhl_dataset_final.csv.gz)
uses Git blob `9bfd12d110b7cf5931da18753d3eb8746295d4aa`.

The Git working file and `C:/Users/colle/Downloads/uhl_dataset_final.csv.gz`
both have 36,030,816 bytes and SHA-256:

`f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c`

The complete source has 777,176 data records. `row_number` in the selected JSON
is the original CSV data-record number, excluding the header. Source row numbers
are retained in pseudonymous identities; an identity is not reassigned to a
different record. No source CSV or workbook was overwritten.

The first attached workbook (`synthetic_with_triage.xlsx`, sheet `in`, header
range A1:W1) has demographics/presentations and `TriageCategory`, but lacks all
seven vital columns. Its record identifiers, age, presentation and triage label
agree with the CSV across all 777,176 records; raw date text is formatted
differently. The final vital-value verification uses the subsequently supplied
CSV with readings, not inferred values from the workbook.

The source release's existing metadata identifies synthetic vitals and estimated
labels. Those are properties of the supplied data. **This change creates no new
synthetic patient measurements.** It does not establish that the original values
were recorded clinical observations.

A new independent offline verifier is included:

```text
python scripts/verify_demo_source.py --source /path/to/uhl_dataset_final.csv.gz --output verification.json
```

It verifies the original source/model hash, reads the entire source, compares
every selected input and estimated label without rounding or tolerance, and
emits the mapping for all 100 source records. Missing, changed or duplicate
records fail verification. The accompanying record-verification JSON and
verification archive retain the executed results.

Final cohort: `uhl_curated_demo_100_v3`, 74,707 bytes, SHA-256:
`1fb5aad5fec098da3335065f7932da6e832104988b579d4c991209e5e561f691`.
The trained model hash is unchanged:
`7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b`.

## Selection and implementation

The offline scan uses existing supported adult records aged 16-74. It excludes
invalid readings, child-labelled presentations, pre-2020 COVID presentations and
inverted blood pressures. Within the requested matched/unmatched groups it
prefers source values nearer the source medians, within the 1st-99th percentile
bounds, and pain no higher than the source 90th percentile (8/10). These are
sampling limits, not clinical normal ranges or altered rule thresholds. A
complaint cap preserves variety; the sample covers 19 complaint types.

The model baseline has at least 50% assigned-class probability. The final result
matches the CSV's estimated acuity label. This is deliberately selected for
matched checks and favourable final-label agreement; **it is not an unbiased
accuracy evaluation, clinical validation or representative hospital case mix**.
Model evaluation reports remain unchanged and separate. The numerical safety
checks remain project research rules, not licensed official MTS content.

- Scanner/builder now select by the actual ML-plus-vital-floor result, rather
  than requiring agreement with the separate keyword pathway. The batch scan
  uses the same rule definitions and is tested against the serving evaluator
  on both sides of every boundary.
- The small repository independently reevaluates all selected observations to
  validate matched status, final acuity, floor and override metadata. It checks
  exactly 60 matched patients, 100 unique identities and the recorded category
  mix. Source-copy and real model verification are performed separately.
- Frontend category parsing accepts known categories, valid numerical acuities
  and consistent saved override labels. `None`, unknown/status values, invalid
  numbers and contradictory colours no longer become misleading categories.
- `/status/uhl` now exposes the verified serving-data hash and expected sample
  counts so the deployed dataset can be identified. Older builds may lack those
  fields; the current GitHub code has not been replaced by these local changes.
- Default runtime still loads only the small 100-case JSON in memory. Full CSV
  scanning and the large patient-cache seed are not part of startup. The full
  source remains an optional offline file and is excluded from the source ZIP
  and Azure/Docker runtime packages.

## Verification performed

- Source file hash and all 100 input records verified against the newly attached
  compressed CSV; its hash is identical to the Git source.
- Real Chrome/API pass over **all 100** fresh assessments: 60 matched, 22 raised,
  78 unchanged, final counts 1/59/27/8/5; no downgrades, correct served source hash,
  decisive check visible and all 17 checks expandable; no browser page errors.
- Focused backend/selection/rule/packaging checks passed (176 before adding the
  four source-verifier mutation checks); real inference reproduced all 100
  recorded baselines and safety results.
- Frontend: 86 tests passed in 11 files. Production Vite build passed.
- Local Azure preflight passed; the exact cohort rebuilt from recorded scan
  candidates with the same SHA-256. No new dependency versions were introduced.
- **Final extracted package, with the full CSV absent: 1349 backend tests
  passed, 21 pre-existing skips and 5 warnings, in 141.29 seconds.**
  Its Azure preflight, Python compile checks and ZIP hygiene passed.
- Two independent process starts from that extraction each returned 100 cases,
  with stable identities and no full CSV or patient SQLite cache. Local
  readiness/list timings were 8.39 and 7.64 seconds, not Azure measurements.
- Final ZIP integrity, byte comparison against the tested extraction, source
  record verification, credential-value hygiene and patch applicability passed.
- Existing warnings remain visible: two test-client dependency deprecations,
  one NumPy compiled-extension size warning, and two SciPy optimizer
  deprecations. The 21 skips concern retired full-MIMIC active-source tests;
  no tests were skipped to accommodate this revision. Docker execution and
  live Azure/LLM/SMS integration were not performed.

## Remaining limits

The new source package is local. GitHub/Azure have not been updated, no Azure
state was purged, and live cost savings were not measured. Existing clinician
records are retained. New observations and explicit clinician decisions may
legitimately change the distribution; the five Acuity 5 count is the verified
fresh source sample, not an artificial cap on future clinical decisions.

The original training-data labels are estimated and the existing vitals are
partly synthetic according to the source metadata. Complete approved MTS content
and additional clinical observations remain external requirements described in
`MTS_REQUIREMENTS_REVIEW.md`. This revision does not retrain the model, introduce
new clinical thresholds or establish clinical/regulatory approval.

The source ZIP excludes dependencies, runtime data and Git history. Its patch
omits `.env.example` so the old removed key cannot appear in diff context; the
sanitized template is included separately and in the source ZIP.
