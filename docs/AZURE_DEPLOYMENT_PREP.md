# Azure Deployment Preparation — AI Triage Agentic System

## Status

This project is ready for Azure deployment preparation as a research/demo workflow.

It is not ready for clinical use.

## Current system capability

The local MVP includes:

- FastAPI backend
- Streamlit demo UI
- MIMIC-IV-ED Demo data pipeline
- Triage-time input separation
- Retrospective label separation
- Data Validation Agent
- Case Summary Agent
- Safety Review Agent
- Human Review Queue
- Responsible AI Governance Report
- Responsible AI Evidence Package
- Demo readiness checker
- Release package script

## Clinical safety boundary

The system does not assign Manchester triage categories.

The current Manchester engine returns:

```text
NO_AUTOMATED_MANCHESTER_CLASSIFICATION_CONFIGURED