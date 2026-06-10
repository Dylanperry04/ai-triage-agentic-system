# Responsible AI Toolkit Alignment

## Purpose

This document explains how the AI Triage Agentic System aligns with the Responsible AI review-gate pattern described in the W&B `rai-toolkit` repository and the project governance material.

This document is not a compliance claim.

This document does not state that the system is clinically safe.

This document does not state that the system is ready for clinical deployment.

The current system remains:

`NOT_READY_FOR_CLINICAL_USE`

## Current Project Stage

The current system is a research and development prototype using public demo data.

Current implemented capabilities:

* MIMIC-IV-ED Demo data pipeline.
* Verified schema checks.
* Triage-time input separation.
* Retrospective leakage guard.
* Missing-data audit.
* Human review queue.
* Responsible AI governance endpoint.
* Azure App Service deployment.
* AutoGen multi-agent evidence review prototype.
* Manchester classification blocked because no clinician-approved ruleset exists.

Current excluded capabilities:

* No real patient-care use.
* No UHL patient data.
* No autonomous emergency department routing.
* No automated Manchester triage category assignment.
* No clinical validation claim.
* No replacement of triage nurse or clinician judgement.

## Why the RAI Toolkit Is Relevant

The RAI Toolkit pattern is relevant because this project needs more than model scores.

A clinical triage system needs:

* Declared intended use.
* Risk scoping.
* Dataset and schema evidence.
* Evaluation evidence.
* Red-team evidence.
* Human review.
* Decision records.
* Traceability.
* Clear approval, rejection, or request-changes status.

This project should use the RAI Toolkit as a governance reference pattern.

The toolkit should not be treated as a clinical safety certification tool.

## Review Gate Mapping

### 1. Intake

The intake stage records what the system is, who owns it, what data it uses, and what it is allowed to do.

Current intake evidence:

* System name: AI Triage Agentic System.
* Dataset: MIMIC-IV-ED Demo.
* Deployment: Azure App Service.
* Current user: research/development only.
* Current clinical status: not for clinical use.
* Current triage status: no automated Manchester classification.

Future intake evidence should include:

* Named project owner.
* Named clinical safety owner.
* Intended use statement.
* Excluded use statement.
* Dataset list.
* Deployment context.
* User roles.
* Risk level.
* Governance approvals.

### 2. Scope

The scope stage defines the risks, limits, and required checks.

Current scoped risks:

* Missing triage input data.
* Retrospective data leakage.
* Unsafe clinical overclaiming.
* Hallucinated clinical details.
* Premature Manchester triage automation.
* Lack of clinician-approved ruleset.
* Lack of UHL validation.
* Human review gaps.

Current scope decision:

The system must remain blocked from clinical use.

Future scoped risks should include:

* Bias and subgroup performance.
* Dataset shift between public ED data and UHL data.
* Unsafe reassurance.
* Missed emergency escalation.
* Prompt injection.
* PII/PHI exposure.
* Incomplete audit trail.
* Incorrect explanation.
* Clinician overreliance.

### 3. Assess

The assess stage runs technical checks and produces evidence.

Current assessment evidence:

* Unit tests.
* Schema validation.
* Missing-data audit.
* Dataset audit report.
* Retrospective leakage guard.
* Azure deployment verification.
* Governance report endpoint.
* Human review queue status.
* AutoGen multi-agent dry run.

Current assessment verdict:

`NOT_READY_FOR_CLINICAL_USE`

Future assessment evidence should include:

* Larger public dataset validation.
* Manchester ruleset test cases.
* Clinician-reviewed case set.
* Explanation quality checks.
* Safety escalation tests.
* Fairness and subgroup checks.
* Reliability tests.
* Azure monitoring evidence.
* Evaluation dashboard output.

### 4. Probe

The probe stage tests the system against realistic and adversarial cases.

Current probe evidence:

* Human review records can be saved.
* Missing-data cases are visible.
* AutoGen agents can review verified Azure API outputs.
* The system blocks automated Manchester classification.

Future probe evidence should include:

* Clinician reviewer probing.
* Unsafe reassurance probes.
* Prompt injection probes.
* Missing vitals probes.
* Contradictory data probes.
* Emergency red-flag probes.
* Hallucination probes.
* Leakage probes.
* Explanation challenge cases.

The probe stage must always preserve human judgement.

### 5. Decide

The decide stage records whether the system is approved, rejected, or requires changes.

Current decision:

`REQUEST_CHANGES`

Reason:

* No clinician-approved Manchester ruleset.
* Not all missing-data cases have completed human review.
* No clinical validation.
* No UHL validation.
* No production governance approval.

Current release decision:

The system may continue as a research prototype.

The system must not be used clinically.

Future release decisions should include:

* Named reviewer.
* Decision timestamp.
* Evidence package link.
* Approval status.
* Remediation items.
* Residual risks.
* Version or commit hash.
* Dataset version.
* Model version.
* Ruleset version.

## Mapping to Current System Components

| RAI Review Gate Area  | Current Project Component             | Current Status             |
| --------------------- | ------------------------------------- | -------------------------- |
| Intake                | Dataset and system metadata           | Partially implemented      |
| Scope                 | Governance report and safety charter  | Partially implemented      |
| Assess                | Tests, audits, leakage guard          | Implemented for demo stage |
| Probe                 | Human review queue and AutoGen review | Partially implemented      |
| Decide                | Governance verdict                    | Implemented for demo stage |
| Evidence record       | Responsible AI evidence package       | Partially implemented      |
| Human review          | Human review queue                    | Partially implemented      |
| Red-team testing      | Not yet implemented                   | Missing                    |
| Policy-as-code        | Not yet implemented                   | Missing                    |
| Weave tracing         | Not yet implemented                   | Missing                    |
| Production monitoring | Not yet implemented                   | Missing                    |

## Current Responsible AI Controls

The current system should continue to enforce:

* No clinical use.
* No automated Manchester triage.
* No real patient data in GitHub.
* No use of retrospective data as triage-time input.
* No invented clinical thresholds.
* No LLM-only triage decisions.
* Human review required for incomplete or unsafe cases.
* Governance verdict exposed through API.
* Evidence package exported as JSON.

## How We May Use RAI Toolkit Later

The RAI Toolkit may be useful later for:

* Structured review-gate design.
* Healthcare preset comparison.
* Red-team probes.
* Policy-as-code checks.
* Evidence-backed reports.
* Reviewer-pinned findings.
* JSON/HTML assessment records.
* Mapping evidence to NIST AI RMF and EU AI Act-style controls.

Before integrating it into the main project, we must inspect:

* Dependencies.
* Data handling.
* Whether any PHI/PII could be sent to external services.
* Whether it requires W&B/Weave cloud logging.
* Whether local-only operation is possible.
* Whether it conflicts with existing project dependencies.
* Whether its healthcare examples are only examples or clinically validated assets.

## Critical Limitation

The RAI Toolkit can help organise evidence.

It cannot make the AI triage system clinically safe by itself.

It cannot replace:

* Clinician review.
* Formal risk management.
* Hospital governance.
* Legal review.
* Clinical validation.
* Data protection review.
* Approved Manchester triage rules.

## Project-Specific Safety Rule

For this project, the RAI review gate must never approve clinical use unless all of the following are true:

1. Clinician-approved Manchester ruleset exists.
2. Ruleset is deterministic and auditable.
3. Dataset leakage checks pass.
4. Missing-data handling is approved.
5. Safety Review Agent blocks unsafe outputs.
6. Human-in-the-loop workflow is active.
7. UHL validation protocol is approved.
8. Governance/audit trail is complete.
9. Clinical stakeholders approve the evidence.
10. The system is explicitly approved for the relevant deployment context.

Until then, the correct governance verdict remains:

`NOT_READY_FOR_CLINICAL_USE`

## Immediate Next Implementation Tasks

1. Keep the current system not-for-clinical-use.
2. Add policy-as-code checks for project-specific safety rules.
3. Add red-team test cases for unsafe triage outputs.
4. Add structured agent audit logs.
5. Add a governance evidence package per run.
6. Add Azure OpenAI / Foundry only for explanation and review support.
7. Do not implement Manchester classification until approved clinical rules are supplied.

## Summary

The RAI Toolkit should guide how this project structures evidence, review gates, policy checks, red-team testing, and decision records.

It should not be treated as a shortcut to clinical approval.

The safest alignment is:

* Use public ED data first.
* Keep triage classification deterministic.
* Keep LLMs away from final triage decisions.
* Keep clinicians in the loop.
* Keep every output auditable.
* Keep governance verdicts conservative.
