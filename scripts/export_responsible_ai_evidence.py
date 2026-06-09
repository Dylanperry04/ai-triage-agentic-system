import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from app.config import settings
from app.storage.human_review_repository import read_human_reviews


def read_json_if_exists(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_evidence_package(processed_dir: Path) -> Dict[str, Any]:
    dataset_audit_path = processed_dir / "dataset_audit_report.json"
    missing_inputs_path = processed_dir / "missing_triage_inputs_report.json"
    schema_report_path = processed_dir / "schema_report.json"
    human_review_path = processed_dir / "human_reviews.jsonl"

    dataset_audit = read_json_if_exists(dataset_audit_path)
    missing_inputs = read_json_if_exists(missing_inputs_path)
    schema_report = read_json_if_exists(schema_report_path)

    human_reviews = read_human_reviews(human_review_path)

    reviewed_stay_ids = {int(record.stay_id) for record in human_reviews}

    missing_cases = []
    if missing_inputs:
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

    blocking_issues: List[str] = [
        "No clinician-approved Manchester triage ruleset configured."
    ]

    if unreviewed_missing_stay_ids:
        blocking_issues.append(
            "Some cases with missing triage inputs have no saved human review."
        )

    if dataset_audit is None:
        blocking_issues.append("Dataset audit report is missing.")

    if missing_inputs is None:
        blocking_issues.append("Missing triage inputs report is missing.")

    if schema_report is None:
        blocking_issues.append("Schema report file is missing.")

    governance_verdict = (
        "NOT_READY_FOR_CLINICAL_USE"
        if blocking_issues
        else "READY_FOR_RESEARCH_DEMO_ONLY"
    )

    return {
        "package_name": "Responsible AI Evidence Package",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "system_name": "AI Triage Agentic System",
        "dataset": "MIMIC-IV-ED Demo v2.2",
        "clinical_use_status": "not_for_clinical_use",
        "governance_verdict": governance_verdict,
        "blocking_issues": blocking_issues,
        "workflow_status": {
            "schema_first_pipeline": dataset_audit is not None,
            "missing_data_audit_available": missing_inputs is not None,
            "schema_report_available": schema_report is not None,
            "human_review_log_available": human_review_path.exists(),
            "manchester_rules_configured": False,
            "automated_clinical_triage_enabled": False,
        },
        "responsible_ai_controls": {
            "intake": {
                "status": "IMPLEMENTED",
                "evidence": "MIMIC-IV-ED Demo cases are loaded and grouped by stay_id.",
            },
            "scope": {
                "status": "IMPLEMENTED",
                "evidence": "Workflow is limited to verified public demo data and triage-time input fields.",
            },
            "assess": {
                "status": "IMPLEMENTED",
                "evidence": "Dataset audit, missing-data report, leakage guard, and unit tests are available.",
            },
            "probe": {
                "status": "PARTIALLY_IMPLEMENTED",
                "evidence": "Human review records can be saved and retrieved. Not all missing-data cases may be reviewed yet.",
            },
            "decide": {
                "status": "BLOCKED",
                "evidence": "System is blocked from clinical use because no clinician-approved Manchester ruleset is configured.",
            },
        },
        "dataset_audit": dataset_audit,
        "schema_report": schema_report,
        "missing_triage_inputs_report": missing_inputs,
        "human_review_summary": {
            "review_record_count": len(human_reviews),
            "reviewed_stay_ids": sorted(reviewed_stay_ids),
            "missing_stay_count": len(missing_stay_ids),
            "reviewed_missing_stay_count": len(reviewed_missing_stay_ids),
            "reviewed_missing_stay_ids": reviewed_missing_stay_ids,
            "unreviewed_missing_stay_ids": unreviewed_missing_stay_ids,
        },
        "human_reviews": [
            record.model_dump(mode="json")
            for record in human_reviews
        ],
        "safety_statement": (
            "This package is evidence for a research/demo workflow only. "
            "It does not certify clinical safety, does not assign Manchester triage categories, "
            "and must not be used for patient care."
        ),
    }


def export_evidence_package() -> Path:
    output_path = settings.processed_dir / "responsible_ai_evidence_package.json"

    package = build_evidence_package(settings.processed_dir)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(package, f, indent=2)

    return output_path


def main():
    output_path = export_evidence_package()
    print(f"Responsible AI evidence package exported to: {output_path}")


if __name__ == "__main__":
    main()