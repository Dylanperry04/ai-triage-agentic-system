import json
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.storage.human_review_repository import read_human_reviews

router = APIRouter()


def _read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Required governance evidence file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/governance/report")
def get_governance_report():
    from app.rules.manchester_engine import get_approved_ruleset

    ruleset = get_approved_ruleset()
    provisional_active = bool(ruleset) and ruleset.get(
        "validation_status"
    ) != "CLINICALLY_APPROVED"

    dataset_audit_path = settings.processed_dir / "dataset_audit_report.json"
    missing_inputs_path = settings.processed_dir / "missing_triage_inputs_report.json"
    schema_report_path = settings.processed_dir / "schema_report.json"
    model_eval_path = settings.processed_dir / "model_evaluation_report.json"
    human_review_path = settings.processed_dir / "human_reviews.jsonl"

    dataset_audit = _read_json_file(dataset_audit_path)
    missing_inputs = _read_json_file(missing_inputs_path)
    model_eval = _read_json_file(model_eval_path) if model_eval_path.exists() else {"status": "MISSING"}
    human_reviews = read_human_reviews(human_review_path)
    reviewed_stay_ids = {int(record.stay_id) for record in human_reviews}

    missing_cases = missing_inputs.get("missing_cases", [])
    missing_stay_ids = {int(c["stay_id"]) for c in missing_cases if c.get("stay_id") is not None}
    unreviewed_missing = sorted(missing_stay_ids.difference(reviewed_stay_ids))

    controls = {
        "dataset_loaded": {
            "status": "PASS",
            "evidence": {"sample_size": dataset_audit.get("sample_size"), "dataset": "Kaggle-KTAS"},
        },
        "schema_report_available": {
            "status": "PASS" if schema_report_path.exists() else "WARNING",
            "evidence": str(schema_report_path),
        },
        "triage_input_separation": {
            "status": "PASS",
            "evidence": {
                "triage_input_fields": dataset_audit.get("triage_input_fields", []),
                "retrospective_label_fields": dataset_audit.get("retrospective_label_fields", []),
                "policy": "KTAS labels, mistriage, diagnosis, disposition, and LOS excluded from triage-time inputs.",
            },
        },
        "missing_data_visibility": {
            "status": "PASS",
            "evidence": {
                "cases_with_missing_triage_inputs": missing_inputs.get("cases_with_missing_triage_inputs"),
                "missing_case_percent": missing_inputs.get("missing_case_percent"),
            },
        },
        "human_review_for_missing_data": {
            "status": "PASS" if not unreviewed_missing else "REQUEST_CHANGES",
            "evidence": {"unreviewed_missing_stay_ids": unreviewed_missing},
        },
        "ktas_model_report": {"status": "PASS" if model_eval_path.exists() else "WARNING", "evidence": model_eval},
        "official_manchester_mapping": {
            "status": "NOT_IMPLEMENTED",
            "evidence": (
                "No official Manchester Triage System mapping is implemented. "
                "Neither KTAS nor MIMIC-IV-ED is Manchester-labelled."
            ),
        },
        "provisional_mts_research_ruleset": {
            "status": "ACTIVE" if provisional_active else "DISABLED",
            "evidence": (
                {
                    "ruleset_id": ruleset.get("ruleset_id") if ruleset else None,
                    "is_official_mts": False,
                    "is_clinically_approved": False,
                    "note": (
                        "Provisional, unvalidated research ruleset is active "
                        "(default-on). Produces provisional Manchester-style "
                        "categories that are NOT the official MTS and NOT "
                        "clinically approved; clinician review required on every "
                        "output. See RULESET_PROVENANCE.md."
                    ),
                }
                if provisional_active
                else "Provisional MTS research ruleset is disabled; engine is fully gated."
            ),
        },
        "clinical_use_guardrail": {
            "status": "PASS",
            "evidence": "System declares not_for_clinical_use and requires human review.",
        },
    }
    blocking_issues: List[str] = [
        "No clinician-APPROVED Manchester triage ruleset configured "
        "(a provisional, unvalidated research ruleset is active by default, "
        "but it is not approved for clinical use)."
        if provisional_active
        else "No Manchester triage ruleset configured."
    ]
    if unreviewed_missing:
        blocking_issues.append("Some cases with missing triage inputs have no saved human review.")
    if not schema_report_path.exists():
        blocking_issues.append("Schema report file is missing.")

    return {
        "system_name": "AI Triage Agentic Workflow",
        "default_dataset": "MIMIC-IV-ED-Demo-v2.2",
        "datasets_available": ["MIMIC-IV-ED-Demo-v2.2", "Kaggle-KTAS"],
        "ktas_governance_evidence_dataset": "Kaggle-KTAS",
        "clinical_use_status": "not_for_clinical_use",
        "provisional_mts_mode": "enabled" if provisional_active else "disabled",
        "official_mts_ruleset": False,
        "clinically_approved_ruleset": False,
        "governance_verdict": "READY_FOR_RESEARCH_DEMO_ONLY" if not unreviewed_missing else "NOT_READY_FOR_CLINICAL_USE",
        "blocking_issues": blocking_issues,
        "controls": controls,
        "responsible_ai_review_gate": {
            "intake": (
                "Default dataset MIMIC-IV-ED Demo (public); Kaggle KTAS available "
                "as a separate research dataset. The audit/missing-data evidence "
                "in this report is KTAS-specific."
            ),
            "scope": (
                "Workflow produces deterministic safety checks plus dataset-"
                "specific ML: for MIMIC-IV-ED Demo cases an acuity model predicts "
                "ESI acuity, mapped to a five-level colour/priority display, with "
                "a deterministic escalate-only vital override; for KTAS cases a "
                "KTAS model predicts a KTAS class only (no MTS category). The two "
                "models are never mixed. Full MIMIC is not integrated yet. All "
                "outputs are research-only and not clinically validated."
            ),
            "assess": "Dataset audit, missing-data report, leakage guard, model report, and unit tests are available.",
            "probe": "Human review records can be saved and retrieved.",
            "decide": (
                "System remains blocked from clinical use: any Manchester-style "
                "category shown is provisional and unvalidated, and clinician "
                "review is required on every output."
            ),
        },
    }
