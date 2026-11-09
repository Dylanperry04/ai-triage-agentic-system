# Independent audit resolution and prototype review

30 October 2026. Local working revision based on Git `ddecf4689d4e2e995f0072cfbc119da30718e4ef`.
No commit, GitHub push, Azure deployment, retraining or dataset regeneration was performed.

## Outcome and interpretation

The supplied audit identified real integration faults. Each finding below was
checked against the source and supervisor requirements before implementation.
The supervisor requires traceable MTS process assessment and evaluation; her
email does not explicitly replace the student's ML recommendation with an MTS
classifier. The prototype therefore retains the requested hybrid policy: ML,
then escalation-only generic vital safeguards and positively assessed MTS
findings. The completed MTS comparison is separately named. Unknown observations
never imply a negative finding or automatically produce Acuity 5.

This report describes software verified with synthetic data. It does not certify
hospital readiness, clinical accuracy, regulatory status or official MTS approval.
No independent clinical reference cases were supplied. The research question
about differences between complaints remains unanswered until suitable reference
data are collected.

## Finding-by-finding resolution

| Finding | Disposition | Independently established cause and correction | Evidence / verification |
|---|---|---|---|
| F01: incomplete acceptance and policy ambiguity | VERIFIED AND FIXED | The review route accepted a headline without a completed checklist. `submit_review` now reads the exact linked assessment and requires completion or a deliberate exception of at least five characters. The UI shows chart/completeness and distinguishes the hybrid advisory. Switching the main result entirely to MTS was a policy suggestion, not an explicit supervisor requirement; the earlier ML requirement is retained. | `app/api/case_routes.py`, `app/schemas/review.py`, `frontend-react/src/views/Triage.jsx`; incomplete rejection, exception persistence and stale-run tests in `test_mts_workflow_integration.py`. |
| F02: urgent evidence lost when ML is unavailable | VERIFIED AND FIXED | Orchestration previously constructed the headline only after successful ML prediction and only applied an MTS floor to an existing category. Both vital safeguards and positive assessed MTS findings now establish a documented urgency floor independently. No evidence still means no category. A documented urgent vital floor also sets the escalation action, even without a completed chart. | `app/agents/orchestrator.py`; missing-ML Red integration, no-result and discriminator regressions; executable governance safety check now inspects the active safety/output layers. |
| F03: unsaved edits permit stale acceptance | VERIFIED AND FIXED | Checklist draft state was private to the child component. Draft state now reaches the decision panel. Acceptance and override are disabled while dirty; urgent escalation remains available. Patient/chart changes warn before discarding and stale writes remain server-rejected. A reload control recovers a retained stale draft. | `MTSChecklist.jsx`, `Triage.jsx`; UI regression tests and operated browser check: acceptance and override both disabled before save. |
| F04: checklist bypasses vital clock | VERIFIED AND FIXED | A separate save route did not perform the ordinary observation transition. Both paths now use `observation_transition` for per-reading provenance and the reminder. Only newly assessed/repeated core measurements advance the clock. A notes-only save does not. | `app/services/observation_update.py`, `mts_routes.py`, `case_routes.py`; integration asserts partial update, exact assessed fields and unchanged clock on notes-only save. |
| F05: MTS eligibility depends on ML input success | VERIFIED AND FIXED | Model metadata depended on successful feature snapshot construction and one global eligibility test excluded MTS-only output. The configured model and executable identities are recorded independently. ML, ML+safety, MTS and hybrid outputs have separate eligibility and coverage. A catalog mismatch does not automatically discard a valid ML comparison. | `workflow_run.py`, `decision_policy.py`, `mts_routes.py`, `mts_evaluation.py`; real missing-input run retains Red MTS/final comparison and zero ML coverage. |
| F06: historical results use another policy | VERIFIED AND FIXED | The historical artifact uses ordinal expected cost, while serving uses modal class with an individual more-urgent probability threshold of 0.25. The old numbers must not describe the current app. A reproducible aggregate replay invokes actual serving/override functions on the original 116,576 TEST rows, checking source/model/split hashes. UI separates historical results from the version-matched current replay. | `scripts/evaluate_serving_policy.py`, `artifacts/reports/serving_policy_evaluation.json`, `status_routes.py`, `ModelPerformance.jsx`. Replay completed. It is explicitly post-hoc, uses estimated synthetic labels and does not evaluate the new MTS layer. The audit's exact 90/100 statistic is not repeated as independently reproduced. |
| F07: clean frontend install fails | VERIFIED AND FIXED | `package.json` and the npm lockfile had different root Vite/Vitest declarations. Regenerated the lockfile, retained npm as the single package manager, removed the conflicting pnpm lockfile and aligned CI commands. | Actual original `npm ci` failure retained in private logs; repaired project `npm ci` installed 169 packages, reported zero advisories; 96 frontend tests and production build pass. |
| F08: explanation review displaces labels | VERIFIED AND FIXED | Category references and explanation annotations shared latest-case selection. Clinical references, guideline reviews and explanation reviews now have separate append-only streams and independent controls. Changing a clinical reference requires exact supersession and a correction reason; stale correction fails. Optional legacy combined requests split into those streams without erasing labels. | `mts_routes.py`, `mts_repository.py`, `MTSReferenceForm.jsx`, new Research Review workspace; separate explanation review preserves the original discriminator list; exact correction and stale correction tests. |
| F09: missing identification inflates recall | VERIFIED AND FIXED | Null identification cases were excluded. End-to-end micro metrics now treat missing identification as an empty prediction set, counting independently labelled findings as false negatives. Conditional-on-identification metrics and coverage remain available separately. | `mts_evaluation.py`; regression with one missing and one correct shock identification yields recall 0.5, F1 2/3 and conditional F1 1.0. |
| F10: multiple determining findings | VERIFIED AND FIXED | All matched rules in the most urgent band were called decisive. The first applicable rule in stored chart order is now the single determining discriminator. All matches and earlier unresolved findings remain in the trace. Category completion depends on unresolved higher-priority information. | `mts_assessment.py`; ordered airway/shock regression checks one determining finding while retaining both matches. |
| F11: contradictions count as complete | VERIFIED AND FIXED | A calculated positive and an explicit contrary finding were flagged but could still yield a completed MTS category. A conflict now withholds completed priority and marks resolution required. Documented positive evidence still supplies an escalation floor. | `mts_assessment.py`; temperature/clinical-finding conflict regression verifies no completed category and preservation of the urgent candidate. |
| F12: checklist breaks requested-information handoff | VERIFIED AND FIXED | The save route reset workflow state without setting ordinary response markers. The shared observation transition records requesting role, response time, the original request timestamp and reassessment target; request context remains until the responsible reviewer acts. | `observation_update.py`, `mts_routes.py`; ED-nurse checklist test returns requested information to the requesting ED doctor and preserves request status. |
| F13: complete journey omits new streams | VERIFIED AND FIXED | Audit loading joined old workflow streams only. It now joins reference, saved explanation, guideline-review and explanation-review streams, retaining exact run/reference/explanation IDs and source records in CSV. Dataset identity is retained so source filtering does not omit these events. Complete exports retain existing truncation safeguards. | `status_routes.py`, `audit_dashboard.py`; real application save/export regression checks all linked records and dataset identity. Demo reset archives the new streams together with their runs, preserving its existing archive-only behavior. |
| F14: labels lack assessment definitions | VERIFIED AND FIXED | The checklist provided names without usable assessment help. Added source-referenced paraphrased assessment guidance, numerical context and a declared age-16-and-over initial scope. Pediatric positive findings and unsupported age scope are rejected. | `app/rules/mts_definitions.py`, `mts_catalog.py`, `mts_routes.py`, `MTSChecklist.jsx`; checklist validation and numerical-boundary tests. A qualified local clinician must still validate transcription/wording and the local MTS edition. Software help is not approved clinical training. |
| F15: doctor review lacks MTS evidence | VERIFIED AND FIXED | Doctor review displayed generic readings without the frozen new assessment. A read-only exact-run endpoint and summary now show selected chart, determining finding, missing/conflicting observations, context and full rule trace. Doctor research annotations use the separate workspace. | `MTSReviewSummary.jsx`, `ReviewQueue.jsx`, `mts_routes.py`; doctor exact-run API/role-boundary integration and frontend loading tests. Existing historical review functionality remains. |
| F16: unsupported complaint points to missing action | VERIFIED AND FIXED | Triage-nurse help referred to a general observation action unavailable to that role. Unsupported chart state now exposes a real general-observation action. Recording measurements/context through it uses the same nursing authority already granted to checklist recording. Upload permission remains separate. | `MTSChecklist.jsx`, `Triage.jsx`, follow-up authorization in `case_routes.py`; current role-boundary and follow-up suites pass. Unknown complaints still do not receive an invented MTS chart. |
| F17: execution identity incomplete | VERIFIED AND FIXED | Catalog/model versions did not bind executable selection, threshold and execution behavior. Each saved assessment now includes source digests for serving/feature code, safety rules, MTS execution/catalog and hybrid policy, plus an application revision. Per-output evaluation checks the relevant identities. | `decision_policy.py`, `workflow_run.py`, `mts_routes.py`, redaction metadata tests; current replay is withheld in the UI when serving/model/safety or source data differs. These identify code, not clinical validation. |
| F18: entered readings relabelled as original source | VERIFIED AND FIXED | Reload after general updates merged readings but labelled them source-recorded. Per-field recording metadata now distinguishes original source, previously clinician-recorded, newly assessed, unavailable and not assessed. Reload includes actual recorded values and provenance separately from original source readings. | `observation_update.py`, `internal.py`, `case_routes.py`, `mts_routes.py`, `MTSChecklist.jsx`; provenance and source-forgery integration tests. |
| F19: laptop recommendation below long checklist | REQUIRES FURTHER VERIFICATION (responsive fix implemented) | The prior responsive rules stacked the advisory after a long checklist. Intermediate sizes now use a queue row plus checklist/advisory columns with independent scrolling; narrow layouts place the advisory before the checklist. The selected case, completeness and primary action remain together. | `atoms.jsx`; source/layout review and operated browser at actual 1910 CSS pixels. The browser viewport override did not apply its requested 1280x800 or narrow dimensions, so this release does not claim a successful live test at those exact sizes. A real laptop/narrow-device usability check is still needed. |
| F20: cover overlap and policy wording | VERIFIED AND FIXED | Imported title/subtitle geometry overlapped the screenshot frame. Repositioned/reduced the image, gave title/subtitle their own column and inserted a fresh prototype screenshot. Slide 9 explicitly names the historical ordinal-cost policy and separately shows current replay accuracy. Study phases and pending clinical references are stated. | Final editable 14-slide PPTX, all slides rendered and inspected, package/layout/import validation passed. One encoding defect found in the new 2023-2025 label was corrected and re-rendered. Native PowerPoint execution was not tested. |

## Supervisor requirement coverage

Software implementation is distinct from actual clinical evaluation.

| ID | Requirement | Final status and scope |
|---|---|---|
| R01 | Chart proposal from complaint | Implemented explicit mapping with abstention and nurse confirmation; no trained flowchart classifier is claimed. |
| R02 | Flowchart selection accuracy | Implemented, includes abstentions and API support for adjudicated acceptable alternatives; clinical references pending. |
| R03 | Applicable discriminators | Implemented nurse-assisted findings and source-based measured predicates for eight initial charts; no ML symptom extraction is claimed. |
| R04 | Discriminator precision/recall/F1 | Implemented end-to-end and conditional metrics with denominators; clinical labels pending. |
| R05 | Guideline adherence | Implemented separate exact-run clinician judgements for hybrid and completed MTS targets; missing targets cannot be adjudicated as completed decisions. Results pending. |
| R06 | Unsupported clinical claims | Implemented exact saved text/hash, separate claim reviews and reviewed/unreviewed counts. Actual clinical claim reviews pending. |
| R07 | Selected chart audit | Implemented chart identity, source page, confirmation and selection reason. |
| R08 | Identified findings audit | Implemented all matches plus one ordered determining discriminator. |
| R09 | Symptoms/observations audit | Implemented complaint, notes, states, values and per-reading recording metadata. |
| R10 | Executed rules audit | Implemented outcomes, criteria, conflicts, missing higher-priority findings and executable identities. |
| R11 | Final recommendation evidence | Implemented ML/safety/MTS stages and all linked explanation/reference records in journey export. |
| R12 | Structured checklist | Implemented dynamic nursing checklist; doctor sees exact saved checklist evidence. General editor remains for follow-up/unsupported presentations. |
| R13 | Relevant chart observations | Implemented varying clinical findings, oxygen context and PEFR. Seven retained ML inputs are explicitly identified as model/clinical-review readings, not invented mandatory local MTS requirements. Hospital mandatory/optional policy awaits nurse input. |
| R14 | Assessed status | Implemented explicit assessed state, never inferred from presence in CSV. |
| R15 | Measurements | Implemented finite/range/unit checks and shared observation transition. |
| R16 | Unavailable information | Implemented explicit unavailable state, clears a stale measurement. |
| R17 | Not yet assessed | Implemented unknown state; incomplete findings do not become absent. |
| R18 | Dynamic generation | Implemented versioned eight-chart catalog and source-referenced help, declared adult scope. |
| R19 | Nurse-led workflow | Implemented save/reassess, dirty protection, deliberate exceptions, clocks and requested-information handoff. Local usability/clinical sign-off pending. |
| R20 | Chest pain comparison | Implemented group metrics; actual clinical results pending. |
| R21 | Abdominal pain comparison | Implemented adult-scope group metrics; actual clinical results pending. |
| R22 | Trauma comparison | Implemented supported trauma charts; ambiguous mechanisms require nurse choice. Results pending. |
| R23 | Respiratory comparison | Implemented adult-scope group metrics; actual clinical results pending. |
| R24 | Classification accuracy | Implemented separately for each output with coverage and confusion matrices. |
| R25 | Undertriage | Implemented numerical prediction greater than reference, with denominator and Wilson interval. |
| R26 | Overtriage | Implemented numerical prediction smaller than reference, with denominator and Wilson interval. |
| R27 | Agreement | Implemented exact agreement and quadratic weighted kappa; arithmetic checked against sklearn. |
| R28 | Between-complaint research question | Evaluation framework implemented. Research conclusion requires independent hospital reference cases. |
| R29 | Adapt presentation | Implemented editable 14-slide audit-review deck with corrected policy claims and study plan. |
| R30 | Week 10 results | Current synthetic replay and historical results available; clinical performance explicitly not evaluated. |

## Other audit suggestions evaluated

- Moved the five probability bars and selected-class probability under collapsed
  Model details. They remain model-estimated probabilities, not validated
  certainty in the hybrid/MTS recommendation.
- Moved research annotation to a separate reviewer workspace. Revealing AI is
  deliberate; pre-existing independent-reference attestation and provenance are
  required in the UI. Hiding an output does not establish blinded methodology.
- Kept raw hashes/full JSON in technical audit details. Concise clinical evidence
  remains beside the recommendation.
- Removed unused demo email/password inputs. Demo selection explicitly does not
  authenticate a real staff identity. Backend role enforcement remains.
- Retired the legacy heuristic engine from the active UHL decision path. Kept it
  for historical dataset/API compatibility rather than deleting unrelated code.
  The requested generic vital-sign safeguard remains active and separately named.
- Shared the observation state/provenance/clock/handoff service instead of
  deleting the generic follow-up editor, whose context/upload functions remain
  relevant. This avoids an unnecessary UI rewrite and preserves existing roles.
- Kept historical training artifacts and compatibility modules. Their existence
  is not evidence that they drive the current UHL headline. A wholesale removal
  was not necessary to meet the supervisor's requirements.

## Current-policy replay

Original compressed CSV, model and split assignment hashes were verified before
loading the trusted artifact. No test rows were resampled, no model was trained
and no clinical findings were fabricated. Test count: 116,576 previously accessed
synthetic rows. The replay is a post-hoc comparison against estimated labels.

| Policy | Accuracy | Undertriage | Overtriage |
|---|---:|---:|---:|
| Historical ordinal-cost policy | 24.5351% | 1.1349% | 74.3301% |
| Current modal + 25% individual urgent-class policy | 42.8270% | 22.0466% | 35.1264% |
| Current policy + retained vital safeguard | 36.5324% | 19.7665% | 43.7011% |

These figures show a policy tradeoff. They do not demonstrate that the model is
clinically accurate, that the vital safeguard should be weakened or that any
policy is approved for hospital use. MTS comparisons cannot be reconstructed from
these rows because independently assessed MTS findings are absent.

## Verification actually executed

- Backend: full suite **1,410 passed, 21 skipped**, five warnings. The warnings
  concern Starlette/httpx deprecation, the existing environment's NumPy binary
  size warning and scipy optimization deprecation. No test result is inferred
  from the external auditor's reported counts. New/focused tests cover missing
  ML, acceptance/exception/stale run, measurement clock/handoff/provenance,
  independent reference correction, separate explanation review, ordered rules,
  conflict, missing-identification errors and exact journey links.
- Frontend: clean project `npm ci` succeeds; **96 tests pass in 13 files**;
  Vite production build succeeds. npm reports zero vulnerabilities for the
  installed locked dependency set. Deprecated Recharts/whatwg-encoding warnings
  remain; a major chart-library migration was outside this correction scope.
- Python runtime `pip check`: no broken requirements. Python compileall and
  `git diff --check` executed. Line-ending notices are not code failures.
- Operated the rebuilt local interface: demo sign-in, source-data chest-pain
  checklist, dirty acceptance/override blocking, save/reassessment, Red MTS
  escalation and separate research workspace. Test observations remain only in
  disposable local runtime state. Requested viewport overrides did not change
  the browser's measured size, so exact laptop/mobile visual checks remain open.
- All 14 updated presentation slides rendered and individually inspected;
  native tables retained, package/layout/font/import checks passed. No claim of
  opening the deck in native PowerPoint is made.
- Package and extracted-runtime verification are recorded separately in the
  accompanying current package receipt. The original dataset/model/cohort hashes
  remain pinned, with the existing 777,176-row source verification retained.

## Outstanding boundaries and future hospital data

No independent nurse/clinician reference dataset exists in this release. There
are zero clinical results to report for the supervisor's research question. The
100-case cohort remains curated for demonstration, with 60 matching generic vital
safety checks, and must not be used to claim unbiased performance.

The supplied fourth-edition text is the transcription source; no local qualified
clinical sign-off or confirmation of the hospital's approved edition was supplied.
Eight age-16-and-over charts are an initial scope, not full MTS coverage.

Hospital data mapping, a discriminator-feature training pipeline, temporal and
patient-overlap controls, clinical adjudication, and approved identity/storage
arrangements need the real export and agreed study protocol. Retraining alone
cannot supply those missing facts. `HOSPITAL_DATA_HANDOFF.md` states the current
contract and the next integration steps precisely. Real-patient collection was
not enabled in this synthetic prototype.

Live Azure behavior, SMS delivery and cloud LLM generation were not tested in
this local revision. No new deployment was authorized. The local prototype works
without sending SMS; optional cloud explanations require valid configuration.

## Supervisor-ready summary

The prototype now records a source-based nursing checklist and reconstructable
MTS decision evidence, preserves urgent documented findings when ML cannot run,
requires complete assessment or a recorded acceptance exception, and includes
exact evidence in doctor review and complete journey exports. Separate reference,
guideline and explanation reviews support the requested process and complaint
metrics without overwriting one another. Historical and current synthetic policy
results are clearly distinguished. Clinical performance remains awaiting the
independent nurse-led reference data proposed in the retrospective and silent-mode
study; the current demonstration makes no claim of clinical validation.
