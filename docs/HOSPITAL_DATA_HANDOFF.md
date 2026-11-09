# Hospital-data handoff and model replacement

This release is a working local prototype for the supplied synthetic UHL cohort.
A hospital model cannot safely be connected merely by renaming a CSV or replacing
a joblib file. The current release deliberately pins the dataset, feature schema
and trusted model digest and refuses incompatible artifacts.

## Current executable contract

The active model consumes exactly these ordered fields:

| Input | Meaning / unit |
|---|---|
| age | years |
| month, hour | triage-time calendar values in Europe/Dublin |
| time_bin, season | derived by the shared serving adapter |
| presenting_complaint | triage-time presenting complaint, not diagnosis |
| temperature | Fahrenheit inside this model contract; UI explicitly converts Celsius |
| heartrate | beats/min |
| resprate | breaths/min |
| o2sat | percent |
| sbp, dbp | mmHg, diastolic cannot exceed systolic |
| pain | numeric 0–10 |

`ml_training/uhl_synthetic/serving.py` owns the transformation and artifact
validation. Unknown complaints, unsupported ages, missing required inputs or an
incompatible artifact withhold ML prediction. Documented vital/MTS urgency floors
remain available independently. Missing ML never implies Acuity 5.

The current acuity model does not learn flowchart selection or discriminator
identification. The eight-chart research catalog proposes a chart by an explicit
complaint mapping; a nurse confirms it and records findings. A later ML pipeline
that predicts from discriminator features requires a newly versioned feature
schema, actual labelled observations, training code and independent evaluation.
Those future observations cannot be reconstructed from the current vital-only CSV.

## Hospital data needed before mapping and retraining

Agree a de-identified encounter key and, if permitted, an independently managed
pseudonymous patient grouping key for leakage checks. Do not provide names,
addresses or raw patient identifiers to this demo. Verify the actual column names,
units, time zone, missing-value codes and observation timing against the hospital
export. Keep original nurse category distinct from estimated labels, revised
category, AI category and post-triage outcomes.

For the requested process evaluation, obtain the nurse's chart and discriminator
IDs with the locally used MTS edition/definitions, observations and symptoms,
reference category, reviewer/source and recording time. Distinguish unavailable
items from assessed negative findings. Record revisions and reasons separately.
The reference APIs also accept adjudicated alternative charts. An independently
recorded reference must precede viewing AI; merely hiding AI on a screen is not
proof of independence.

## Proposed study split and release sequence

The supplied supervisor email proposes 2023–2025 for training and 2026 for
validation, followed by an on-site prospective silent-mode comparison. Confirm
the precise split and any internal tuning partition before training. Do not reuse
the 2026 validation period to select features, thresholds or model candidates.
Report missing data, abstentions, patient overlap and reference coverage.

1. Validate and map the actual approved hospital export locally. Preserve source
   provenance and quarantine invalid rows with aggregate error counts.
2. Train candidate models only on the agreed training portion. Freeze the
   selected feature schema, preprocessing, labels, threshold policy and artifact.
3. Evaluate the exact serving policy and each escalation stage on the separate
   reference data, across the four complaint groups, with counts and uncertainty.
4. Review rule transcription and unsupported explanation claims with qualified
   clinical reviewers. Retain independent and unblinded annotations separately.
5. Prepare a new explicitly named hospital release/profile and trusted hashes;
   test its adapter, prediction contract and failure paths before switching.
6. For a prospective silent-mode study, record nurse and AI assessments
   independently and prevent AI results from affecting normal patient care.

The synthetic replay command is reproducible from the application root:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python scripts/evaluate_serving_policy.py
```

It hashes the pinned inputs, verifies the original split assignment, invokes the
actual selection and vital-override functions, and exports aggregate results.
It never trains a model or fabricates MTS findings. It refuses a different dataset
because its purpose is to reproduce this specific synthetic experiment.

## Deployment boundary

The nursing/reviewer demonstration and exports work locally with synthetic data.
Patient-data and credentialed research collection remain disabled for the new
MTS reference streams. Before real records or on-site use, the hospital's agreed
identity, storage, retention, consent/study and clinical-validation arrangements
must be implemented and verified in the intended environment. This release does
not claim those unknown external arrangements are complete. No hospital dataset,
clinical reference annotations or approved deployment configuration was supplied.
