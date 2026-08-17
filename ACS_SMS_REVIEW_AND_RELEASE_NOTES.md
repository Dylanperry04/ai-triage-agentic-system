# ACS SMS review and release notes

This source package keeps the working 22.4/UHL application and its in-app
notification workflow intact. SMS is an optional, disabled-by-default secondary
prompt. No recipient number or ACS credential is stored in this repository.

## GitHub `main` integration

The v25.7 archive was merged additively into the existing GitHub `main` branch.
The integration deliberately preserves the branch's newer pseudonym-aware UHL
cache/resolver invalidation fixes, startup cache pre-warming, readable
`SignIn.jsx` refactor, and existing `.env.example`. The React production assets
were rebuilt from that retained source. The obsolete
`.github/workflows/main_triage.yml` workflow was removed, while no other
pre-existing repository file was deleted. Eight model-report CSV byte changes
are included so their CRLF-encoded files match `SHA256_MANIFEST.csv` exactly.

Merged-tree validation collected 1,106 backend tests: 1,085 passed and 21 were
skipped, with no failures or XFAILs. The React suite passed 47/47 and the
production build transformed 2,076 modules. The archive-only results recorded
later in this document remain the provenance for the unmodified ZIP.

## Feedback adjudication

All fourteen SMS-DEMO findings were checked against the implementation rather
than accepted from the review document alone. Each described a real failure
mode or a justified deployment/operational gap and is addressed:

1. Historical backlog: activation watermark, case allowlist, in-app/SMS
   eligibility separation, cap-one canary, and a read-only pre-enable report.
2. Obsolete work: terminal cancellation plus revalidation after claim and
   immediately before the ACS call; acknowledgement policy remains explicitly
   non-cancelling for a still-current event.
3. Publication races: leased, generation-aware outbox claim and completion.
4. Schedule races: exact version and generation consumption; a stale worker
   cannot consume or cancel a replacement schedule.
5. Worker crashes: expired pre-submission claims are requeued as a new
   generation; stale `sending` work becomes ambiguous; Function completion is
   allowed only after a durable successor or terminal result exists.
6. Temporal identity: UTC canonicalisation with microsecond precision.
7. Delivery ordering: delivery events are stored before correlation, applied
   idempotently, and repaired after partial failure or out-of-order arrival.
8. Reconciliation: workflow-to-notification repair retries continuously and
   reports complete/degraded/failed state.
9. Acknowledgement: the browser issues one backend command. It converges after
   a partial durable-store failure and handles a Service-Bus-created due alert
   before the legacy five-minute sweep persists its active flag.
10. Bell accuracy: paginated authoritative replacement, degraded fallback
    merge by semantic event identity, quiet initial/read loading, and removal of
    server-inactive rows in the same session.
11. Releases: normal deployment stages both web and Function code from one
    commit, deploys the worker first to fail closed across schema changes, and
    verifies matching build IDs without changing SMS flags.
12. Health: publication state is separate from the Function heartbeat, age,
    enablement state, and build identity.
13. Queue privacy: operations documentation now states that Microsoft ACS
    delivery events contain the destination number; delivery events expire from
    the active queue after one day and DLQs require explicit secure cleanup.
14. Local development: Vite proxies `/notifications` to the backend.

## Deliberate boundaries

- Sender registration, Azure provider registration, resource creation,
  deployment, Key Vault secret entry, and a chargeable handset test are not
  performed by this package.
- Live SMS starts disabled. The first handset test requires an approved `ALTER`
  sender, one current activation timestamp, exactly one allowlisted case, a
  daily limit of one, a clean pre-enable report, and separate approval.
- The demonstration routes through one Key Vault recipient. Individual nurse
  and ED-doctor phone routing remains blocked until an authoritative staff
  directory, assignment source, consent policy, and fallback owner exist.
- The newer GitHub UHL cache/resolver correctness and pseudonym-rotation fixes
  remain active; the notification integration does not replace them.

See `docs/ACS_SMS_OPERATIONS.md` for the controlled rollout and rollback steps
and `docs/NOTIFICATION_ARCHITECTURE.md` for the data flow.

## v25.7 deployment-readiness review disposition

The two independent v25.6 reviews were reproduced against the exact archive,
not treated as instructions. The following findings were confirmed and fixed:

- Clean CI did not install the retired Streamlit test manifest, so pytest could
  not collect. The deployment workflow now installs
  `requirements-legacy-ui.txt` only in its test environment and performs an
  explicit clean collection check. Streamlit remains outside the production
  runtime package.
- The UHL repository test could write completed and hidden `.building` SQLite
  caches beneath `data/cache`. CI now redirects that cache to runner temporary
  storage, rejects source-tree cache writes, excludes and defensively removes
  `data/cache`, ignores it in Git, and tests both completed and hidden cache
  names against package hygiene.
- Two checks against one frozen Function settings object did not prove live
  rollout revocation. App Settings remain the operator input, but a monotonic
  policy record in the existing notification repository is now the dispatch
  authority. Both SQLite and Azure Table implementations reject stale policy
  writers; the worker point-reads the current policy after claim and immediately
  before transport, atomically records its version on `sending`, and cancels
  revoked unsent work. Configuration readback now verifies both applications
  and waits for the restricted health endpoint to prove durable convergence.
- Rollback and missing-worker kill switches now assign and read back a newer
  policy version as well as forcing SMS flags off. Future pre-SMS rollback can
  persist the durable kill switch before replacing the current web build.
- The post-deployment workflow now invokes the tested smoke script rather than
  maintaining a second inline implementation. That check verifies web/UHL
  health, the durable API, build/worker identity, and policy convergence before
  tagging.
- The Docker findings were real for that optional deployment path, but they did
  not block the primary ZIP-based App Service workflow. The image is now an
  explicit backend-only service, copies the referenced runtime manifest, and
  implements the documented `SKIP_AZURE` build argument.
- The pytest advisory was real but test-environment-only. CI now pins 9.0.3.
- v25.6's XPASS claim was inaccurate. Two clean Python 3.11 executions produced
  one expected non-strict XFAIL, so the manifest and README now say XFAIL.

Clean supplied-archive validation: 1,106 backend tests collected; 1,084 passed,
21 skipped, one expected XFAIL, and zero failed. The focused notification and
infrastructure suite passed 71/71; deployment smoke passed 3/3; React passed
47/47 and the production build transformed 2,076 modules. Dependency
consistency, runtime vulnerability audit, Python compilation, Azure preflight,
Function registration, workflow YAML and all 27 Bash blocks, PowerShell parsing,
Bicep build/lint, package hygiene, and source/archive safety passed.

Authenticated ARM validation/what-if, resource deployment, registered `ALTER`
sender availability, Key Vault recipient entry, and a chargeable handset canary
remain external gates. They are not source-package defects, but live delivery
must not be claimed until those environment-specific checks succeed.

## v25.6 independent-review disposition

Three independent v25.5 assessments were checked against the source, executable
probes, the deployment workflow, the selected model bundle, and primary Azure
and upstream security documentation. Confirmed findings were fixed without
changing the accepted notification types or single-recipient demonstration
mode:

- Dispatch now re-evaluates the current activation watermark and case allowlist
  after claiming and immediately before ACS transport. Revoked, unsent work is
  atomically cancelled as `rollout_policy_revoked` in both repository backends.
- The cap-one activation gate now requires zero eligible notifications and zero
  eligible schedules, preventing an old canary alert from sending on enablement.
- Historical rollback disables and verifies both SMS flags before checkout and
  can deploy a pre-SMS commit without requiring notification-era files.
- A missing Function forces and verifies web publication/submission off.
- The production manifest now contains only active FastAPI/UHL/notification
  runtime dependencies. Streamlit, XGBoost, LightGBM, PyArrow, and NCCL are not
  installed into the web package; CatBoost remains because the selected serving
  bundle uses `CatBoostClassifier`.
- FastAPI, Starlette, and python-multipart were upgraded together. The exact
  runtime manifest passes `pip check`, the complete regression suite, and an
  independent vulnerability audit with no known findings.
- CI enforces compressed and expanded package limits, checks the live
  `WEBSITE_RUN_FROM_PACKAGE` setting, verifies all four Function registrations,
  verifies the exact post-clean deployment tree in an isolated Python process,
  and performs read-only web/notification/build/heartbeat checks before tagging.
- The activation script now configures and reads back the publish-only staging
  state on both the App Service and Function.

Validation after v25.6: 1,080 backend tests passed, 21 skipped, one accepted
non-strict XPASS, and no failures; 69 notification/infrastructure tests and two
deployment-smoke tests passed; 47 React tests passed; the Vite build transformed
2,076 modules. Python
compilation, Azure preflight, Function registration, workflow YAML and shell
parsing, PowerShell parsing, Bicep build/lint, dependency consistency, and the
runtime vulnerability audit all passed.

An upper-bound local reproduction of the dependency-inclusive App Service
bundle contained 11,569 files, occupied 868,252,683 bytes expanded, and
compressed to 289,524,349 bytes. It is below both workflow budgets and the
one-gigabyte run-from-package ceiling. The GitHub workflow recalculates these
values for every deployment and stops before Azure deployment if they regress.

The source is ready for controlled Azure validation. ARM validation/what-if,
resource provisioning and RBAC propagation, registered `ALTER` sender
availability, Key Vault recipient entry, and an explicitly approved cap-one
handset test remain external gates; this release does not claim that those live
steps have occurred.

## v25.5 independent-review disposition

The v25.4 independent review and its six executable probes were checked against
both this implementation and the accepted pre-SMS v25.2 archive. Review text
was treated as evidence, not as a change request.

| Finding | Decision | v25.5 result |
|---|---|---|
| F-01 information requests | Rejected as a false contract assumption | The original v25.2 bell explicitly generated only overdue-vitals and pending-escalation alerts. Information requests remain visible workflow queue/status changes; creating a new bell and SMS would change the accepted application. A regression test freezes this boundary. |
| F-02 same-generation schedule race | Confirmed, in scope | A losing duplicate rereads the schedule and preserves/activates the deterministic notification already recorded by the winning generation. Exact SQLite and Azure Table interleavings are tested. |
| F-03 terminal stale-flag recreation | Confirmed, in scope | Terminal or `notifications_suppressed` reconciliation now cancels/deactivates and returns before any producer can recreate work. All declared terminal states are tested. |
| F-04 one-handset routing | Correct observation, not a defect | Every eligible demo alert intentionally resolves to the one Key Vault recipient. Production staff routing remains fail-closed and unimplemented. |
| F-05 disabled worker settlement | Confirmed, in scope | An in-flight disabled worker atomically advances to a new unpublished generation before completing the old broker message. Bare `sms_disabled` is no longer a safe Function outcome. Both repositories are tested. |
| F-06 person-target mutation authorization | Confirmed latent defect, safe to fix now | List, read, and acknowledge now share the same role plus optional exact `target_user_id` boundary. Both direct mutation endpoints are tested. |
| F-07 Azure acknowledgement audit fields | Confirmed, in scope | SQLite and Azure Table now preserve the first acknowledgement identity/time even when workflow reconciliation deactivated the bell first. SMS state is unchanged. |
| F-08 inherited web dependency advisories | Confirmed as inherited/non-SMS scope | No dependency pins were changed. Broad framework/model-library upgrades would modify the previously accepted working deployment and require their own compatibility/security release. |
| F-09 Service Bus scaling permission | Confirmed by Microsoft guidance, in scope | The Function now receives namespace-scoped Azure Service Bus Data Owner, replacing separate sender/receiver assignments, so the scale controller has the required management-read permission. |
| F-10 Azure Table scan/index design | Real future scale concern, not a demo blocker | The personal demo is low-volume and capped at 100 SMS attempts/day. Repartitioning/migration is intentionally deferred rather than mixed into this correctness repair. |
| F-11 release identity | Confirmed traceability issue | `RELEASE_MANIFEST.json` distinguishes the 22.4/UHL application version, v25.2 source lineage, and v25.5 SMS release; the deployment build remains commit-bound through `ALTER_BUILD_ID`. |

Validation after these changes: 1,065 backend tests passed, 21 skipped, one
accepted non-strict XPASS, and no failures; 59 notification/infrastructure tests
passed; 47 React tests passed; the Vite production build transformed 2,076
modules; Python compilation, Azure preflight, Function registration, GitHub
workflow parsing, PowerShell parsing, and Bicep build/lint all passed. Live ARM
validation, what-if, sender registration, deployment, and handset delivery remain
external gates because this workstation is not authenticated to Azure.
