# Supervisor feedback implementation — local review

Date: 29 October 2026. Base Git revision: `ddecf4689d4e2e995f0072cfbc119da30718e4ef`.
The new changes are uncommitted local working files. **Nothing from this
revision has been pushed to GitHub or deployed to either Azure application.**
This follows the student's latest instruction to evaluate a local file first.

## 1. Feedback analysis and requirement coverage

| Supervisor requirement | Interpretation and implementation | Status |
|---|---|---|
| Flowchart selection accuracy | Compare the automated complaint-to-chart proposal with an independently supplied clinician reference. Count abstentions in the denominator. The current proposal is an explicit mapping, not a trained flowchart classifier. | Software implemented; clinical results awaiting references |
| Discriminator precision, recall and F1 | Compare the identified discriminator set with independently annotated applicable findings; export TP/FP/FN and annotation coverage. Missing references are not empty negative sets. | Software implemented; clinical results awaiting references |
| Guideline adherence | Record an independent clinician judgement of the decision's consistency with the relevant MTS rules; calculate consistent / adjudicated decisions. Engine self-agreement is not clinical adherence. | Software implemented; clinical results awaiting adjudication |
| Unsupported clinical claims | Save exact displayed explanation text and hash; link a clinician claim review to its exact explanation/run. Count explanations containing one or more unsupported symptoms, findings or rules. Unreviewed does not mean zero unsupported claims. | Capture/calculation implemented; no actual clinical reviews supplied |
| Reconstructable audit trail | Freeze complaint, proposed/selected chart, selection reason, symptoms, measurement/status snapshots, every rule result, matched/decisive findings, category changes and source/model/catalog versions with the exact workflow run. Display in the audit UI and preserve in journey CSV. | Implemented |
| Structured observation checklist | Replace the old vital tiles with a dynamic chart-based checklist. Distinguish source-recorded values, assessed findings, unavailable data and not-yet-assessed information. Require the context needed for numerical rules. | Implemented for eight initial charts |
| Four complaint groups | Chest pain, adult abdominal pain, respiratory symptoms and trauma. Trauma includes major trauma, head injury, limb problems, torso injury and wounds; ambiguous injury labels need nursing chart selection. | Implemented within this declared scope |
| Category performance within each group | Report accuracy, undertriage, overtriage, exact agreement, weighted kappa, prediction coverage and confusion matrices separately for ML, displayed ML+safety, and completed MTS assessments. Export denominators and accuracy confidence intervals. | Software implemented; valid clinical comparisons awaiting references |
| Week 10 presentation | Update the confirmed 14-slide draft, preserving its design and editable content. Cover the research question, checklist, audit, metrics, limits and reference protocol. Correct the matrix to the exact saved experiment report. | Implemented in the accompanying editable PPTX |

The supervisor's request combines **application changes** with a **clinical
research evaluation**. Software tests can verify the former. They cannot
substitute for the independent clinical reference annotations needed for the
latter. The student confirmed that no such additional labels are available.

## 2. Confirmed defects and integration problems corrected

- A previous checklist could remain asserted after observations changed through
  the general vitals workflow. Current assessments now invalidate that checklist
  while preserving the earlier frozen trace.
- Saving a checklist after a complaint update could compare against the original
  complaint, rejecting the chart correctly proposed by the current screen.
  Saving and reading now use the same merged current observations/complaint.
- A caller could submit an altered measurement while labelling it source-recorded.
  The server now verifies source-recorded values against the actual record,
  including temperature conversion; new observations must be explicitly assessed.
- Unavailable measurements previously retained stale source values in the merged
  case. An explicit unavailable value now clears the old measurement and reaches
  missing-data safeguards.
- Privacy redaction could corrupt new catalog hashes/revision IDs, breaking exact
  run comparisons. Narrow metadata validation preserves valid digests and UUIDs,
  including valid UUIDs containing no letters, while retaining raw-identifier
  removal and free-text scrubbing.
- Observation saves could trigger competing preview/logged assessments on refresh.
  The UI updates its observation-revision tracker before the refresh and new run.
- Research form state could outlive the displayed workflow run. It is remounted
  for the exact case/run so reference input cannot silently transfer to another run.
- Selecting only the latest case review could lose reviews of earlier saved
  explanations. Clinical category metrics deduplicate by case; claim reviews
  deduplicate independently by saved explanation.
- Plain text replacement in the imported presentation retained old bibliography
  hyperlinks. Reference boxes were recreated at their original positions and
  linked only to their actual sources.
- The draft confusion matrix did not equal `selection_record.json.test_metrics`.
  The updated slide uses the exact saved matrix and clearly describes its
  estimated-label experiment and substantial overtriage.

These fixes are supported by focused regression tests or presentation package
inspection. They are not claims that every possible software or clinical defect
has been eliminated.

## 3. Main code modifications

| Area | Files | Purpose |
|---|---|---|
| Source rules | `app/rules/mts_catalog.py`, `mts_assessment.py` | Versioned eight-chart catalog, source pages, full hash, complaint mapping, explicit evidence execution |
| Observation contract | `app/schemas/mts.py`, `internal.py` | Strict states, finite measurements, chart scope, version binding, current-checklist validation |
| Decision integration | `app/agents/orchestrator.py`, `app/schemas/workflow.py`, `workflow_run.py` | Separate MTS trace; positive findings may raise urgency; frozen assessment record |
| APIs and storage | `app/api/mts_routes.py`, `case_routes.py`, `safe_dto.py`, `app/storage/mts_repository.py`, `app/main.py` | Nurse saves with concurrency checks; exact explanation/review linkage; research endpoints |
| Research metrics | `app/analytics/mts_evaluation.py` | Independent-reference eligibility, complaint groups, error directions, agreement, confidence intervals and missing-data handling |
| Security | `app/security/authz.py`, `redaction.py`, `agent_gateway.py` | Existing role enforcement, metadata integrity and restricted explanation evidence |
| Explanations | `app/agents/autogen_multi_agent_team.py`, `llm_explanation_agent.py` | Distinguish matched findings, unknown states, incomplete charts and actual MTS escalation |
| UI | `MTSChecklist.jsx`, `MTSTrace.jsx`, `MTSReferenceForm.jsx`, `MTSEvaluation.jsx`, triage/audit/model-evidence views and API client | Integrated nursing checklist, evidence display, review capture and comparison |
| Audit export | `app/analytics/audit_dashboard.py` and export regression | Expose and preserve the complete MTS execution trace |
| Local evaluation | `Run-Local-Review.ps1`, `LOCAL_REVIEW.md` | Python 3.11 setup, compiled UI, local persistence, loopback-only server and SMS disabled |
| Release checks | `scripts/azure_smoke_test.py`, workflow source | Optional future verification of the exact catalog and complaint-metric schema; no deployment executed |

## 4. Technical and clinical research decisions

**Recommendation policy.** Retain the trained ML prediction and existing
generic vital safeguards. A positively assessed, chart-specific MTS finding
provides an urgency floor and may raise the advisory. It never lowers urgency.
Unknown higher-priority findings prevent a completed MTS classification; they
do not cancel a known positive urgency floor. Major trauma has no fabricated
Green/Blue fallback. No MTS category is inferred from normal or missing vitals.

**Source scope.** Rules are a research transcription of the supplied *Emergency
Triage*, Manchester Triage Group, fourth edition (printed 2027), ISBN
9781394266654. Initial printed chart pages are 68, 94, 122, 128, 132, 152, 162
and 176. This is source attribution, not a claim of clinical approval. The source
PDF is not redistributed. The publisher record is
[Wiley](https://www.wiley.com/en-gb/emergency-triage-4th-edition-p-9781394266654).
The existing Irish waiting-target mapping is retained; see the earlier
`MTS_REQUIREMENTS_REVIEW.md` and
[HIQA Mid West monitoring report](https://www.hiqa.ie/sites/default/files/2025-09/MWR/WS5-Regulatory_Monitoring.pdf).

**Numerical context.** Book-defined SpO2 predicates use assessed oxygen-therapy
context. Peak-flow predicates require measured and predicted/personal-best
values. Threshold boundary tests distinguish strict from inclusive comparisons.
Clinical findings require an explicit nursing assessment. A universal
heart-rate-150-to-Red MTS rule was not invented.

**Data/model integrity.** The original CSV, existing selected 100 records and
model are unchanged. The 60/100 matching checks refer to the retained generic
vital safeguards, not 60 independently labelled clinical MTS assessments.
Baseline final category counts remain 1 / 59 / 27 / 8 / 5. The new checklist
starts without invented nursing findings. No model training was performed.

**Reference independence.** Chart/category/discriminator primary metrics use
only references explicitly attested as recorded independently before viewing
AI. Unblinded annotations are stored separately and excluded from those primary
metrics. The software records that attestation; it cannot prove a reviewer was
actually blinded. Explanation reviews necessarily see the output and therefore
count for either category-reference collection method.

**Metric interpretation.** Undertriage means predicted category > reference
category, because 1 is most urgent. Overtriage is the reverse. Exact agreement
equals classification accuracy; quadratic weighted kappa also considers the
size of the ordinal disagreement. Undefined denominators/kappa remain null.
The curated 100-case demo is not an unbiased evaluation cohort.

**Research storage.** Use the existing guarded, redacted append-only storage
with exact case/run links and version/model hashes. Current aggregate results
exclude different model/catalog versions and duplicate case reviews. New
research collection is disabled in patient-data/credentialed deployments.
References never become model inputs or trigger automatic retraining.

## 5. Executed verification

- Full backend suite: **1,401 passed, 21 skipped**. The 21 skips are existing
  retired full-MIMIC active-source contracts, identified in `tests/conftest.py`;
  none was added to bypass this work. Five warnings concern existing dependency
  deprecations/binary-interface reporting; test execution passed.
- React/Vitest: **91 passed across 12 files**, including new checklist state,
  chart selection, concurrency payload and unavailable-evaluation tests.
- Production Vite build: passed, **2,081 modules** transformed. Compiled assets
  are included so local review does not require Node.js.
- Focused real-model/API integration: **13 passed**, covering saved traces,
  escalation-only behavior, access restrictions, unavailable values, stale
  saves, complaint updates, source-value integrity and exact explanation review.
- Known-case research metric tests independently check weighted kappa against
  scikit-learn and verify under/overtriage direction, exclusions and null results.
- Source verification scanned **777,176 CSV rows**: all **100 selected records**
  match their original row, age, arrival time, complaint, seven vital values and
  estimated source label. CSV/model/cohort hashes match the retained values.
- Official actionlint and `git diff --check`: passed. PowerShell launcher syntax
  parsed successfully. No cloud release was triggered.
- Local package HTTP verification respects the existing content negotiation:
  request `Accept: text/html` for the browser interface; API clients retain JSON
  status at `/`. The launcher explicitly enables `SERVE_REACT_UI`.
- The 14-slide presentation was rendered and visually reviewed. Package, slide
  dimensions, native editable tables, reference fonts and first-party re-import
  checks passed. No native PowerPoint execution is claimed.

The delivery includes a separate package verification receipt for the extracted
ZIP and clean runtime environment. It records the checks actually completed on
that package. Live Azure OpenAI generation, SMS sending and clinical validation
are not asserted by these results.

## 6. Outstanding issues and limits

1. Obtain independently clinician-reviewed cases with chart, applicable
   discriminator and reference category labels. Until then, clinical accuracy,
   adherence and between-complaint differences cannot be reported honestly.
2. Obtain qualified review of the eight chart transcriptions, definitions and
   nursing workflow before any clinical use. Other MTS charts are outside the
   declared initial implementation scope.
3. Agree an unselected evaluation cohort, annotation/adjudication process and
   sample-size/uncertainty plan with the supervisor. Practice/demo annotations
   must not be presented as clinical evidence.
4. Optional Azure OpenAI explanation generation needs the student's separate
   credentials. No secrets or production configuration are packaged. Unsupported
   explanation claims still require actual review; a safety phrase filter alone
   cannot establish factual correctness.
5. The existing frozen experiment report is not a re-evaluation of the new MTS
   layer or current display policy. Estimated-label fit does not establish
   clinical MTS validity. The current model does not learn the newly recorded
   discriminators without a separately designed and validated training study.
6. The triage interface now adapts to the narrow local preview: patient queue,
   checklist and recommendation stack vertically; navigation and measurement
   rows adapt to the available width. The wide desktop layout is retained.
   Other screens have not received a complete mobile usability review.
7. GitHub/Azure publication is intentionally deferred until the student has
   reviewed the local package and explicitly requests publication.

## 7. Supervisor-ready explanation

The prototype now records a nurse-confirmed MTS flowchart and a structured
checklist of its clinical observations. Each saved recommendation retains the
chart, assessed/missing findings, executed rules, evidence and model results,
making the decision reconstructable in the interface and audit export.
Explicitly positive MTS findings may raise urgency without lowering the ML
recommendation. Evaluation software now supports the requested MTS process
metrics and chest-pain, abdominal-pain, trauma and respiratory comparisons.
Clinical results remain unevaluated because independent reference labels have
not yet been supplied. The Week 10 slides reflect this distinction and the next
annotation/evaluation steps. The current revision is supplied for local review
and has not been published to GitHub or Azure.

## Local preview follow-up

The earlier temporary test server had been stopped after packaging, leaving the
preview URL unavailable. A persistent hidden local server now serves the app at
`http://127.0.0.1:8769/`, with isolated local practice data and SMS disabled.
The fixed-width triage columns also hid patient details in the narrow preview;
responsive navigation, stacked panels and measurement rows correct that issue.

Actual browser checks confirmed all three triage panels fit inside the narrow
476 CSS-pixel viewport, and the wide layout retained three columns. A screenshot
is supplied in the review directory. The frontend suite was rerun: **91 tests
passed across 12 files**; the production Vite build passed with **2,081 modules**.
Health, readiness, catalog and evaluation HTTP endpoints returned 200. Backend
logic, model and cohort were unchanged during this follow-up; the full backend
suite above was not rerun solely for these layout changes. Nothing was pushed
to GitHub or deployed to Azure.
