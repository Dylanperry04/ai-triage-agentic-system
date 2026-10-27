# MTS specification and Irish applicability review

Reviewed 26 October 2026 against `mts_it_specification_german.pdf`, the supplied
29-page implementation guide dated 11 March 2018. Its main text is English;
some diagrams and terminology are German. This is an implementation guide,
not a complete discriminator dictionary or a certification of this application.

## Irish applicability

The presentation/flowchart and reductive-discriminator approach agrees with the
HSE Emergency Medicine Programme Model of Care 2025, page 53. That guidance
recommends MTS for patients aged 16 and over and ICTS for children under 16. It
also recommends a modified process for people aged 75 and over, including a
least-urgent category of 3 and further assessments. EMEWS is an additional
post-triage deterioration framework. The existing research model and generic
vital thresholds do not implement those full clinical pathways.

The German time targets are 0/10/30/90/120 minutes. An Irish HIQA inspection
report for University Hospital Galway (16 February 2023, hospital response)
records MTS targets of 0/10/60/120/240. The existing app uses the latter values.
The German targets therefore have not been substituted. This comparison is
evidence of different clinical conventions, not a finding that any target is
individually prescribed by Irish statute. Local UHL clinical policy still needs
to determine the approved current implementation and edition.

Irish device regulation is a separate question. HPRA explains that software can
qualify as a medical device and that qualification, classification and applicable
conformity assessment are the manufacturer's responsibility. Matching a German
IT guide or passing software tests does not establish clinical approval or Irish
regulatory compliance. This package retains its research-only status.

Sources checked:

- [HSE Emergency Medicine Programme Model of Care 2025](https://about.hse.ie/api/v2/download-file/healthcare_professional_publications/Emergency_Medicine_Programme_Model_of_Care_2025.pdf/), pages 41 and 53–54.
- [HIQA University Hospital Galway inspection, 16 February 2023](https://www.hiqa.ie/system/files?file=inspectionreports%2FUniversity+Hospital+Galway%2C16+February+2023.pdf), hospital response: MTS category targets. Search-index text was available; direct retrieval returned a service error during this review.
- [HSE Irish Children's Triage System](https://healthservice.hse.ie/filelibrary/onmsd/national-emergency-medicine-programme-irish-childrens-triage-system-icts.pdf). This publicly available edition is supporting context, not an assertion that it is the current local paediatric protocol.
- [HPRA qualification and classification of medical devices](https://www.hpra.ie/regulation/medical-devices/manufacturers-and-authorised-representatives/qualification-and-classification-of-medical-devices).
- [ALSG MTS content licensing](https://alsg.zendesk.com/hc/en-gb/articles/4405111382930--MTS-Licencing-Manchester-Triage-System-MTS-content).
- [Manchester Triage Group material access](https://www.triagenet.net/classroom/course/section.php?id=3402).

## Requirement coverage

The statuses below deliberately distinguish implemented research functionality
from official MTS functionality. No approved complete machine-readable MTS
catalogue was supplied or found openly available through the official sources.

| Supplied guide | Requirement | Current result |
|---|---|---|
| pp. 3–4 | Presentation and discriminator determine urgency; fixed urgency for a discriminator; ABCDE ordering | **Gap.** Current headline comes from the ML selection policy and an escalate-only vital safety floor. The separate keyword pathway engine is provisional. It must not be represented as official MTS. |
| pp. 3–4 | Time to first physician contact; regional targets | **Wording corrected.** The recommendation panel now says first physician contact. Irish-style existing targets retained for the reasons above. |
| pp. 4–7 | Licensed workbook; complete unchanged presentations, explanations and discriminator order; updates and exclusions | **External dependency.** Obtain the approved edition and machine-readable content from the rights holder. The 17 displayed safety checks preserve existing project rules; they are not substitutes for the licensed catalogue. |
| pp. 6–9 | Presentation selection; inline definitions; risk limit; successive urgency tiers unlocked by explicit exclusion | **Gap.** The new evidence panel is a review of evaluated project checks, not an interactive official MTS assessment. It does not record clinician exclusion of an entire MTS tier. |
| pp. 8–9 | Triage before registration; temporary incomplete drafts; highest unexcluded tier; appointment handling | **Gap.** The app operates on pre-existing synthetic cases and recorded assessments. No registration-independent MTS draft workflow has been added. |
| p. 9 | Finalisation freezes assessment; corrections suggested to the user | **Partial.** Audited model runs and evidence are retained and clinician accept/override/escalate actions remain required. Automatic research-model safety escalation differs from the guide's suggestion/confirmation workflow. |
| pp. 10–11 | Presentation-dependent final checks; oxygen saturation on room air versus oxygen therapy; documented exceptions | **Gap.** Oxygen therapy context and exception choices are not available in the current model input. Numerical SpO2 safety checks are displayed and missingness is explicit. |
| p. 12 | Newly abnormal pulse, arrhythmia and known baseline; clinician exceptions | **Gap.** Current numeric pulse safety rules have no rhythm/baseline discriminator record. |
| p. 13 | Temperature checks with age-specific infant logic and recorded exceptions | **Partial measurements only.** Celsius display and Fahrenheit model conversion are retained and tested. Infant MTS logic is not implemented; Irish paediatric routing also needs ICTS. |
| p. 14 | Infection focus, respiratory rate, pressure and new confusion for sepsis review | **Gap.** New confusion and infection-focus assessments are absent; generic numeric/keyword checks do not establish a sepsis discriminator. |
| p. 15; pp. 19–20 | Pain self/observer assessment, observer identity, assessment reason and exceptions | **Gap.** The current single pain value is not the required structured assessment. |
| pp. 16, 29 | Glucose units, ketones/acidosis, hypoglycaemia/hyperglycaemia logic | **Gap.** These observations are not supplied by the cohort or captured by the current interface. No glucose findings are inferred. |
| pp. 17–18 | Initial deadline, dynamic remaining wait, physician-contact acknowledgement; reassessment never extends initial wait | **Gap/partial.** Observation reruns and previous/new values are audited. Existing observation reminders and arrival ordering are not the required MTS physician-contact deadline and must not be described as such. |
| pp. 18–20 | Preserve original observations and versions; reason, presentation and selected discriminator; structured lists | **Partial.** Saved runs now retain the complete evaluated safety evidence and a minimised explanation snapshot. Official presentation/discriminator selection and independent self/observer pain records remain absent. |
| pp. 20–22 | Patient information sheet; full area occupancy, appointment/untriaged statuses, live warnings, optional floor plan/waiting-room display | **Gap.** Current role queues and static target labels are research workflow screens, not a full HIS occupancy implementation. Patient leaflet and floor plan are optional customer choices. |
| pp. 23–25 | Thirteen operational reports by time and urgency, including door-to-triage, door-to-physician, timeliness and disposition | **Partial analytics only.** Existing audit/model/ITD summaries do not supply every required timestamp or report. Missing measures must not be manufactured from model-run time. |
| pp. 26–28 | Clinical quality audit: controlled sampling, 2% and at least 10/user, exit-option inclusion, restricted review and anonymised results | **Gap.** The existing security/action audit and retraining labels are different functions. No assertion of German works-council agreement or Irish governance approval is made. |
| pp. 8, 29 | HIS communication, HL7 v2 ORU/OBX, document export, optional device interfaces | **Gap.** The current REST API and CSV/audit exports do not constitute an HL7 v2, HCM/xDT/XML or VITAL integration. |

## What this change provides

The recommendation now shows decisive safety checks first, other matched checks,
the observed value and criterion, missing observations, and an expandable list of
all evaluated safety checks with matched/not-matched/not-assessed states. It also
explains the model's individual-class 25% selection policy only when it raises
that patient's model estimate. The same rule definitions drive both the override
and its evidence. The follow-up removes the parallel heuristic pathway block
from the patient panel; its audit evidence remains available in saved runs.
Evidence is carried through the response and stored against the exact assessment.
Explanations requested by the UI use that stored assessment rather than rerunning
the current case. Historical runs without the necessary evidence request a new
logged assessment instead of inventing an explanation.

See `DISCRIMINATOR_PIPELINE.md` for the discriminator-based ML migration.

## Pulse clarification from the supplied guide

Page 12, “Neuer unnormaler Puls”, proposes Orange for pulse below 60 or above
100, or an arrhythmic pulse, with documented exceptions for a known rhythm
disorder, an athlete, a child, or a recorded explanation. It is a final-check
correction workflow, not a complete Red discriminator dictionary. It does not
support a new rule that 150 bpm alone always means Red. The current input
contract has no new-versus-baseline pulse or rhythm assessment, so those
clinical facts must not be inferred from one number. Existing project safety
floors remain research rules, not “approved MTS” rules.

The [HSE 2023 national ED triage audit](https://assets.hse.ie/media/documents/HSE_National_Clinical_Audit_of_ED_Triage_Report_Final_1.pdf)
reports MTS edition 3, version 3.7 (2014) in the audited EDs. The official
[MTS content licensing guidance](https://alsg.zendesk.com/hc/en-gb/articles/4405111382930--MTS-Licencing-Manchester-Triage-System-MTS-content)
distinguishes licensed framework and licensed clinical content. A complete
approved Irish discriminator catalogue was not supplied or found in the public
official sources searched. Obtaining that content and its required observations
remains necessary before replacing project thresholds with an official MTS
decision pipeline.
