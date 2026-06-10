# Clinical Safety & Governance Charter

## Project

AI Triage Agentic System

## Current Status

This system is a research and development prototype.

It is **not for clinical use**.

It must not be used to make real patient-care decisions, route real patients, prioritise real emergency department attendance, or replace triage nurse or clinician judgement.

## Supervisor Direction

The intended final project is a Microsoft Azure-based, multi-agent AI triage system that:

* Uses public emergency department datasets first.
* Later validates against University Hospital Limerick data only under approved governance.
* Recommends Manchester-style triage levels only when a clinician-approved deterministic ruleset exists.
* Explains its reasoning.
* Flags unsafe outputs.
* Keeps a governance and audit trail for responsible clinical AI use.

## Core Clinical Safety Principle

The system must support clinicians.

The system must not independently make final clinical triage decisions.

A human clinician must remain responsible for final review, approval, override, and patient-care decisions.

## Permitted Use of LLMs

LLMs may be used for:

* Intake support.
* Text summarisation.
* Clinical entity extraction, where outputs are validated.
* Explanation generation based only on verified evidence.
* Governance reporting.
* Human-review support.

LLMs must not be used as the final authority for:

* Manchester triage category assignment.
* Emergency department routing.
* Clinical diagnosis.
* Treatment recommendations.
* Patient prioritisation.
* Replacing clinician judgement.

## Manchester Triage Rule

Manchester-style triage recommendations may only be enabled when all of the following are true:

1. A clinician-approved Manchester-style ruleset has been supplied.
2. The ruleset is implemented deterministically.
3. The ruleset is version-controlled.
4. Each rule can be audited and traced to its source.
5. The ruleset has been tested against known cases.
6. Human review remains available.
7. The governance report confirms the system is still not acting autonomously.

Until then, the system must return:

`NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED`

## Current Design Position

The current system correctly blocks automated Manchester classification.

The current system may review public MIMIC-IV-ED Demo cases, identify missing data, show audit reports, produce governance evidence, and request human review.

The current system must not assign Red, Orange, Yellow, Green, or Blue Manchester levels.

## Data Safety Rules

The system must separate triage-time data from retrospective data.

Triage-time inputs may include verified fields such as:

* Presenting complaint.
* Initial vital signs.
* Pain score.
* Arrival information.
* Demographics available at triage time.

Retrospective or leakage-prone data must not be used as initial triage inputs, including:

* Final diagnoses.
* Disposition.
* Admission outcome.
* ICU outcome.
* Mortality outcome.
* Future medication administration.
* Future vital signs not available at triage time.

These fields may only be used for retrospective audit, validation, or evaluation where clearly labelled.

## Public Dataset Rule

The project must use public ED datasets first.

Current permitted dataset stage:

* MIMIC-IV-ED Demo.

Full MIMIC-IV-ED, MDS-ED, NHAMCS, or UHL data must not be added until their schemas, permissions, governance risks, and leakage policies are separately reviewed.

## UHL Data Rule

No real UHL patient data may be committed to GitHub.

No real UHL patient data may be stored in unsecured local files.

No real UHL patient data may be used before governance approval, access controls, data minimisation, de-identification policy, and validation protocol are defined.

## Multi-Agent Safety Design

The target system may include:

1. Intake Agent.
2. Clinical Extraction Agent.
3. Vital Signs Agent.
4. Deterministic Manchester Rules Agent.
5. Safety Review Agent.
6. Explainability Agent.
7. Human-in-the-Loop Review Agent.
8. Governance and Audit Agent.

The Deterministic Manchester Rules Agent must not be LLM-based.

The Safety Review Agent must be able to block unsafe or incomplete outputs.

The Human-in-the-Loop Agent must preserve clinician control.

## Governance Review Gate

Every major system version should produce a review record covering:

1. Intake: intended use, users, dataset, deployment context, and risk level.
2. Scope: risks, assumptions, limitations, and required evaluations.
3. Assess: tests, missing-data checks, leakage checks, and output quality checks.
4. Probe: red-team and reviewer probing for unsafe outputs.
5. Decide: approve, reject, or request changes, with rationale and evidence links.

The output should be an evidence package, not a simple pass/fail claim.

## Audit Requirements

Every agent run should record:

* Timestamp.
* Case identifier.
* Agent name.
* Input evidence used.
* Output produced.
* Safety flags.
* Missing data.
* Whether human review is required.
* Whether automated Manchester classification was blocked.
* Governance verdict.

## Safety Failure Conditions

The system must request human review if:

* Required triage fields are missing.
* Vital signs are missing or contradictory.
* The model invents clinical facts.
* The output includes unsupported clinical claims.
* Retrospective data is used incorrectly.
* A Manchester category is requested before approved rules exist.
* The governance verdict is not ready for clinical use.
* The output appears unsafe, overconfident, or insufficiently explained.

## Current Governance Verdict

The current expected governance verdict is:

`NOT_READY_FOR_CLINICAL_USE`

This is correct and should remain true until formal clinical validation and governance approval are completed.

## Development Commitments

All future development must follow these commitments:

* Verified facts must be separated from assumptions.
* Clinical rules must not be invented.
* Dataset schemas must be inspected before use.
* LLM outputs must be treated as fallible.
* Safety checks must be deterministic where possible.
* Human review must remain central.
* All outputs must be auditable.
* All clinical-use claims must be conservative.
* The system must prioritise patient safety over feature completion.

## Current Stage Approval

The current approved project stage is:

* Public demo dataset pipeline.
* Azure-hosted FastAPI backend.
* Missing-data review.
* Human review queue.
* Responsible AI governance report.
* AutoGen multi-agent prototype using verified Azure API evidence.
* No clinical triage automation.

## Explicit Non-Goal at Current Stage

The current system must not attempt to provide clinical triage recommendations for real patients.

The current system must not claim to be clinically validated.

The current system must not claim to be safe for deployment in an emergency department.
