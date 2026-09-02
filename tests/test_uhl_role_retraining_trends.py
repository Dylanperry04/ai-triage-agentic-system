"""Acceptance tests for the UHL workflow/monitoring changes."""
from __future__ import annotations

import csv
from datetime import datetime, timezone

import pytest

from app.analytics.audit_dashboard import (
    aggregate_audit_dashboard,
    normalise_audit_records,
)
from app.analytics.itd_evidence import build_itd_evidence
from app.constants import DATASET_SOURCE, MODEL_INPUT_COLUMNS
from app.retraining import monthly_export
from app.security import authz
from app.security.identity import AuthContext, map_groups_to_roles


def _ctx(role: str) -> AuthContext:
    return AuthContext(authenticated=True, user_id=f"test-{role}", roles=[role])


def _snapshot() -> dict:
    return {
        "age": 57,
        "month": 8,
        "hour": 14,
        "time_bin": "afternoon",
        "season": "summer",
        "presenting_complaint": "SHORTNESS OF BREATH",
        "temperature": 99.1,
        "heartrate": 104,
        "resprate": 24,
        "o2sat": 93,
        "sbp": 108,
        "dbp": 68,
        "pain": 4,
    }


def _run(run_id: str, timestamp: str, case_uid: str | None = None) -> dict:
    return {
        "workflow_run_id": run_id,
        "timestamp_utc": timestamp,
        "case_uid": case_uid or f"uhl~{run_id}",
        "source_dataset": DATASET_SOURCE,
        "input_schema": list(MODEL_INPUT_COLUMNS),
        "input_snapshot": _snapshot(),
        "final_acuity": 3,
        "model_version": "uhl-test-v1",
        "model_sha256": "a" * 64,
    }


def _review(
    run: dict,
    status: str,
    role: str,
    *,
    final: int | None = None,
    created_at: str = "2026-08-20T12:00:00+00:00",
) -> dict:
    return {
        "workflow_run_id": run["workflow_run_id"],
        "case_uid": run["case_uid"],
        "review_status": status,
        "reviewer_role": role,
        "final_clinician_acuity": final,
        "override_reason": "Persistent hypoxia" if status == "OVERRIDDEN" else "",
        "review_comment": "Reviewed against current observations.",
        "created_at_utc": created_at,
    }


def test_role_matrix_enforces_ed_workflow_and_removed_role():
    ed_nurse = authz.permissions_for(_ctx("ed_nurse"))
    triage = authz.permissions_for(_ctx("triage_nurse"))
    doctor = authz.permissions_for(_ctx("ed_doctor"))

    assert {
        authz.PERM_RECORD_VITALS,
        authz.PERM_UPDATE_VITALS,
        authz.PERM_PROVIDE_REQUESTED_INFORMATION,
    } <= ed_nurse
    assert not ed_nurse & {
        authz.PERM_RUN_TRIAGE_ASSESSMENT,
        authz.PERM_ACCEPT_ACUITY,
        authz.PERM_OVERRIDE_ACUITY,
        authz.PERM_RESOLVE_ESCALATION,
    }
    assert {
        authz.PERM_RUN_TRIAGE_ASSESSMENT,
        authz.PERM_REVIEW_AI_PREDICTION,
        authz.PERM_ACCEPT_ACUITY,
        authz.PERM_OVERRIDE_ACUITY,
        authz.PERM_REQUEST_INFORMATION,
        authz.PERM_ESCALATE_CASE,
    } <= triage
    assert authz.PERM_UPDATE_VITALS not in triage
    assert {
        authz.PERM_REVIEW_ESCALATION,
        authz.PERM_REQUEST_INFORMATION,
        authz.PERM_RESOLVE_ESCALATION,
        authz.PERM_CLOSE_CASE,
    } <= doctor
    assert authz.PERM_UPDATE_VITALS not in doctor
    assert authz.PERM_ACCEPT_ACUITY not in doctor
    assert map_groups_to_roles(["ed-nurses"]) == ["ed_nurse"]
    assert map_groups_to_roles(["clinical-supervisors", "clinical_supervisor"]) == []


def test_monthly_dataset_uses_exact_runs_labels_and_dublin_boundaries():
    # August 2026 in Dublin is [31 Jul 23:00 UTC, 31 Aug 23:00 UTC).
    before = _run("before", "2026-07-31T22:59:59+00:00")
    accepted = _run("accepted", "2026-07-31T23:00:00+00:00")
    overridden = _run("override", "2026-08-31T22:59:59+00:00")
    after = _run("after", "2026-08-31T23:00:00+00:00")
    unresolved = _run("unresolved", "2026-08-15T10:00:00+00:00")
    bad_schema = _run("legacy", "2026-08-16T10:00:00+00:00")
    bad_schema["input_schema"] = []
    runs = [before, accepted, overridden, after, unresolved, bad_schema, accepted.copy()]
    reviews = [
        _review(before, "ACCEPTED_AS_PRESENTED", "triage_nurse"),
        _review(accepted, "ACCEPTED_AS_PRESENTED", "triage_nurse"),
        _review(overridden, "OVERRIDDEN", "triage_nurse", final=1),
        _review(after, "ACCEPTED_AS_PRESENTED", "triage_nurse"),
        _review(unresolved, "REQUEST_MORE_INFORMATION", "triage_nurse"),
        _review(bad_schema, "ACCEPTED_AS_PRESENTED", "triage_nurse"),
    ]

    rows, metadata = monthly_export.build_export_dataset("2026-08", runs, reviews)

    assert [row["workflow_run_id"] for row in rows] == ["accepted", "override"]
    assert rows[0]["final_clinician_acuity"] == rows[0]["system_prediction"] == 3
    assert rows[0]["label_source"] == "accepted_model_recommendation"
    assert rows[1]["final_clinician_acuity"] == 1
    assert rows[1]["label_source"] == "clinician_override"
    assert list(rows[0].keys()) == monthly_export.EXPORT_COLUMNS
    assert metadata["eligible_cases"] == 2
    assert metadata["accepted_cases"] == 1
    assert metadata["overrides"] == 1
    assert metadata["duplicate_source_run_ids_removed"] == 1
    assert metadata["exclusion_reasons"] == {
        "unexpected_model_input_schema": 1,
        "unresolved_or_nonfinal": 1,
    }


def test_ed_doctor_final_escalation_is_eligible_only_after_resolution():
    run = _run("doctor-final", "2026-08-10T09:00:00+00:00")
    requested = _review(run, "ESCALATION_REQUIRED", "triage_nurse")
    rows, metadata = monthly_export.build_export_dataset("2026-08", [run], [requested])
    assert rows == []
    assert metadata["excluded_unresolved_cases"] == 1

    resolved = _review(
        run,
        "ESCALATION_RESOLVED",
        "ed_doctor",
        final=2,
        created_at="2026-08-20T12:05:00+00:00",
    )
    rows, metadata = monthly_export.build_export_dataset(
        "2026-08", [run], [requested, resolved]
    )
    assert rows[0]["final_clinician_acuity"] == 2
    assert rows[0]["label_source"] == "ed_doctor_final_escalation_override"
    assert metadata["doctor_final_escalations"] == 1
    assert metadata["overrides"] == 1


def test_monthly_generation_is_atomic_idempotent_and_retries_itd_notice(
    monkeypatch, tmp_path
):
    run = _run("accepted", "2026-08-10T09:00:00+00:00")
    review = _review(run, "ACCEPTED_AS_PRESENTED", "triage_nurse")
    calls = []

    def fail_notice(manifest):
        # Notification state and the manifest must be reconciled while the same
        # month lock is held, or a slower worker can overwrite a newer revision.
        assert (tmp_path / ".2026-08.lock").is_file()
        calls.append(manifest["reporting_month"])
        raise RuntimeError("notification service unavailable")

    monkeypatch.setattr(monthly_export, "_notify_ready", fail_notice)
    now = datetime(2026, 9, 1, 1, tzinfo=timezone.utc)
    first = monthly_export.generate_monthly_export(
        "2026-08",
        now=now,
        workflow_runs=[run],
        human_reviews=[review],
        directory=tmp_path,
    )
    assert first["notification_status"] == "retry_pending"
    csv_path = monthly_export.csv_path_for("2026-08", tmp_path)
    original = csv_path.read_bytes()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == 1
    assert csv_rows[0]["workflow_run_id"] == "accepted"

    monkeypatch.setattr(monthly_export, "_notify_ready", lambda manifest: "ntf-monthly")
    second = monthly_export.generate_monthly_export(
        "2026-08",
        now=now,
        workflow_runs=[run],
        human_reviews=[review],
        directory=tmp_path,
    )
    assert second["notification_status"] == "created"
    assert second["notification_id"] == "ntf-monthly"
    assert csv_path.read_bytes() == original
    manifest, verified_path = monthly_export.validate_export_artifact("2026-08", tmp_path)
    assert manifest["eligible_cases"] == 1
    assert verified_path == csv_path
    assert manifest["revision"] == 1

    with pytest.raises(monthly_export.MonthlyExportError, match="has not completed"):
        monthly_export.generate_monthly_export(
            "2026-09", now=now, workflow_runs=[], human_reviews=[], directory=tmp_path
        )


def test_monthly_durable_source_read_uses_exact_dublin_start_and_fails_at_cap(
    monkeypatch,
):
    import app.api.status_routes as status_routes

    monkeypatch.setenv("PATIENT_DATA_MODE", "true")
    monkeypatch.setenv("MONTHLY_EXPORT_READ_LIMIT", "1000")
    monkeypatch.setenv("AZURE_AUDIT_READ_MAX_LIMIT", "1000")
    calls = []

    def bounded_reader(*, record_kind, limit, since_utc=None):
        calls.append((record_kind, limit, since_utc))
        return []

    monkeypatch.setattr(status_routes, "_read_patient_durable_records", bounded_reader)
    assert monthly_export._load_source_records("2026-08") == ([], [])
    assert calls == [
        ("workflow_run", 1000, "2026-07-31T23:00:00+00:00"),
        ("human_review", 1000, "2026-07-31T23:00:00+00:00"),
    ]

    def capped_reader(*, record_kind, limit, since_utc=None):
        return [{} for _ in range(limit)] if record_kind == "workflow_run" else []

    monkeypatch.setattr(status_routes, "_read_patient_durable_records", capped_reader)
    with pytest.raises(monthly_export.MonthlyExportError, match="cap"):
        monthly_export._load_source_records("2026-08")


def test_download_integrity_validation_detects_tampering(monkeypatch, tmp_path):
    run = _run("accepted", "2026-08-10T09:00:00+00:00")
    review = _review(run, "ACCEPTED_AS_PRESENTED", "triage_nurse")
    monkeypatch.setattr(monthly_export, "_notify_ready", lambda manifest: "ntf-monthly")
    monthly_export.generate_monthly_export(
        "2026-08",
        now=datetime(2026, 9, 1, 1, tzinfo=timezone.utc),
        workflow_runs=[run],
        human_reviews=[review],
        directory=tmp_path,
    )
    path = monthly_export.csv_path_for("2026-08", tmp_path)
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(monthly_export.MonthlyExportError, match="checksum"):
        monthly_export.validate_export_artifact("2026-08", tmp_path)


def test_patient_trends_show_vitals_and_advisory_direction_without_claiming_outcome():
    records = normalise_audit_records(
        workflow_reruns=[
            {
                "rerun_id": "r1",
                "timestamp_utc": "2026-08-20T10:00:00+00:00",
                "case_uid": "uhl~one",
                "source_dataset": DATASET_SOURCE,
                "previous_final_acuity": 3,
                "new_final_acuity": 2,
                "movement": "ESCALATION",
                "changed_vitals": [{"field": "o2sat", "previous": 96, "new": 89}],
            },
            {
                "rerun_id": "r2",
                "timestamp_utc": "2026-08-20T11:00:00+00:00",
                "case_uid": "uhl~one",
                "source_dataset": DATASET_SOURCE,
                "previous_final_acuity": 2,
                "new_final_acuity": 3,
                "movement": "DE_ESCALATION",
                "changed_vitals": [{"field": "o2sat", "previous": 89, "new": 95}],
            },
            {
                "rerun_id": "r3",
                "timestamp_utc": "2026-08-20T12:00:00+00:00",
                "case_uid": "uhl~two",
                "source_dataset": DATASET_SOURCE,
                "previous_final_acuity": 4,
                "new_final_acuity": 2,
                "movement": "ESCALATION",
                "changed_vitals": [{"field": "heartrate", "previous": 90, "new": 140}],
            },
        ]
    )
    dashboard = aggregate_audit_dashboard(records)
    trends = {item["case_uid"]: item for item in dashboard["patient_trends"]}
    assert trends["uhl~one"]["latest_direction"] == "improving"
    assert [point["value"] for point in trends["uhl~one"]["vital_series"][0]["points"]] == [96.0, 89.0, 95.0]
    assert trends["uhl~two"]["latest_direction"] == "deteriorating"
    assert dashboard["patient_trend_summary"] == {
        "deteriorating": 1,
        "improving": 1,
        "unchanged": 0,
        "insufficient_data": 0,
        "patients_with_reassessments": 2,
    }


def test_itd_evidence_answers_escalation_count_and_top_staff():
    records = normalise_audit_records(
        human_reviews=[
            {
                "workflow_run_id": "a",
                "case_uid": "uhl~a",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "review_status": "ESCALATION_REQUIRED",
                "reviewer_role": "triage_nurse",
                "reviewer_user_id": "nurse-a",
                "reviewer_display_name": "Nurse A",
            },
            {
                "workflow_run_id": "b",
                "case_uid": "uhl~b",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "review_status": "ESCALATION_REQUIRED",
                "reviewer_role": "triage_nurse",
                "reviewer_user_id": "nurse-a",
                "reviewer_display_name": "Nurse A",
            },
            {
                "workflow_run_id": "c",
                "case_uid": "uhl~c",
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "review_status": "ESCALATION_REQUIRED",
                "reviewer_role": "triage_nurse",
                "reviewer_user_id": "nurse-b",
                "reviewer_display_name": "Nurse B",
            },
        ]
    )
    evidence = build_itd_evidence(records, calendar_today=True)
    from app.api.status_routes import _compose_itd_answer

    answer = _compose_itd_answer(
        "how many patients have been escalated today and which staff member has escalated the most?",
        {},
        evidence,
        None,
        1,
    )
    assert answer == "3 escalated patients were recorded today. Nurse A recorded the most, with 2."
    assert evidence["escalations_by_person"][0] == {"label": "Nurse A", "count": 2}


def test_itd_general_audit_queries_and_unique_patient_semantics(monkeypatch):
    from app.analytics.itd_query import answer_audit_question
    monkeypatch.setenv("ITD_ASSISTANT_LLM_PLANNER_ENABLED", "false")
    evidence = {
        "model_assessments_run": 20,
        "accepts_submitted": 8,
        "information_requests_submitted": 3,
        "patients_discharged": 4,
        "observation_updates": 5,
        "observation_updates_by_person": [
            {"label": "Nurse Aoife", "count": 3},
            {"label": "Nurse Liam", "count": 2},
        ],
        "access_denied": 3,
        "denied_by_role": [
            {"label": "researcher", "count": 2},
            {"label": "ed_nurse", "count": 1},
        ],
    }
    assert answer_audit_question("How many AI assessments were run today?", evidence, "today").startswith("20 model assessments")
    assert answer_audit_question("What percentage of assessments were accepted today?", evidence, "today").startswith("40.0% (8 of 20)")
    assert answer_audit_question("How many requests for additional information were made today?", evidence, "today").startswith("3 requests for additional information")
    assert answer_audit_question("How many patients were discharged today?", evidence, "today").startswith("4 discharged patients")
    observation_answer = answer_audit_question("How many observations did each ED nurse record today?", evidence, "today")
    assert "Nurse Aoife (3)" in observation_answer
    assert "overdue" not in observation_answer
    denial_answer = answer_audit_question("How many access denials were there by role?", evidence, "today")
    assert "By role: researcher (2), ed_nurse (1)" in denial_answer


def test_itd_current_state_uses_all_retained_rows_not_activity_window():
    records = normalise_audit_records(workflow_states=[{
        "case_uid": "uhl~old-open",
        "updated_at_utc": "2020-01-01T00:00:00+00:00",
        "case_status": "escalation_requested",
        "escalation_status": "requested",
    }])
    evidence = build_itd_evidence(records, window_days=1)
    assert evidence["records_in_window"] == 0
    assert evidence["open_escalations"] == 1


def test_itd_combined_escalation_and_overdue_question_answers_both_parts():
    from app.api.status_routes import _compose_itd_answer

    answer = _compose_itd_answer(
        "how many escalations are open and are any vitals overdue?",
        {},
        {
            "escalations_submitted": 4,
            "open_escalations": 2,
            "escalations_resolved": 1,
            "overdue_vitals_alerts_active": 3,
        },
        None,
        7,
    )

    assert "2 escalations are currently open" in answer
    assert "3 overdue-observation alerts are currently active" in answer


def test_monthly_export_revises_when_a_late_final_label_arrives(monkeypatch, tmp_path):
    run = _run("late-final", "2026-08-31T22:50:00+00:00")
    requested = _review(run, "ESCALATION_REQUIRED", "triage_nurse")
    monkeypatch.setattr(monthly_export, "_notify_ready", lambda manifest: f"notice-r{manifest['revision']}")

    first = monthly_export.generate_monthly_export(
        "2026-08", now=datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc),
        workflow_runs=[run], human_reviews=[requested], directory=tmp_path,
    )
    assert first["eligible_cases"] == 0
    assert first["revision"] == 1

    resolved = _review(
        run, "ESCALATION_RESOLVED", "ed_doctor", final=2,
        created_at="2026-09-01T00:10:00+00:00",
    )
    revised = monthly_export.generate_monthly_export(
        "2026-08", now=datetime(2026, 9, 1, 0, 11, tzinfo=timezone.utc),
        workflow_runs=[run], human_reviews=[requested, resolved], directory=tmp_path,
    )
    assert revised["eligible_cases"] == 1
    assert revised["revision"] == 2
    assert revised["supersedes_sha256"] == first["sha256"]
    with monthly_export.csv_path_for("2026-08", tmp_path).open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        assert [row["workflow_run_id"] for row in csv.DictReader(handle)] == ["late-final"]


def test_retraining_csv_neutralises_spreadsheet_formulas():
    run = _run("formula", "2026-08-10T09:00:00+00:00")
    run["input_snapshot"]["presenting_complaint"] = "=HYPERLINK(\"https://example.test\")"
    review = _review(run, "OVERRIDDEN", "triage_nurse", final=2)
    review["override_reason"] = "+1+1"
    review["review_comment"] = "@SUM(1,1)"
    rows, _ = monthly_export.build_export_dataset("2026-08", [run], [review])
    content = monthly_export._csv_bytes(rows).decode("utf-8-sig")
    parsed = next(csv.DictReader(content.splitlines()))
    assert parsed["presenting_complaint"].startswith("'=")
    assert parsed["override_reason"].startswith("'+")
    assert parsed["review_comment"].startswith("'@")


def test_current_month_demo_preview_is_not_persisted_or_notified(monkeypatch):
    run = _run("preview", "2026-08-10T09:00:00+00:00")
    review = _review(run, "ACCEPTED_AS_PRESENTED", "triage_nurse")
    monkeypatch.setattr(monthly_export, "_notify_ready", lambda _manifest: pytest.fail("preview notified"))
    manifest, content = monthly_export.build_current_month_demo_preview(
        now=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
        workflow_runs=[run], human_reviews=[review],
    )
    assert manifest["demo_preview"] is True
    assert manifest["official_monthly_artifact"] is False
    assert manifest["persisted"] is False
    assert manifest["eligible_cases"] == 1
    assert b"workflow_run_id" in content


def test_explanation_is_server_limited_and_only_final_agent_crosses_api():
    from app.agents.autogen_multi_agent_team import condense_explanation
    from app.api.safe_dto import safe_multiagent_explanation_response

    long_text = (
        "The main reason this acuity level was suggested was the low oxygen saturation. "
        "The respiratory rate also increased. The heart rate increased. "
        "The pain score changed. The complaint was reviewed. "
        "Clinician review is required before any clinical action."
    )
    result = safe_multiagent_explanation_response(
        "uhl~safe-case",
        DATASET_SOURCE,
        {
            "status": "PASS",
            "agent_turns": [
                {"agent": "IntakeAgent", "text": "internal"},
                {"agent": "ExplanationAgent", "text": long_text},
            ],
            "final_explanation": long_text,
            "safety_failures": [],
        },
    )
    assert "agent_turns" not in result
    assert result["explanation_agent_only"] is True
    assert result["final_explanation"] == condense_explanation(long_text)
    assert len(result["final_explanation"]) <= 650
    assert len(result["final_explanation"].split(". ")) <= 4
