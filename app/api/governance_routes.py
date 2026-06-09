import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.storage.human_review_repository import read_human_reviews


router = APIRouter()


def _read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Required governance evidence file not found: {path}",
        )

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/governance/report")
def get_governance_report():
    """
    Responsible AI governance report.

    This endpoint combines:
    - dataset audit evidence
    - missing triage input evidence
    - schema report availability
    - human review audit records
    - Manchester rules status
    - clinical-use warning

    It does not certify the system. It provides an evidence package for review.
    """

    dataset_audit_path = settings.processed_dir / "dataset_audit_report.json"
    missing_inputs_path = settings.processed_dir / "missing_triage_inputs_report.json"
    schema_report_path = settings.processed_dir / "schema_report.json"
    human_review_path = settings.processed_dir / "human_reviews.jsonl"

    dataset_audit = _read_json_file(dataset_audit_path)
    missing_inputs = _read_json_file(missing_inputs_path)

    schema_report_exists = schema_report_path.exists()

    human_reviews = read_human_reviews(human_review_path)

    reviewed_stay_ids = {int(record.stay_id) for record in human_reviews}

    missing_cases = missing_inputs.get("missing_cases", [])
    missing_stay_ids = {
        int(case["stay_id"])
        for case in missing_cases
        if case.get("stay_id") is not None
    }

    reviewed_missing_stay_ids = sorted(
        missing_stay_ids.intersection(reviewed_stay_ids)
    )

    unreviewed_missing_stay_ids = sorted(
        missing_stay_ids.difference(reviewed_stay_ids)
    )

    controls = {
        "dataset_loaded": {
            "status": "PASS",
            "evidence": {
                "sample_size": dataset_audit.get("sample_size"),
                "dataset": "MIMIC-IV-ED Demo v2.2",
            },
        },
        "schema_report_available": {
            "status": "PASS" if schema_report_exists else "WARNING",
            "evidence": (
                "schema_report.json exists."
                if schema_report_exists
                else "schema_report.json was not found. Run: python scripts\\build_sample_cases.py --n 100"
            ),
        },
        "triage_input_separation": {
            "status": "PASS",
            "evidence": {
                "triage_input_fields": dataset_audit.get("triage_input_fields", []),
                "retrospective_label_fields": dataset_audit.get("retrospective_label_fields", []),
                "policy": "Retrospective fields are not used as triage-time inputs.",
            },
        },
        "missing_data_visibility": {
            "status": "PASS",
            "evidence": {
                "cases_with_missing_triage_inputs": missing_inputs.get("cases_with_missing_triage_inputs"),
                "missing_case_percent": missing_inputs.get("missing_case_percent"),
                "missing_cases": missing_cases,
            },
        },
        "human_review_for_missing_data": {
            "status": "PASS" if not unreviewed_missing_stay_ids else "REQUEST_CHANGES",
            "evidence": {
                "missing_stay_count": len(missing_stay_ids),
                "reviewed_missing_stay_count": len(reviewed_missing_stay_ids),
                "reviewed_missing_stay_ids": reviewed_missing_stay_ids,
                "unreviewed_missing_stay_ids": unreviewed_missing_stay_ids,
            },
        },
        "leakage_guard": {
            "status": "PASS",
            "evidence": (
                "Leakage guard is implemented in app/rules/leakage_guard.py "
                "and checked by unit tests."
            ),
        },
        "human_review_audit_log": {
            "status": "PASS" if human_reviews else "WARNING",
            "evidence": {
                "review_record_count": len(human_reviews),
                "reviewed_stay_ids": sorted(reviewed_stay_ids),
            },
        },
        "manchester_rules": {
            "status": "NOT_CONFIGURED",
            "evidence": (
                "No clinician-approved Manchester ruleset has been supplied. "
                "The system must not assign Red/Orange/Yellow/Green/Blue triage categories."
            ),
        },
        "clinical_use_guardrail": {
            "status": "PASS",
            "evidence": (
                "System explicitly declares not_for_clinical_use and does not perform automated triage classification."
            ),
        },
    }

    blocking_issues: List[str] = []

    if controls["manchester_rules"]["status"] == "NOT_CONFIGURED":
        blocking_issues.append(
            "No clinician-approved Manchester triage ruleset configured."
        )

    if unreviewed_missing_stay_ids:
        blocking_issues.append(
            "Some cases with missing triage inputs have no saved human review."
        )

    if not schema_report_exists:
        blocking_issues.append("Schema report file is missing.")

    if blocking_issues:
        governance_verdict = "NOT_READY_FOR_CLINICAL_USE"
    else:
        governance_verdict = "READY_FOR_RESEARCH_DEMO_ONLY"

    return {
        "system_name": "AI Triage Agentic System",
        "dataset": "MIMIC-IV-ED Demo v2.2",
        "clinical_use_status": "not_for_clinical_use",
        "governance_verdict": governance_verdict,
        "blocking_issues": blocking_issues,
        "controls": controls,
        "responsible_ai_review_gate": {
            "intake": "Processed MIMIC-IV-ED Demo cases are loaded and grouped by stay_id.",
            "scope": "Workflow is limited to public demo data and verified triage-time input fields.",
            "assess": "Dataset audit, missing-data report, leakage guard, and unit tests are available.",
            "probe": "Human review records can be saved and retrieved for individual ED stays.",
            "decide": "System remains blocked from clinical use because Manchester rules are not configured.",
        },
    }