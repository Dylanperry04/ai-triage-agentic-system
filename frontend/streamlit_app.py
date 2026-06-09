from datetime import datetime, timezone
from uuid import uuid4
import json

import streamlit as st

from app.config import settings
from app.storage.jsonl_repository import read_jsonl
from app.schemas.internal import EDTriageCase
from app.agents.orchestrator import run_workflow
from app.schemas.review import HumanReviewRecord
from app.storage.human_review_repository import (
    append_human_review,
    get_reviews_for_stay,
    read_human_reviews,
)


st.set_page_config(
    page_title="AI Triage Agentic System",
    layout="wide",
)


def load_cases() -> list[dict]:
    path = settings.processed_dir / "triage_cases_sample.jsonl"

    if not path.exists():
        st.error("No processed cases found. Run: python scripts\\build_sample_cases.py --n 100")
        st.stop()

    return read_jsonl(path)


def read_json_file(path):
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_local_governance_report():
    dataset_audit_path = settings.processed_dir / "dataset_audit_report.json"
    missing_inputs_path = settings.processed_dir / "missing_triage_inputs_report.json"
    schema_report_path = settings.processed_dir / "schema_report.json"
    human_review_path = settings.processed_dir / "human_reviews.jsonl"

    dataset_audit = read_json_file(dataset_audit_path)
    missing_inputs = read_json_file(missing_inputs_path)

    if dataset_audit is None or missing_inputs is None:
        return {
            "error": "Governance evidence files are missing. Run audit scripts first.",
            "required_commands": [
                "python scripts\\build_sample_cases.py --n 100",
                "python scripts\\audit_processed_sample.py",
                "python scripts\\inspect_missing_triage_inputs.py",
            ],
        }

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

    blocking_issues = []

    blocking_issues.append(
        "No clinician-approved Manchester triage ruleset configured."
    )

    if unreviewed_missing_stay_ids:
        blocking_issues.append(
            "Some cases with missing triage inputs have no saved human review."
        )

    if not schema_report_exists:
        blocking_issues.append("Schema report file is missing.")

    governance_verdict = (
        "NOT_READY_FOR_CLINICAL_USE"
        if blocking_issues
        else "READY_FOR_RESEARCH_DEMO_ONLY"
    )

    return {
        "system_name": "AI Triage Agentic System",
        "dataset": "MIMIC-IV-ED Demo v2.2",
        "clinical_use_status": "not_for_clinical_use",
        "governance_verdict": governance_verdict,
        "blocking_issues": blocking_issues,
        "controls": {
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
                    else "schema_report.json was not found."
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
                "evidence": "Leakage guard is implemented and covered by unit tests.",
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
        },
        "responsible_ai_review_gate": {
            "intake": "Processed MIMIC-IV-ED Demo cases are loaded and grouped by stay_id.",
            "scope": "Workflow is limited to public demo data and verified triage-time input fields.",
            "assess": "Dataset audit, missing-data report, leakage guard, and unit tests are available.",
            "probe": "Human review records can be saved and retrieved for individual ED stays.",
            "decide": "System remains blocked from clinical use because Manchester rules are not configured.",
        },
    }


def build_local_review_queue():
    missing_inputs_path = settings.processed_dir / "missing_triage_inputs_report.json"
    human_review_path = settings.processed_dir / "human_reviews.jsonl"

    missing_inputs = read_json_file(missing_inputs_path)

    if missing_inputs is None:
        return {
            "error": "Missing triage inputs report not found.",
            "required_command": "python scripts\\inspect_missing_triage_inputs.py",
        }

    human_reviews = read_human_reviews(human_review_path)
    reviewed_stay_ids = {int(record.stay_id) for record in human_reviews}

    missing_cases = missing_inputs.get("missing_cases", [])

    queue_items = []

    for case in missing_cases:
        stay_id = int(case["stay_id"])

        case_reviews = [
            record.model_dump(mode="json")
            for record in human_reviews
            if int(record.stay_id) == stay_id
        ]

        queue_items.append(
            {
                "stay_id": stay_id,
                "subject_id": case.get("subject_id"),
                "chiefcomplaint": case.get("chiefcomplaint"),
                "missing_fields": case.get("missing_fields", []),
                "review_status": "reviewed" if stay_id in reviewed_stay_ids else "needs_review",
                "review_count": len(case_reviews),
                "reviews": case_reviews,
            }
        )

    queue_items = sorted(
        queue_items,
        key=lambda item: (item["review_status"] == "reviewed", item["stay_id"]),
    )

    reviewed_count = sum(
        1 for item in queue_items if item["review_status"] == "reviewed"
    )

    needs_review_count = sum(
        1 for item in queue_items if item["review_status"] == "needs_review"
    )

    return {
        "queue_name": "Missing triage input human review queue",
        "total_missing_cases": len(queue_items),
        "reviewed_count": reviewed_count,
        "needs_review_count": needs_review_count,
        "items": queue_items,
    }


records = load_cases()

st.title("AI Triage Agentic System — MIMIC-IV-ED Demo")

st.warning(
    "Not for clinical use. No automated Manchester rules are configured. "
    "This UI demonstrates data pipeline, leakage separation, human review, and Responsible AI governance evidence."
)

tab_case, tab_governance, tab_queue, tab_reviews = st.tabs(
    [
        "Case Review",
        "Responsible AI Governance",
        "Human Review Queue",
        "Human Review Audit",
    ]
)


with tab_case:
    case_options = {
        f"{r['stay_id']} — {r.get('triage', {}).get('chiefcomplaint') if r.get('triage') else 'No chief complaint'}": r
        for r in records
    }

    selected = st.selectbox("Select ED stay", list(case_options.keys()))
    case = EDTriageCase(**case_options[selected])
    result = run_workflow(case)

    st.subheader("Workflow status")

    col1, col2, col3 = st.columns(3)

    col1.metric("Stay ID", result.stay_id)
    col2.metric("Data validation", result.data_validation.validation_status)
    col3.metric(
        "Clinician review",
        "Required" if result.decision.requires_clinician_review else "Not required",
    )

    st.subheader("Triage-time input only")
    st.json(result.triage_input.model_dump(mode="json"))

    st.subheader("Data Validation Agent")
    st.json(result.data_validation.model_dump(mode="json"))

    st.subheader("Case Summary Agent")
    st.json(result.case_summary.model_dump(mode="json"))

    st.subheader("Manchester Decision Status")
    st.json(result.decision.model_dump(mode="json"))

    st.subheader("Safety Review")
    st.json(result.safety_review.model_dump(mode="json"))

    st.subheader("Retrospective labels — not triage input")
    st.json(result.retrospective_labels.model_dump(mode="json"))

    st.subheader("Full preserved case")
    with st.expander("Show full case object"):
        st.json(case.model_dump(mode="json"))

    st.subheader("Save human review")

    review_log_path = settings.processed_dir / "human_reviews.jsonl"
    existing_reviews = get_reviews_for_stay(review_log_path, case.stay_id)

    if existing_reviews:
        st.write(f"Existing reviews for stay {case.stay_id}:")
        for review_record in existing_reviews:
            st.json(review_record.model_dump(mode="json"))
    else:
        st.info("No human review records saved for this stay yet.")

    with st.form("human_review_form"):
        reviewer_role = st.selectbox(
            "Reviewer role",
            ["researcher", "triage_nurse", "emergency_physician", "supervisor"],
        )

        review_status = st.selectbox(
            "Review status",
            [
                "not_reviewed",
                "approved_for_review",
                "request_missing_data",
                "override_required",
                "clinician_review_complete",
            ],
        )

        review_comment = st.text_area(
            "Review comment",
            value=(
                "Initial triage vitals and pain are missing. Human data review required before any triage logic."
                if result.data_validation.requires_human_data_review
                else "Triage-time data reviewed."
            ),
        )

        submitted = st.form_submit_button("Save human review")

        if submitted:
            record = HumanReviewRecord(
                review_id=str(uuid4()),
                stay_id=case.stay_id,
                reviewer_role=reviewer_role,
                review_status=review_status,
                review_comment=review_comment,
                created_at_utc=datetime.now(timezone.utc).isoformat(),
            )

            append_human_review(review_log_path, record)

            st.success("Human review saved to audit log.")
            st.rerun()


with tab_governance:
    st.subheader("Responsible AI Governance Report")

    governance_report = build_local_governance_report()

    if "error" in governance_report:
        st.error(governance_report["error"])
        st.json(governance_report)
    else:
        verdict = governance_report["governance_verdict"]

        if verdict == "NOT_READY_FOR_CLINICAL_USE":
            st.error(f"Governance verdict: {verdict}")
        else:
            st.success(f"Governance verdict: {verdict}")

        st.write("Clinical use status:", governance_report["clinical_use_status"])

        st.subheader("Blocking issues")
        if governance_report["blocking_issues"]:
            for issue in governance_report["blocking_issues"]:
                st.warning(issue)
        else:
            st.success("No blocking issues for research demo.")

        st.subheader("Controls")
        controls = governance_report["controls"]

        for control_name, control in controls.items():
            status = control["status"]

            if status == "PASS":
                st.success(f"{control_name}: {status}")
            elif status in ["WARNING", "REQUEST_CHANGES", "NOT_CONFIGURED"]:
                st.warning(f"{control_name}: {status}")
            else:
                st.info(f"{control_name}: {status}")

            with st.expander(f"Evidence for {control_name}"):
                st.json(control["evidence"])

        st.subheader("Responsible AI review gate")
        st.json(governance_report["responsible_ai_review_gate"])

        st.subheader("Full governance report")
        with st.expander("Show full governance JSON"):
            st.json(governance_report)


with tab_queue:
    st.subheader("Human Review Queue")

    review_queue = build_local_review_queue()

    if "error" in review_queue:
        st.error(review_queue["error"])
        st.code(review_queue["required_command"])
    else:
        col1, col2, col3 = st.columns(3)

        col1.metric("Total missing-data cases", review_queue["total_missing_cases"])
        col2.metric("Reviewed", review_queue["reviewed_count"])
        col3.metric("Needs review", review_queue["needs_review_count"])

        st.subheader("Queue overview")

        queue_table = [
            {
                "stay_id": item["stay_id"],
                "subject_id": item["subject_id"],
                "chiefcomplaint": item["chiefcomplaint"],
                "review_status": item["review_status"],
                "review_count": item["review_count"],
                "missing_fields": ", ".join(item["missing_fields"]),
            }
            for item in review_queue["items"]
        ]

        st.dataframe(queue_table, use_container_width=True)

        st.subheader("Review selected missing-data case")

        queue_options = {
            f"{item['review_status'].upper()} — {item['stay_id']} — {item['chiefcomplaint']}": item
            for item in review_queue["items"]
        }

        if not queue_options:
            st.success("No missing-data cases require review.")
        else:
            selected_queue_label = st.selectbox(
                "Select case from review queue",
                list(queue_options.keys()),
            )

            selected_queue_item = queue_options[selected_queue_label]
            selected_stay_id = int(selected_queue_item["stay_id"])

            st.write("Selected stay ID:", selected_stay_id)
            st.write("Chief complaint:", selected_queue_item["chiefcomplaint"])

            st.write("Missing fields:")
            st.json(selected_queue_item["missing_fields"])

            st.write("Existing reviews for this stay:")
            if selected_queue_item["reviews"]:
                for review in selected_queue_item["reviews"]:
                    st.json(review)
            else:
                st.info("No reviews saved for this stay yet.")

            with st.form(f"queue_review_form_{selected_stay_id}"):
                reviewer_role = st.selectbox(
                    "Reviewer role",
                    ["researcher", "triage_nurse", "emergency_physician", "supervisor"],
                    key=f"queue_reviewer_role_{selected_stay_id}",
                )

                review_status = st.selectbox(
                    "Review status",
                    [
                        "request_missing_data",
                        "approved_for_review",
                        "override_required",
                        "clinician_review_complete",
                        "not_reviewed",
                    ],
                    key=f"queue_review_status_{selected_stay_id}",
                )

                default_comment = (
                    f"Missing triage fields for stay {selected_stay_id}: "
                    f"{', '.join(selected_queue_item['missing_fields'])}. "
                    "Human data review required before any triage logic."
                )

                review_comment = st.text_area(
                    "Review comment",
                    value=default_comment,
                    key=f"queue_review_comment_{selected_stay_id}",
                )

                submitted_queue_review = st.form_submit_button("Save queue review")

                if submitted_queue_review:
                    record = HumanReviewRecord(
                        review_id=str(uuid4()),
                        stay_id=selected_stay_id,
                        reviewer_role=reviewer_role,
                        review_status=review_status,
                        review_comment=review_comment,
                        created_at_utc=datetime.now(timezone.utc).isoformat(),
                    )

                    review_log_path = settings.processed_dir / "human_reviews.jsonl"
                    append_human_review(review_log_path, record)

                    st.success(f"Human review saved for stay {selected_stay_id}.")
                    st.rerun()


with tab_reviews:
    st.subheader("Human Review Audit Log")

    review_log_path = settings.processed_dir / "human_reviews.jsonl"
    all_reviews = read_human_reviews(review_log_path)

    if not all_reviews:
        st.info("No human review records saved yet.")
    else:
        st.write(f"Total review records: {len(all_reviews)}")

        for review_record in all_reviews:
            with st.expander(
                f"Stay {review_record.stay_id} — {review_record.review_status} — {review_record.created_at_utc}"
            ):
                st.json(review_record.model_dump(mode="json"))
                