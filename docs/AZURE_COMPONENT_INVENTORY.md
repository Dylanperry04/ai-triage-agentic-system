# Azure Component Inventory

This document describes the current Azure single-service system (FastAPI serving the built React UI; the Streamlit frontend is retired) and future
infrastructure needs. It does not implement Microsoft Fabric and does not add
Fabric dependencies.

## Current Components

| Component | Current role |
|---|---|
| React frontend (served by FastAPI) | Research/demo user interface for triage review, reassessment, audit dashboard, escalations, model evidence, and system health. Built to `frontend-react/dist` and served by the backend at `/`. The retired Streamlit frontend remains in-repo for reference only. |
| FastAPI backend | RBAC-enforced case, assessment, review, audit, governance, model-performance, and cost-estimate routes |
| Azure App Service | Hosts the Python 3.11 web application |
| GitHub Actions | Builds and deploys the app package |
| Azure OpenAI | Optional LLM/AutoGen explanation surface; cannot assign acuity |
| Model artifact path | Environment-configured path to trained research model artifact |
| Audit storage | Local JSONL in demo/local research mode; durable sink required for real patient-data deployment |
| Governance/reporting modules | Policy checks, model reports, audit evidence, optional W&B telemetry |
| RBAC/role handling | Demo roles locally; trusted proxy/Entra-style group mapping for secured mode |
| Dashboards/model performance | Reads aggregate model comparison reports and selected-model artifacts |

## Current Runtime Facts

- App Service plan assumption: Basic B1
- Region assumption: Sweden Central
- Runtime: Python 3.11
- Deployment path: GitHub Actions
- Application stack: single FastAPI service that serves the built React UI (same origin); the retired Streamlit frontend is kept in-repo for reference only

## Future Components Needed

| Future component | Why it is needed |
|---|---|
| Microsoft Entra ID | Real hospital identity and group claims |
| Key Vault | Secret storage for API keys and model/storage credentials |
| Managed Identity | Avoid static secrets in application settings |
| Private ingress/private endpoint | Restrict access to approved networks |
| Durable audit store | Append-only, queryable audit evidence outside local JSONL |
| Image/multimodal storage | Safe storage for scan/image metadata and future multimodal inputs |
| Retraining compute | Controlled GPU/HPC environment for reproducible retraining |
| Model registry/artifact store | Hash-pinned artifacts and compatibility metadata |
| Monitoring/telemetry | Runtime health, latency, token usage, error rates, and model-report availability |

## Non-Goals For This Patch

- No Microsoft Fabric migration.
- No new Fabric dependency.
- No claim that the current system is clinically deployed or clinically
  validated.
