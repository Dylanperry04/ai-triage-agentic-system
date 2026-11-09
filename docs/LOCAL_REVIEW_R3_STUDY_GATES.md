# Research use and study gates

This local application runs on supplied synthetic observations with estimated acuity labels. It supports workflow review, controlled software verification and research design. A clinical evaluation requires the hospital's approved MTS content, an authorised study environment and independent clinical references.

## 1. Freeze the experiment

- Ask the hospital triage lead to record the local Manchester edition and version, language, chart and discriminator ordering, approved waiting targets, population scope, mandatory versus conditional measurements, and exceptions for unavailable observations. Confirm licensing for digital implementation and distribution with the relevant rights holder. The supplied fourth-edition text and German specification describe different contexts. Use neither as evidence of the policy operating at UHL. [Wiley fourth edition](https://www.wiley.com/en-us/emergency-triage-4th-edition-p-9781394266654) and [HSE national clinical audit of ED triage](https://www.hse.ie/eng/about/who/national-services/patient-flow-academy/patient-flow-resources/hse-national-clinical-audit-of-emergency-department-triage-report-2023.pdf).
- Predefine eligible adult presentations and explain exclusions or alternative valid charts. Include the four requested groups and retain missing, ambiguous and out-of-scope counts. Specify whether the target is the first nurse category, an adjudicated reference or a later category. Record revisions separately.
- Freeze the model digest, source and cohort identities, feature availability, thresholds, safeguards, MTS catalog and review policy before running an evaluation. Preserve the raw ML estimate, completed MTS assessment and hybrid advisory as separate outputs.
- Confirm the hospital and university's protocol, ethics, data-controller authorisation, data protection, access, retention and consent or applicable declaration arrangements before processing patient information. Removing names and patient numbers alone does not establish that an extract is anonymous. [HSE consent policy for health and social care research](https://www2.healthservice.hse.ie/organisation/national-pppgs/hse-national-policy-for-consent-in-health-and-social-care-research/) and [Data Protection Commission guidance](https://www.dataprotection.ie/en/dpc-guidance/anonymisation-pseudonymisation).

## 2. Retrospective validation

- Request a minimum approved triage-time extract. The data owner controls episode linkage, pseudonymisation, any permitted notes and the disclosure rules. Keep outcomes and revised decisions out of features. Retain missingness, source, time and units for the observations. Reconcile dates and institutional shifts before training.
- Use 2023–2025 for training and development if those records are delivered under the approved protocol. Keep the requested 2026 set sealed until candidate selection and thresholds are frozen. Check patient overlap within the approved environment. The supplied file ends in 2024 and cannot furnish the requested 2026 validation set.
- Capture the original nurse chart, applicable discriminators, initial category and evidence, plus subsequent revisions separately if available. Define who adjudicates unclear or conflicting cases and how to record alternative valid charts. Keep label acquisition independent from model predictions.
- Predefine denominators for each metric and report eligible, evaluated, missing, abstained and excluded counts. Report per-group exact agreement, undertriage and overtriage with uncertainty. Include an ordinal agreement statistic when the sample supports it. Assess model, MTS and hybrid outputs separately. Evaluate exact saved explanations against their exact assessment evidence. Report the number reviewed, the number with at least one unsupported claim, and the number of unsupported claims.

## 3. Prospective silent evaluation

- Use a separately approved workflow. Nurses perform ordinary triage and record the reference independently. Research staff capture AI observations separately. Keep AI output out of care, queue priority, SMS and clinical workflow decisions. Only a preapproved post-collection linkage step joins the two sides.
- Use institution-managed identities with separate collection, adjudication and administration roles. Demonstration role headers and a reviewer checkbox cannot prove independent collection. Keep identifiable linkage with the authorised institution.
- A study environment needs an approved durable record store, verified append/transaction boundaries, backups and restore drills, access review, monitoring and incident handling. Demonstrate endpoint permissions, session termination, egress restrictions, deletion/retention policy, disconnected operation and clinical fallback before enabling any patient-data profile.
- Ask UHL which observations, units, input sources, timestamps, workload measures, escalation and reassessment records actually exist. Run task-based usability and accessibility sessions with clinical staff. A browser layout check measures visibility and interaction only.

## Engineering and evaluation boundaries

The active local application is FastAPI (`app/main.py`), the compiled React UI (`frontend-react/dist`), UHL case and ML serving, structured MTS assessment, and synthetic review persistence. Readiness and UHL prediction verify the selected artifact bytes against the pinned model digest before deserialising. The separate Streamlit application, historical MIMIC and older training modules are compatibility and research history. Keep their dependency sets separate and use reviewed artifacts and provenance for any legacy research.

The supported local launcher binds to loopback, uses demonstration identities and disables external SMS. The study gates above require positive evidence from the hospital and the approved hosting environment. A software fix, a digest, an audit test or a demo result cannot supply that evidence.
