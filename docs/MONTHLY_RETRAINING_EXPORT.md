# Monthly UHL retraining-data export

This feature prepares clinician-labelled evidence for the separate, manual UHL
retraining process. It does not train a model or communicate with NVIDIA/Slurm.

## Schedule and reporting boundary

The application starts an idempotent check at service startup and repeats it at
`MONTHLY_RETRAINING_CHECK_INTERVAL_SECONDS` (default: 3600). The target is the
previous completed calendar month in `Europe/Dublin`. For example, the August
2026 file becomes eligible on 1 September 2026 and includes assessments from
`2026-08-01 00:00 Europe/Dublin` up to, but not including, `2026-09-01 00:00`.

Configure durable writable storage with `MONTHLY_RETRAINING_EXPORT_DIR` (or
`ALTER_DATA_ROOT`). `ENABLE_MONTHLY_RETRAINING_EXPORTS=false` disables the
background check but does not remove the ITD-only API.

## Eligibility and labels

Every exported row is keyed by the exact `workflow_run_id` and must contain the
pinned UHL model input schema plus model version/hash:

`age, month, hour, time_bin, season, presenting_complaint, temperature,
heartrate, resprate, o2sat, sbp, dbp, pain`

- `ACCEPTED_AS_PRESENTED`: eligible; final label is the accepted system acuity.
- `OVERRIDDEN`: eligible; final label is `final_clinician_acuity`.
- `ESCALATION_RESOLVED`: eligible only with an ED Doctor final acuity.
- request-more-information, open escalation, missing/invalid run links, missing
  inputs or provenance: excluded and counted by reason.

Traceability fields (`case_uid`, run ID, reasons/comments and reviewer role) are
CSV metadata only and must never be added to the model feature list.

## Idempotency, late labels, integrity and failure behaviour

Generation uses a per-month lock, atomic replacement, exact-run de-duplication,
and a SHA-256 manifest. Each check reconstructs the candidate from recorded
evidence. If its checksum and counts are unchanged, the existing artifact and
notification identity are retained. If a previously unresolved assessment later
receives an eligible final clinician label, the stable monthly artifact is
atomically replaced, its `revision` is incremented, and a revision-specific ITD
notification is created. This prevents an assessment finalized after the first
of the month from being omitted forever while keeping unchanged checks
idempotent. Download verifies the checksum and fails closed if the file was
altered or is missing.

The scheduled pass reconciles the previous month and every retained official
manifest (bounded by `MONTHLY_RETRAINING_RECONCILE_MAX_MONTHS`, default 120).
Consequently, a final label arriving more than one month late still revises its
original assessment-month cohort automatically. Months with no retained
manifest can be generated through the same ITD-only month API if historical
backfill is required.

In the Azure role-switcher demonstration only, ITD can download a clearly
labelled **current-month demo preview**. It is built in memory from current-month
evidence, is not saved as an official artifact, does not send a ready
notification, and does not alter the completed-month reporting rule.

In patient-data mode the durable evidence query begins at the exact UTC instant
corresponding to the Dublin month start. If a client cannot honour that bounded
query, or the configured read cap is reached, generation fails instead of
silently producing an incomplete file. Monitor the manifest/health state and
raise `AZURE_AUDIT_READ_MAX_LIMIT` and `MONTHLY_EXPORT_READ_LIMIT` together only
after sizing the durable query safely.

## ITD operation

1. Receive the in-app dataset-ready notification.
2. Open **ITD Console -> Monthly retraining data**.
3. Review month, generated time and eligible/excluded counts.
4. Select **Download Monthly Retraining Data**.
5. Transfer the verified CSV through the college-approved process and manually
   submit the separate retraining job.

API routes are `/retraining/exports`, `/retraining/exports/generate`,
`/retraining/exports/{YYYY-MM}`, and
`/retraining/exports/{YYYY-MM}/download`; all require ITD permissions. The
demo-only preview routes are `/retraining/preview/current` and
`/retraining/preview/current/download` and remain unavailable outside the approved
non-patient demo profile.
