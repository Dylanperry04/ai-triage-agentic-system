# Verified MIMIC-IV-ED Demo v2.2 schema

The project is locked to the six official MIMIC-IV-ED tables:

1. `edstays`
2. `triage`
3. `vitalsign`
4. `diagnosis`
5. `medrecon`
6. `pyxis`

## edstays

Verified columns:

```text
subject_id, hadm_id, stay_id, intime, outtime, gender, race, arrival_transport, disposition
```

## triage

Verified columns:

```text
subject_id, stay_id, temperature, heartrate, resprate, o2sat, sbp, dbp, pain, acuity, chiefcomplaint
```

## vitalsign

Verified columns:

```text
subject_id, stay_id, charttime, temperature, heartrate, resprate, o2sat, sbp, dbp, rhythm, pain
```

## diagnosis

Verified columns:

```text
subject_id, stay_id, seq_num, icd_code, icd_version, icd_title
```

## medrecon

Verified columns:

```text
subject_id, stay_id, charttime, name, gsn, ndc, etc_rn, etccode, etcdescription
```

## pyxis

Verified columns:

```text
subject_id, stay_id, charttime, med_rn, name, gsn_rn, gsn
```

Any mismatch triggers an exception in `scripts/verify_mimic_schema.py`.
