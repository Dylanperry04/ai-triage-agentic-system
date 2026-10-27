# AI triage audit and implementation report

> Latest UI/cohort changes and verification: [follow-up report](FOLLOWUP_2026-10-26.md).

26 October 2026. Base: `dfaaa75f9e39566542d338bf9b4658412c55396e`.

## Result and scope

The supplied ZIP and GitHub checkout matched after normalising line endings.
The updated source includes the discriminator evidence panel, fixes below,
regression tests, rebuilt React assets, and two specification/pipeline reviews.
Changes are local and packaged for review; no GitHub push, Azure deployment or
chargeable notification submission was performed.

The system remains a research prototype. The displayed checks are the existing
project's **17 provisional vital safety checks**, now evaluated from one shared
definition set. They are not the official MTS discriminator catalogue. The
current model has not been retrained on official discriminators. Full official
MTS implementation and Irish clinical/regulatory compliance cannot be certified
from the supplied guide, current data, or software tests.

The review inventoried every tracked file and new source file, parsed all Python
source, scanned the application/training/scripts with Bandit, traced the active
API/model/rules/audit/UI paths, ran the suites, and challenged the final changes.
This is not a proof that every possible defect has been discovered or a claim
that every generated report/binary line received manual review. The inventory
records the actual files and hashes. Compiled model internals and cloud services
are outside a source-only proof of correctness.

## Confirmed defects and changes

Severity below reflects impact in the affected path, not evidence of an actual
patient incident. Boundary failures were reproduced with regression cases;
there is no claim that malformed data occurred in the pinned supplied cohort.

| ID / severity | Defect and root cause | Implemented correction |
|---|---|---|
| F01 High | Unknown-complaint fallback returned before concern-vital handling; already-urgent pathways could also discard concern reason codes. | Apply concern handling consistently and retain the evidence. Without an approved ruleset, concern physiology produces an alert without inventing a category. |
| F02 High | Safety review omitted the existing systolic-pressure-above-220 check used by other deterministic paths. | Include the same high-pressure threshold in safety review. |
| F03 High | Follow-up category comparison recognised MIMIC sources but ignored the active UHL source; newly available abnormal observations were marked unremarkable. | Compare UHL final acuity and recognise newly recorded abnormal observations; explain model-only escalation. |
| F04 High | Nonfinite observations could bypass comparisons or break JSON output; unsupported temperature units were interpreted through a default conversion path. | Reject NaN/infinity and unsupported units at the shared input schemas; normalise supported aliases. |
| F05 Medium | Pain parsing caught ValueError/TypeError but not `int(float('inf'))` OverflowError. | Treat unusable pain as unavailable and retain review requirements. |
| F06 Medium | Category mapping truncated fractional numbers and treated booleans as integers; infinity could raise. | Reject malformed acuity without inventing a valid category. |
| F07 Medium | Model selection accepted malformed probability vectors/classes. | Require exactly five expected classes, correct array shape, finite probabilities in [0,1], a sum of one and a valid threshold. Prediction remains withheld on failure. |
| F08 High | The UI could label modal confidence as the probability of a different final category. | Use the probability of the displayed class; allow modal confidence only when its class is explicitly the same; preserve a true zero. |
| F09 Medium | BP was passed as a `systolic/diastolic` string to a numerical warning calculation. | Use numeric systolic pressure for warning colour while retaining the displayed pair. |
| F10 High | A late preview could overwrite a newer logged assessment. Detail responses from a previous selection could replace the current patient view. | Track per-request identity and discard stale completions; scope detail updates to the current case. |
| F11 High | A preview could inherit a historical run ID from case detail, allowing actions against evidence not displayed. | Bind actions only to the displayed assessment's own run ID; request a logged assessment before review actions. |
| F12 High | An old patient/run's explanation could appear after selection or reassessment changed. | Invalidate pending responses by case/run and verify the returned run ID. |
| F13 High | The explanation endpoint recalculated current observations rather than explaining the displayed saved assessment. | Persist minimised explanation evidence with each run; the UI supplies that run ID and the endpoint reads its snapshot. Wrong-case, missing, ambiguous or historical unsnapshotted evidence cannot silently trigger replacement inference. |
| F14 High | Model evidence claimed zero patient overlap without report support and blurred training-policy metrics with the different serving policy. | Remove the invented overlap value; expose evaluation/serving policies and a visible scope note. Bind reports to model/cohort hashes and a consistent training run. |
| F15 Medium | The confusion-matrix caption reversed under- and over-triage for rows=true, columns=prediction. | Correct the direction and remove the misleading safety description. |
| F16 Medium | Audit construction read a nonexistent LLM status attribute and the wrong mapping-version source. | Persist actual explanation status, final mapping version, complete safety evidence and explanation snapshot. Correct UHL deployment-policy provenance. |
| F17 Medium | Trusted-principal JSON/claim shapes outside the expected structure could raise exceptions. | Validate base64, object/list structure and string claims, returning unauthenticated on malformed input. |
| F18 High | An explicitly empty role mapping fell back to the default mapping. | Distinguish `None` from an empty mapping, so an empty map grants no roles. |
| F19 High | The loopback guard accepted arbitrary hostnames beginning `127.`. | Parse IP addresses and recognise actual loopback addresses or localhost. |
| F20 Medium | Zero/negative limiter settings could break the limiter; concurrent check-and-reserve operations could exceed the allowed window. | Validate positive settings and protect shared limiter state with a lock. This remains a per-process limiter. |
| F21 Medium | JSONL tail reads with limit zero used `[-0:]` and returned the entire file. | Return an empty result for zero/negative limits. |
| F22 High | Two independent main-push workflows deployed to the same app; the older one skipped tests and release/notification safeguards and could overwrite the verified deployment. | Retain it as an archived file outside `.github/workflows`, leaving the tested deployment path authoritative. |
| F23 Medium | The unbounded Arrow dependency installed wheels requiring NumPy 2 despite the NumPy 1.26 serving pin, crashing retained UI tests. | Pin compatible Arrow 24. Upgrade the retired Streamlit dependency to support the security-fixed Pillow dependency. |
| F24 Security | Dependency audit found known vulnerabilities in Pillow and frontend development tools. | Pin Pillow 13; update Vite to 8.2.4 and Vitest to 4.1.12 and refresh the pnpm lock, including source-map-js. Upgrade local audit-environment packaging tools. Both final dependency audits are clean. |
| F25 Medium | An unavailable model could display a separate heuristic category as its final result although the saved final acuity was null. | Keep the parallel pathway in `rules_category` and withhold the final headline category. |
| F26 Medium | Explanation evidence called unused pathway codes primary drivers of the model output. Returned service errors could leave a blank completed UI. | Mark parallel safety context separately, expose selection policy and actual displayed-class probability, prohibit uncomputed feature-attribution claims, and show retryable service errors. |
| F27 High | Another staff member's observation update could refresh case details while leaving the selected case's older recommendation cached. | Track observation revisions, discard older cached/pending estimates, and fetch fresh evidence before enabling decisions. |

## Requested discriminator display

The panel displays checks that changed the recommendation first, followed by
other matched checks. Each item includes its observed value, unit and criterion.
The expandable list contains all evaluated checks and explicitly distinguishes
matched, not matched and not assessed. Missing observations are never presented
as negative findings, and threshold-adjacent values are not rounded across a
boundary. The panel names its provisional provenance and displays rule version.

The screenshot's Acuity 3 / 31% result with Acuity 4 / 42% is explained by the
existing selection policy: a more urgent class with at least 25% individual
probability may be selected above the modal class. The panel distinguishes that
policy from a vital safety override. It does not invent a clinical discriminator
or a learned feature attribution for a model-only decision.

The same evidence is delivered through the safe API, saved with the workflow run,
and made available to the explanation layer. Existing runs without the new
snapshot remain readable; explaining one through the new UI requires a new
logged assessment. The original model feature contract and clinical thresholds
have not been silently replaced with German rules.

## Specification and Irish coverage

`MTS_REQUIREMENTS_REVIEW.md` maps all sections of the 29-page guide, including
the raster verification diagrams. `DISCRIMINATOR_PIPELINE.md` records the
discriminator-based ML direction and its required catalogue/data contract.

The German document shares the structured presentation/discriminator approach
used in Irish guidance, but its time targets differ. Existing targets are
retained. The HSE recommends MTS for adults, a separate ICTS pathway under 16,
and further older-person/EMEWS assessment. The app does not implement these full
approved clinical pathways. The German guide's tier-exclusion workflow, official
catalogue, oxygen/rhythm/confusion/glucose/structured pain context, physician
deadline clock, complete HIS reporting/quality audit and HL7 integration remain
identified gaps. Copying a numerical check into the model would not fill them.

Sources and exact page references are linked in the requirements review. HPRA's
qualification/classification guidance also makes clear that software verification
does not by itself establish medical-device conformity. No regulatory clearance
is claimed.

## Verification

- Full final Python suite: **1247 passed, 21 skipped, 5 warnings**, 380.12 seconds; JUnit XML retained.
- Final frontend suite: **71 passed in 10 files**, using the checked-in pnpm lock and pnpm 10 (the CI major version). Production Vite build passed.
- Focused final API/evidence/snapshot checks: **99 passed**. Earlier safety-boundary run: **204 passed**; security/storage/limiter review: **81 passed**.
- Real local browser/API walkthrough with Chrome headless: **passed**, no page errors; the recommendation displayed all **17** checks, expanded the full list, ran a logged assessment and returned an explanation response with its matching run ID. Azure status was **NOT_CONFIGURED**.
- Python dependency audit: **no known vulnerabilities found** in the checked environment. Locked frontend dependency audit: **0 advisories**. `pip check`: **no broken requirements**.
- Python compile/AST checks and `git diff --check`: **passed**. Bandit findings remain disclosed below.
- Notification Bicep template: **compiled successfully** using local Bicep 0.46.1; no Azure deployment was performed. Docker image execution could not run because the local Docker Linux daemon was unavailable.
- Source ZIP hygiene and patch applicability checks are recorded in the accompanying verification archive.

The 21 skips are the repository's existing retired full-MIMIC active-source
contracts. They were not added to hide failures. Five old concern-matrix
expectations were updated because they encoded the confirmed missing-alert bug;
the assertions now require the alert and its reason. One manifest assertion was
updated for the tested security-compatible Streamlit/Arrow pins. No substantive
test assertions were suppressed.

Bandit scanned the application, training, legacy presentation, functions and
scripts. It reports 0 high, 13 medium and 54 low findings. The medium findings
were reviewed: the SQL fragments use fixed schema/allowlisted fragments and
parameterised values; the temporary path is an unused placeholder in Azure Table
mode. These are scanner findings, not confirmed SQL-injection exploits. Low
findings include deterministic demo random generation, unset cost configuration
values, optional import/error fallbacks and assertions. They remain visible in
the report; no blanket `nosec` suppression was added. Optional archived training
paths still deserve operational logging review before reuse.

## Remaining risks and practical limits

- No official licensed discriminator catalogue or discriminator-level training
  cohort was supplied or found openly available from the official access routes.
  Full MTS integration and training on those observations remain dependent on
  those resources and local clinical decisions.
- The current synthetic CatBoost artefact and archived training reports have not
  been independently clinically validated. Existing reports do not measure the
  whole deployed modal/25% policy plus overrides. No retraining or model promotion
  was performed.
- Live Azure OpenAI output, Entra/proxy configuration, Key Vault, durable Azure
  storage, SMS delivery, Azure deployment and Linux container execution were not
  verified against real services in this local Windows audit. Service-dependent
  tests use controlled test doubles. Local browser testing exercised the
  not-configured explanation state, not a real clinical narrative.
- Five Python warnings remain: httpx/Starlette and AnyIO compatibility
  deprecations, a native NumPy ufunc ABI-size warning during API assessment tests,
  and archived SciPy optimiser deprecations. Actual model inference and the
  regression suite pass; cross-platform native compatibility still needs
  deployment-image verification. These warnings were not hidden.
- The supported demo's in-memory locks/limiter do not provide cross-process or
  multi-instance coordination. Durable-history explanation lookup is bounded;
  unavailable evidence fails closed. Production scale needs indexed durable
  run retrieval and deployment-level concurrency design.
- The primary source model and research-use assumptions are preserved. A complete
  clinical system also needs approved population-specific workflows, clinical
  risk management, local operational policy and applicable regulatory assessment.

## Applying and reproducing

The source ZIP contains the updated complete project and rebuilt UI. The patch
targets the base commit above; use `git apply --check` before applying to another
checkout. Keep runtime state and secrets outside a shared package.

On a supported Python 3.11 environment install `requirements.txt` and
`requirements-dev.txt`, then run `python -m pytest -q`. In `frontend-react`, use
`pnpm install --frozen-lockfile`, `pnpm test`, and `pnpm build`. The active runtime
starts with `uvicorn app.main:app` locally; cloud deployment is a separate step.
