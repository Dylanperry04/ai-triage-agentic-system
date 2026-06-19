# ai_triage_ktas Architecture Description

## 1. Current Local Architecture

`ai_triage_ktas` is currently a local VS Code research/demo application. The current deployable application path is **Streamlit**, with the entry point at:

`frontend/app.py`

FastAPI exists at:

`app/main.py`

but FastAPI is retained for future API work and is **not** the current deployment path.

The main workflow is coordinated by:

`app/agents/orchestrator.py`

The orchestrator manages the local triage workflow by routing cases through separate dataset-specific pipelines, deterministic safety components, model outputs, explanation/chat functionality, and local audit/review storage.

The project has two separate dataset pipelines:

1. **MIMIC-IV-ED Demo pipeline**
   `app/data_pipeline/mimic_adapter.py`

2. **Kaggle KTAS pipeline**
   `app/data_pipeline/ktas_adapter.py`

These datasets must remain separate and must not be merged.

ML prediction is dataset-specific:

* MIMIC cases use a **MIMIC acuity research model**.
* KTAS cases use **KTAS research models**.
* The KTAS model must never be applied to MIMIC cases.
* The MIMIC model must never be applied to KTAS cases.

The system also includes deterministic safety and validation components:

* data validation agent
* case summary agent
* safety review agent
* provisional Manchester-style research rules
* acuity/MTS-style display mapping
* vital escalation override
* leakage guard
* LLM safety filter

The project includes AutoGen in two forms:

* a single clinician chat agent
* a multi-agent team containing:

  * IntakeAgent
  * ValidationAgent
  * SafetyReviewAgent
  * ExplanationAgent

AutoGen/Azure OpenAI is used only for explanation and chat over already-computed evidence. The LLM must not assign triage category, diagnose, recommend treatment, or override deterministic safety logic.

Local audit/review storage is JSONL-based.

Azure OpenAI configuration values are currently supplied through local `.env` variables.

## 2. Planned Azure Architecture

The planned Azure deployment should mirror the current local Streamlit path, not the FastAPI path.

Target deployment flow:

Research user / reviewer
→ Azure-hosted Streamlit Web App running `frontend/app.py`
→ `app/agents/orchestrator.py`
→ separate dataset adapters:

* MIMIC-IV-ED Demo via `app/data_pipeline/mimic_adapter.py`
* Kaggle KTAS via `app/data_pipeline/ktas_adapter.py`

→ dataset-specific ML prediction:

* MIMIC acuity research model for MIMIC cases only
* KTAS research models for KTAS cases only

→ deterministic safety and validation layer:

* data validation
* case summary
* safety review
* provisional Manchester-style research rules
* acuity/MTS-style display mapping
* vital escalation override
* leakage guard
* LLM safety filter

→ AutoGen explanation/chat layer using Azure OpenAI
→ human clinician review
→ local or deployed audit/review logging mechanism, currently JSONL-based unless changed later.

FastAPI should be shown as a future/API component only, not as the current deployment route.

The Azure OpenAI endpoint, deployment name, API values, and related settings should be supplied through Azure application configuration or secure environment settings after deployment. These should replace local `.env` usage in the deployed environment.

## 3. What ARI Will Validate After Deployment

Microsoft ARI should be run after the Azure resources exist. It cannot produce a meaningful deployed-resource inventory while the project is still only local.

After deployment, ARI can validate and document the Azure-side infrastructure, such as:

* whether an Azure Web App exists for the Streamlit application
* whether Azure OpenAI resources/deployments exist
* what resource groups, regions, and subscriptions are being used
* what app configuration settings are visible at the Azure resource level
* what managed identities, RBAC assignments, or access controls exist, if deployed
* what networking/security resources exist, if deployed
* what monitoring/logging resources exist, if deployed
* whether the deployed Azure environment matches the intended architecture

ARI validates Azure resources. It does not validate the internal Python workflow logic.

## 4. What Must Be Manually Added to the ARI Diagram

ARI cannot see internal Python components inside the application code. The following must be manually added to the architecture diagram:

* Streamlit entry point: `frontend/app.py`
* future FastAPI component: `app/main.py`, clearly labelled as future/API work only
* orchestrator: `app/agents/orchestrator.py`
* separate MIMIC-IV-ED Demo pipeline:

  * `app/data_pipeline/mimic_adapter.py`
* separate Kaggle KTAS pipeline:

  * `app/data_pipeline/ktas_adapter.py`
* dataset-separation rule:

  * MIMIC and KTAS must not be merged
* model-separation rule:

  * MIMIC model only for MIMIC cases
  * KTAS model only for KTAS cases
* deterministic safety/validation components:

  * data validation agent
  * case summary agent
  * safety review agent
  * provisional Manchester-style research rules
  * acuity/MTS-style display mapping
  * vital escalation override
  * leakage guard
  * LLM safety filter
* AutoGen components:

  * single clinician chat agent
  * IntakeAgent
  * ValidationAgent
  * SafetyReviewAgent
  * ExplanationAgent
* Azure OpenAI usage boundary:

  * explanation/chat only
  * no triage assignment
  * no diagnosis
  * no treatment recommendation
  * no override of deterministic safety logic
* human clinician review step
* JSONL-based audit/review storage
* clinical safety warning:

  * research/demo only
  * not for clinical use
  * clinician review required for every output

## 5. Security Controls to Show as Target Controls

Because the project has not yet been deployed to Azure, Azure security controls should be labelled as **target controls**, not current controls, unless they are actually deployed later.

Target controls to show:

* secure Azure application settings for Azure OpenAI endpoint, deployment, and API values
* no hardcoded secrets in code
* restricted access to Azure OpenAI values
* role-based access control for deployed Azure resources
* managed identity where appropriate after deployment
* logging and monitoring for deployed app activity
* audit/review record protection
* separation between application users, reviewers, and infrastructure administrators
* clear non-clinical-use banner in the UI
* preservation of human-review workflow
* deployed-resource review using ARI after Azure resources exist

Do not present these as already implemented Azure controls unless they have actually been deployed.

## 6. Clinical Safety Wording

This system is a research/demo AI triage workflow. It is not for clinical use.

The system may display dataset-specific research predictions and provisional Manchester-style/MTS-style display mappings, but these are not clinically approved triage decisions.

The LLM/AutoGen layer is restricted to explanation and chat over already-computed evidence. It must not assign a triage category, diagnose a patient, recommend treatment, recommend disposition, or override deterministic safety logic.

Every output requires human clinician review.

The deterministic safety and validation layer remains the controlling safety boundary. AutoGen and Azure OpenAI are supporting explanation tools only, not clinical decision-makers.
