# Model evidence and limitations

## Selected artifact

- Model: CatBoost, release 1.2.5
- Artifact contract: version 8
- Run ID: `68827117-f043-437f-96c8-7f02e322e40c`
- Model seed / split seed: 42 / 42
- Split sizes: 544,022 train; 116,576 validation; 116,576 test
- Selection basis: validation-only stability across model seeds 42-46
- Stability report: PASS; CatBoost ranked best by validation safety rank
- Evaluated artifact decision rule: validation-tuned ordinal cost,
  under-triage penalty 10 and over-triage penalty 1
- Current deployment rule: modal class unless a more urgent individual class
  has probability at least 25%; the most urgent qualifying class is used

## Held-out test evidence

| Metric | Result |
|---|---:|
| Accuracy | 0.24535 |
| Macro F1 | 0.12458 |
| High-acuity recall (levels 1-2) | 0.96019 |
| Acuity-1 recall | 0.84681 |
| Over-triage specificity | 0.22564 |
| Predicted urgent rate | 0.81929 |

These held-out figures describe the supplied artifact's evaluated ordinal-cost
rule. They do not yet quantify the current modal-plus-25% deployment rule; that
post-processing rule requires a fresh locked-test evaluation before its
performance can be claimed.

The high recall is paired with very low specificity and an 81.9% urgent
prediction rate. Overall discrimination is weak. The result is therefore not
evidence of clinical utility or safety.

## Known limitations

- The source data, triage vitals, and acuity labels are synthetic.
- `master_estimated_acuity` is a provisional research label, not clinician-
  adjudicated ground truth.
- The selected run is marked `single_seed_preliminary`; five-seed evidence is
  validation-only, not five independent held-out test evaluations.
- There is no prospective validation, silent-live evaluation, calibration study
  on real UHL activity, impact analysis, usability/human-factors assessment, or
  regulated clinical safety case.
- The two `DOA` records were unseen during fitting and are intentionally routed
  away from the model.
- CatBoost 1.2.5 emits a NumPy ABI-size runtime warning in the clean pinned
  environment. The artifact loads and inference tests pass, and the versions
  match the training contract, but this warning remains an upgrade risk that
  should be resolved by retraining/republishing under a currently supported
  binary stack.

## Verdict

**NO-GO for clinical or production decision support.** A controlled research
demo can proceed only with conspicuous research-use labelling, human review,
access control, no automated clinical action, and monitoring. Promotion requires
at minimum real-data validation, prospective clinical governance, subgroup and
calibration acceptance criteria, secure target-environment qualification, and a
new supported model artifact.
