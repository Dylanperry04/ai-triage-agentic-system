# UHL demo final deployment-readiness report

Date: 2026-09-01 (Europe/Dublin)

## Verdict

**GO for the documented controlled synthetic-data UHL demo profile after deployment through the repository's GitHub Actions workflow.** The supported profile is one Azure App Service instance and one Uvicorn worker. This is not a hospital-production safety certification, and no software can honestly be guaranteed to produce zero errors.

The supplied source ZIP is not a Kudu-ready dependency bundle. Commit/push this source release and use `.github/workflows/deploy-azure.yml`; that workflow rebuilds the frontend, installs pinned Python dependencies into `deployment.zip`, deploys the existing web/notification resources, and runs the hardened live smoke checks.

## Claim-by-claim decisions

| External claim | Verified? | Implemented? | Decision and evidence |
|---|---:|---:|---|
| ED Doctor confirmation was counted as initiating an escalation | Yes | Yes | `ESCALATION_CONFIRMED` is now confirmation-only. Unique escalated patients and staff attribution use initiation events. Tests cover request → ownership → resolution. |
| A schema-valid Foundry plan could answer a different question | Yes | Yes | High-confidence deterministic intents run first. Fallback plans must be both structurally valid and semantically compatible with the requested event, operation, grouping, current-state semantics, and server-derived time range. |
| The ITD endpoint only reached the planner through keywords | Yes | Yes | Every non-clinical ITD question now tries dynamic system facts and then the validated audit planner before returning unsupported. The duration question about supplying requested information works in the rendered UI. |
| Role/permission questions were a small phrase catalogue | Yes | Yes | Active role and permission answers are derived from the backend `ROLE_PERMISSIONS` matrix. The browser returned that ED Doctors cannot enter observations. |
| Oversize/malformed plans were silently truncated/coerced | Yes | Yes | The validator rejects the entire plan for excess queries/filters, unknown fields, invalid dates/types/ranges, invalid distinct/group fields, and operation-incompatible keys. |
| Foundry could silently substitute the wrong date | Yes | Yes | Recognised natural-language dates and explicit ranges are interpreted authoritatively by the server. Current-state calculations determine latest retained state before any activity-window filtering. |
| Negated clinician-review wording could pass explanation safety | Yes | Yes | Final output must contain an affirmative present/future review requirement; negated wording fails. The server also enforces the `The main reason this acuity level was suggested was...` opening and the concise ExplanationAgent-only result. |
| Clinical Supervisor remained visible | Yes | Yes, visibly | Active UI/API/docs wording was removed. Historical audit values and the legacy environment-variable alias remain readable internally for backward compatibility; they do not create or expose an active role. |
| Reset Demo left durable notifications/schedules/SMS work | Yes | Yes | The demo-only reset is coordinated against backfill/sweeps, archives file evidence and the local notification DB, deactivates SQLite/Azure notification and schedule rows, and cancels pending SMS generations/claims. It refuses unsafe patient-data or SMS-publishing modes. |
| Azure smoke could pass without a usable UHL model/data | Yes | Yes | `/ready` returns 503 unless hashes, exact row counts, actual model load/serving contract, derived case store, and writable runtime state pass. The smoke test now requires that contract and can exercise `/system/assistant`. |
| Simultaneous clinical decisions could both commit | Yes | Yes for supported demo profile | Per-case read-check-write transitions are serialized, revisions are persisted, stale/conflicting actions return 409, and client `action_id` supports idempotent retries. The same boundary protects reassessment and observation-alert transitions. Distributed scale-out remains intentionally unsupported. |
| Azure active cache on `/home` conflicted with startup optimisation | Yes | Yes | Startup defaults the active cache to `/tmp/alter-uhl-cache/uhl_cases.sqlite3`, uses `/home/data/cache` only as the persistent verified seed, and prewarms before Uvicorn starts. |
| Scheduler/notification SQLite locking was reproducible | Previously real | Yes | WAL setup occurs at repository initialization rather than on every connection, and lock retries/coordination are covered. The full scheduler-enabled suite passed. |
| Existing clinical queues/reassessment/disposition were broken | Previously real | Already corrected and retained | ED Nurse sees all non-closed observation cases, reassessment returns the exact case/run to the requesting clinician, ED Doctor has escalation and disposition views, and off-page escalation details contain the exact workflow-run link. |
| ED Nurse could see an automatically generated AI reassessment | Previously real | Already corrected and retained | Follow-up assessment is system-attributed for exact linkage; AI values are suppressed from the ED Nurse response/UI. |
| Monthly first export could permanently lose later labels | Previously real | Already corrected and retained | Retained artifacts can be revised idempotently when previously unresolved assessments receive labels. The official previous-month artifact and clearly labelled current-month demo preview remain separate. |
| Doctor changes were omitted from override evidence | Previously real | Already corrected and retained | Doctor final acuity changes preserve structured reasons, label source, analytics override counts, and retraining provenance. |
| CSV formula injection | Previously real | Already corrected and retained | Spreadsheet-control prefixes in free-text CSV cells are neutralised while raw audit evidence remains intact. |
| Monthly notification lacked a direct action | Previously real | Already corrected and retained | It opens the ITD console, where the consistent `Download Monthly Retraining Data` control is visible. |
| Audit Log CSV omitted most of the patient's journey and only exported the loaded page | Yes | Yes | The old nine-column browser export was replaced with a protected server-side complete-journey export. It emits one chronological row per recorded event, includes every safe normalised/source audit field, carries the current model-input/vital snapshot forward, and adds previous/new/delta/changed columns for each observation. Active Audit Log filters are honoured without the dashboard's page limit. If a source reaches the explicit 100,000-row-per-stream safety ceiling the endpoint returns 503 instead of silently producing a partial file. |
| Persisted idempotent clinical retries could fail after redaction | Yes | Yes | Valid action/review identifiers and the persisted action-payload hash are now preserved as safe metadata. Identical retries return the original result, while a changed payload with the same action identifier returns 409. |
| A multi-intent question could accept a Foundry plan covering only one requested event population | Yes | Yes | Semantic validation now accumulates every explicit audit population in the question and rejects plans with missing or unrelated event categories. Foundry remains a fallback after deterministic high-confidence parsing. |
| Cancelling a scheduler task could leave its worker thread writing during test/runtime teardown | Plausible and concurrency-sensitive | Yes, hardened | Thread-backed background jobs are shielded and allowed a bounded finish before lifecycle teardown completes. The full Python 3.11 scheduler-enabled suite passed without a database-lock failure. |
| The production dependency range could resolve a different W&B release | Yes | Yes | The only ranged production runtime requirement was pinned to the tested `wandb==0.28.2`. Dependency consistency and vulnerability audits passed. |
| `.env.example` was missing from the previous release archive | Yes, as a packaging defect | Yes, packaging only | The source template was not edited because the operator will update it. This final archive includes the existing `.env.example` unchanged; only a real `.env` remains excluded. |

## What “general ITD assistant” means

The assistant now supports natural-language questions expressible through the approved audit schema: counts, distinct patients, groups, percentages, min/max/average, trends, comparisons, durations, latest state, filters, and date ranges. It also has separate dynamic read-only sources for roles/permissions and safe system/model/configuration facts.

It deliberately does **not** execute generated SQL, read arbitrary files, expose patient-level content, or let Foundry calculate authoritative figures. Foundry may translate unfamiliar wording into a strictly validated plan; the backend performs the calculation. If a fact was never recorded, the assistant says so instead of inventing an answer. This boundary is necessary, not an incomplete connection to Foundry.

## Preserved requested functionality

- ED Nurse records/repeats observations and receives routine observation requests/overdue alerts.
- Triage Nurse runs/reviews the advisory model and can accept, override, request information, or escalate.
- ED Doctor owns final escalation review/resolution and patient disposition, but not routine observations.
- Researcher, ITD/security administrator, and Governance Auditor permissions remain intact.
- Audit records preserve actor, role, case, exact workflow run, timestamp, previous/new values, action, and reason/comment.
- The Audit Log download reconstructs the complete safe journey rather than exporting only the visible table: all 13 deployed UHL inputs, every vital snapshot, explicit changes, decisions, escalations, disposition, comments/reasons, actor/role, model metadata, and exact workflow identifiers are included where recorded.
- Analytics reconstructs vital-sign and model-estimate trends and labels improvement/deterioration as advisory.
- Only the final ExplanationAgent summary crosses the API boundary, with a short enforced explanation and clinician-review requirement.
- Monthly CSV contains every deployed UHL input plus exact run/case linkage, prediction, final label, decision/status, reasons/comments, reviewer role, label source, model version, and model hash.
- No automatic training, NVIDIA login, Slurm submission, or deployed-model replacement was added.

## Verification evidence

- Backend: Python 3.11.9, both schedulers enabled: **1,152 passed, 21 intentionally skipped, 0 failed**.
- Frontend: Node 22.12.0 / pnpm 10.34.5: **55 passed across 8 files, 0 failed**; production build passed with 2,076 modules.
- Dependency/security checks: `pip-audit` and `pnpm audit` reported no known vulnerabilities; `pip check` passed.
- Static/deployment checks: Python compilation, AutoGen imports, Azure preflight, `startup-backend.sh` syntax, notification Bicep compilation, asset hashes, and package hygiene passed.
- UHL assets: dataset SHA-256 `f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c`; model SHA-256 `7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b`.
- Deep readiness: model loaded and serving bundle validated; 777,176 source rows; 777,174 model-scope rows; runtime storage writable.
- Local deployment-style smoke: `/`, `/health`, `/ready`, `/status/uhl`, `/runtime/status`, notifications, notification health, and `/system/assistant` all passed.
- Browser: the current FastAPI/React build ran locally; a Triage Nurse logged and accepted an assessment, the accepted case remained available to the ED Nurse, the nurse recorded repeat observations, and ITD opened the complete Audit Log export. The downloaded CSV contained **46 chronological events and 119 columns**, linked the exact workflow, preserved the accepted decision, and recorded heart rate **63.3 → 72.0 (delta 8.7)**. The protected endpoint returned HTTP 200 with `X-Audit-Export-Complete: true`.

## Deployment requirements

Use the existing workflow with the synthetic demo configuration documented in `.env.example` and `infrastructure/azure_deploy.md`. Keep:

- one App Service instance;
- one Uvicorn worker;
- `PATIENT_DATA_MODE=false`;
- the demo authentication/role-switcher settings for the scripted presentation;
- `ALTER_DATA_ROOT=/home/data`;
- active UHL cache on `/tmp` and persistent seed under `/home/data/cache`;
- monthly export and overdue-vitals schedulers enabled;
- SMS disabled unless the matching Azure notification stack is provisioned and verified.

The operator explicitly chose to update `.env.example`; this release preserves and packages the existing file unchanged. Azure App Service configuration values remain the actual runtime authority, not the template file.

## Remaining limitations

- Azure CLI was not logged in, so no live App Service or Function was changed and subscription/resource configuration was not independently queried.
- Local Azure OpenAI credentials were intentionally absent. Prompt, validation, fallback, and API contracts were tested, but a live Foundry response must be confirmed by the post-deployment smoke/walkthrough.
- No live SMS provider or Azure notification Function delivery was performed locally.
- In-process clinical transition locks do not support multiple App Service instances/workers. That is acceptable for the documented demo; scale-out requires a shared transactional state store.
- The dependency audits found no known advisories at the time of testing; this is not a permanent future-vulnerability guarantee.
