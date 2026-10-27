# Moving toward an ML model that uses discriminators

The intended direction is a clinician-supported research pipeline that predicts
acuity from structured discriminators available at the time of triage. The
current CatBoost artefact consumes its original 13 fields. It has not been
retrained in this audit, and it cannot accept a new discriminator schema safely.
The supplied guide describes workflow and verification checks, not the complete
training dataset or official MTS dictionary.

## Implemented foundation

`app/rules/discriminators.py` evaluates the 17 **existing provisional numerical
safety checks** from one definition set. Each evaluation contains a stable code,
observed value and unit, criterion, priority and explicit assessment status.
Unmeasured observations remain unknown. `WorkflowRunRecord` retains the evidence,
rule version, input snapshot, clinician-review linkage and a minimised explanation
snapshot. These records improve traceability now and preserve what the system
actually evaluated for later research. They are not labelled official MTS data.

The existing monthly retraining CSV still exports the original feature contract
and clinician labels. Its columns have deliberately not been silently expanded:
using new columns requires a newly trained, evaluated and versioned artefact.

## Required next implementation inputs

1. Obtain the licensed, current MTS presentation/discriminator dictionary and
   local UHL approval of its edition and adult workflow. Resolve the separate
   ICTS pathway for under-16s and the HSE older-person/EMEWS requirements.
2. Define an approved structured collection form. For each discriminator retain
   catalogue code/version, presentation, status (present/absent/not assessed),
   acquisition time, assessor/source and supporting observation/unit. Preserve
   each tier's exclusion confirmation, the selected discriminator, clinician
   corrections and the reason for any exception. Record oxygen context, rhythm,
   new confusion, infection focus, glucose/ketones and pain self/observer source
   where the approved presentation calls for them.
3. Supply a lawful, appropriately governed cohort with those observations,
   timestamps, encounter/patient grouping and independently adjudicated target
   labels. The current synthetic cohort does not contain these assessments.
   A vital-derived flag cannot stand in for airway compromise, haemorrhage,
   consciousness, clinician-assessed pain, or another unmeasured discriminator.

## Training and validation contract

- Build features only from observations available **before** the decision being
  predicted. Keep unknown separate from explicitly absent and retain catalogue
  version and assessment completeness.
- Keep the chosen final discriminator, assigned category, later overrides,
  clinician acceptance and later outcomes out of input features when they are
  consequences of the target decision. Predicting the category from its final
  selected discriminator's fixed urgency risks a circular task rather than
  learning independent clinical risk.
- Decide whether the target is reproducing MTS or predicting an independently
  validated urgency/outcome. Reproducing an exact approved discriminator-to-tier
  mapping may be better handled deterministically; ML value must be demonstrated
  against that baseline.
- Split by patient and time before preprocessing. Validate externally where
  feasible; evaluate missingness, age subgroups and presentation coverage.
  Retain adjudication provenance and prevent the system's own unreviewed outputs
  from becoming ground truth.
- Evaluate the **whole serving pipeline**, including its probability-selection
  policy and safety overrides, on a held-out cohort. The packaged existing test
  metrics use an ordinal cost-sensitive training policy, whereas the current
  serving path uses a modal/25% policy. Those metrics cannot be claimed as
  performance of the complete deployed workflow.
- Record calibration, per-class recall, severe under-triage, over-triage, subgroup
  uncertainty and clinician workflow outcomes. Predefine acceptance criteria and
  keep the test set out of operating-point selection.
- Version the new schema, preprocessing, catalogue, model and decision policy
  together. Reject incompatible inputs. Preserve the old artefact for rollback
  and complete prospective clinical and applicable device-regulatory evaluation
  before clinical use.

This describes the work that becomes executable when the catalogue, approved
workflow and discriminator-level cohort are available. No model trained on
fabricated clinical discriminators is included in this package.
