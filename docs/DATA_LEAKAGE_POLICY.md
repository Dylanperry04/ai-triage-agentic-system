# Data leakage policy

This project separates triage-time inputs from retrospective labels.

## Allowed initial triage inputs

These can be used in a triage-time workflow:

- `subject_id`
- `stay_id`
- `intime`
- `gender`
- `race`
- `arrival_transport`
- `chiefcomplaint`
- `temperature`
- `heartrate`
- `resprate`
- `o2sat`
- `sbp`
- `dbp`
- `pain`

## Retrospective fields — not used as triage input

These are kept for audit/evaluation only:

- `outtime`
- `disposition`
- `acuity`
- `diagnosis.icd_code`
- `diagnosis.icd_title`
- `diagnosis.icd_version`

## High-risk leakage fields

These require explicit time-filtering and clinical approval before use:

- repeated `vitalsign` records after triage
- `pyxis` medication administration after arrival
- any diagnosis field
- final ED disposition

## Rule

The default pipeline exports two files:

1. Full internal cases with all preserved source data.
2. A triage-input-only export that excludes known leakage fields.
