from app.schemas.internal import EDTriageCase
from app.schemas.workflow import WorkflowResult
from app.rules.manchester_engine import run_manchester_engine
from app.agents.safety_review_agent import run_safety_review
from app.agents.data_validation_agent import run_data_validation_agent
from app.agents.case_summary_agent import run_case_summary_agent


def run_workflow(case: EDTriageCase) -> WorkflowResult:
    triage_input = case.to_triage_time_input()
    retrospective_labels = case.to_retrospective_labels()

    data_validation = run_data_validation_agent(triage_input)
    case_summary = run_case_summary_agent(triage_input, data_validation)
    decision = run_manchester_engine(triage_input)
    safety = run_safety_review(triage_input)

    return WorkflowResult(
        stay_id=case.stay_id,
        triage_input=triage_input,
        data_validation=data_validation,
        case_summary=case_summary,
        retrospective_labels=retrospective_labels,
        decision=decision,
        safety_review=safety,
        audit={
            "source_dataset": case.source_dataset,
            "workflow_version": "0.3.0-case-summary-agent",
            "clinical_decision_policy": "No automated Manchester classification until clinician-approved ruleset supplied",
            "leakage_policy": "Outcome and retrospective fields excluded from triage_input",
            "validation_policy": "Missing or non-informative triage inputs require human data review",
            "summary_policy": "Case summary uses triage-time fields only and does not diagnose or classify",
        },
    )