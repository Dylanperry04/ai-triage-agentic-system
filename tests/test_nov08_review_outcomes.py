"""Fault tests for decision commitment, role attribution and saved readback."""
from dataclasses import replace
from itertools import permutations

import pytest
from fastapi import HTTPException

from tests.test_nov04_backend_regressions import recoverable_review
from tests.test_uhl_role_retraining_trends import _run
from app.analytics.audit_dashboard import normalise_audit_records, aggregate_audit_dashboard
from app.retraining.monthly_export import build_export_dataset
from app.storage.review_outcome import reconcile_review_outcomes
from app.storage.jsonl_io import read_jsonl_dicts


def evidence(tmp_path):
    return (read_jsonl_dicts(tmp_path / "human_reviews.jsonl", strict=True),
            read_jsonl_dicts(tmp_path / "case_workflow_state.jsonl", strict=True))


@pytest.mark.parametrize("advance", [False, True])
def test_unapplied_review_is_not_a_label_or_completed_decision(recoverable_review, monkeypatch, tmp_path, advance):
    from app.api import case_routes
    from app.constants import DATASET_SOURCE
    from app.storage.case_state_repository import append_case_state
    from app.analytics.audit_journey_export import audit_journey_csv_bytes

    case, ctx, _ = recoverable_review
    case.source_dataset = DATASET_SOURCE
    body = case_routes.ReviewBody(action_id="failure", workflow_run_id="run-original",
        review_status="OVERRIDDEN", final_clinician_acuity=1, override_reason="Recorded fault test")
    with monkeypatch.context() as fault:
        def fail(_):
            raise OSError("projection failure")
        fault.setattr(case_routes, "_append_workflow_state", fail)
        with pytest.raises(OSError):
            case_routes.submit_review(case.case_uid, body, ctx)
    if advance:
        append_case_state(tmp_path / "case_workflow_state.jsonl", {
            "case_uid": case.case_uid, "state_revision": 2, "latest_workflow_run_id": "newer"})
    reviews, states = evidence(tmp_path)
    outcome = "superseded" if advance else "pending"
    run = _run("run-original", "2026-11-01T12:00:00Z", case.case_uid)
    rows, counts = build_export_dataset("2026-11", [run], reviews, states)
    assert rows == []
    assert counts["exclusion_reasons"] == {"review_" + outcome: 1}
    records = normalise_audit_records(human_reviews=reviews, workflow_states=states)
    attempts = [r for r in records if r["record_kind"] == "human_review_attempt"]
    assert len(attempts) == 1 and attempts[0]["review_outcome"] == outcome
    assert attempts[0]["decided_acuity"] is None
    assert not any(r["record_kind"] == "human_review" for r in records)
    # The attempt remains in the complete evidence export, not silently removed.
    csv_content = audit_journey_csv_bytes(records)
    assert outcome in str(csv_content)
    if advance:
        with pytest.raises(HTTPException) as rejected:
            case_routes.submit_review(case.case_uid, body, ctx)
        assert rejected.value.status_code == 409
    else:
        case_routes.submit_review(case.case_uid, body, ctx)
        reviews, states = evidence(tmp_path)
        assert len(reviews) == 1
        rows, counts = build_export_dataset("2026-11", [run], reviews, states)
        assert len(rows) == 1 and rows[0]["final_clinician_acuity"] == 1
        assert counts["review_outcome_counts"] == {"applied": 1}


@pytest.mark.parametrize("roles", list(permutations(["security_admin", "triage_nurse", "ed_doctor"])))
def test_doctor_final_action_attribution_ignores_claim_order(recoverable_review, tmp_path, roles):
    from app.api import case_routes
    from app.constants import DATASET_SOURCE
    from app.storage.case_state_repository import append_case_state

    case, ctx, _ = recoverable_review
    case.source_dataset = DATASET_SOURCE
    ctx = replace(ctx, roles=list(roles))
    append_case_state(tmp_path / "case_workflow_state.jsonl", {
        "case_uid": case.case_uid, "state_revision": 1, "latest_workflow_run_id": "run-original",
        "latest_system_acuity": 2, "escalation_status": "confirmed", "case_status": "escalation_pending"})
    response = case_routes.submit_review(case.case_uid, case_routes.ReviewBody(
        workflow_run_id="run-original", review_status="ESCALATION_RESOLVED",
        final_clinician_acuity=1, review_comment="Doctor confirms priority"), ctx)
    reviews, states = evidence(tmp_path)
    assert reviews[0]["reviewer_role"] == response["workflow_state"]["reviewer_role"] == "ed_doctor"
    assert reviews[0]["reviewer_roles"] == list(roles)
    # Requests without an idempotency key still carry commitment provenance.
    assert reviews[0]["base_state_revision"] == 1
    rows, _ = build_export_dataset("2026-11", [_run("run-original", "2026-11-01T12:00:00Z", case.case_uid)], reviews, states)
    assert len(rows) == 1 and rows[0]["label_source"] == "ed_doctor_final_escalation_override"


def test_applied_proof_requires_exact_case_run_review_and_revision():
    review = {"case_uid": "c", "workflow_run_id": "r", "review_id": "v", "base_state_revision": 4}
    good = {"case_uid": "c", "workflow_run_id": "r", "latest_workflow_run_id": "r",
            "last_review_id": "v", "state_revision": 5}
    for changed in ({"case_uid": "other"}, {"workflow_run_id": "other"},
                    {"last_review_id": "other"}, {"state_revision": 6}, {"latest_workflow_run_id": "new"}):
        assert reconcile_review_outcomes([review], [{**good, **changed}])[0]["review_outcome"] != "applied"
    assert reconcile_review_outcomes([review], [good, {**good, "state_revision": 8}])[0]["review_outcome"] == "applied"
    legacy = {key: value for key, value in review.items() if key != "base_state_revision"}
    assert reconcile_review_outcomes([legacy], [good])[0]["review_outcome"] == "applied"
    assert reconcile_review_outcomes([legacy], [])[0]["review_outcome"] == "unverified"
    assert "review_outcome" not in review  # persisted evidence is immutable


def test_attribution_does_not_grant_unassigned_clinical_authority():
    from app.security.identity import AuthContext, clinical_attribution_role
    from app.security import authz
    ctx = AuthContext(authenticated=True, roles=["security_admin", "triage_nurse"])
    assert clinical_attribution_role(ctx, authz.PERM_RESOLVE_ESCALATION) is None
    assert authz.PERM_RESOLVE_ESCALATION not in authz.permissions_for(ctx)
    assert clinical_attribution_role(ctx, authz.PERM_ACCEPT_ACUITY) == "triage_nurse"


def test_failed_later_attempt_does_not_displace_an_applied_label():
    from tests.applied_review_fixtures import applied_review_states
    run = _run("run", "2026-11-01T12:00:00Z")
    accepted = {"case_uid": run["case_uid"], "workflow_run_id": "run", "review_id": "a",
                "review_status": "ACCEPTED_AS_PRESENTED", "reviewer_role": "triage_nurse",
                "created_at_utc": "2026-11-01T12:01:00Z"}
    states = applied_review_states([accepted])
    pending = {**accepted, "review_id": "b", "base_state_revision": 1,
               "review_status": "REQUEST_MORE_INFORMATION", "created_at_utc": "2026-11-01T12:02:00Z"}
    rows, counts = build_export_dataset("2026-11", [run], [accepted, pending], states)
    assert len(rows) == 1 and rows[0]["review_status"] == "ACCEPTED_AS_PRESENTED"
    assert counts["review_outcome_counts"] == {"applied": 1, "pending": 1}
    # Once that request actually takes effect, the assessment is unresolved.
    states += [{**states[0], "last_review_id": "b", "state_revision": 2}]
    rows, counts = build_export_dataset("2026-11", [run], [accepted, pending], states)
    assert rows == [] and counts["exclusion_reasons"] == {"unresolved_or_nonfinal": 1}


def test_review_proof_is_independent_of_the_display_window():
    review = {"case_uid": "c", "workflow_run_id": "r", "review_id": "v", "base_state_revision": 4,
              "review_status": "OVERRIDDEN", "system_prediction": 3, "final_clinician_acuity": 2}
    proof = {"case_uid": "c", "workflow_run_id": "r", "latest_workflow_run_id": "r",
             "last_review_id": "v", "state_revision": 5, "record_kind": "case_workflow_state_current"}
    records = normalise_audit_records(human_reviews=[review], workflow_states=[], review_application_states=[proof])
    assert len(records) == 1 and records[0]["record_kind"] == "human_review"
    assert records[0]["review_outcome"] == "applied"


@pytest.mark.parametrize("mismatch", [None, "input", "checklist", "policy", "missing_snapshot", "missing_run"])
def test_current_assessment_is_exact_read_only_and_rejects_stale_evidence(recoverable_review, monkeypatch, mismatch):
    from types import SimpleNamespace
    from app.api import case_routes
    from app.schemas import workflow_run
    from app.rules import decision_policy
    from app.storage import workflow_run_repository

    case, ctx, _ = recoverable_review
    current = SimpleNamespace(mts_checklist_revision="checklist", to_triage_time_input=lambda: object())
    monkeypatch.setattr(case_routes, "EDTriageCase", lambda **_: current)
    monkeypatch.setattr(workflow_run, "triage_input_fingerprint", lambda _: "input")
    monkeypatch.setattr(decision_policy, "system_identity", lambda: {"system_policy_sha256": "policy"})
    saved = {"case_uid": case.case_uid, "source_dataset": case.source_dataset, "final_acuity": 1}
    run = {"assessment_response": saved, "timestamp_utc": "2026-11-01T12:00:00Z", "triage_input_sha256": "input",
           "mts_assessment": {"checklist_revision": "checklist"}, "system_identity": {"system_policy_sha256": "policy"}}
    if mismatch == "input": run["triage_input_sha256"] = "old"
    if mismatch == "checklist": run["mts_assessment"]["checklist_revision"] = "old"
    if mismatch == "policy": run["system_identity"]["system_policy_sha256"] = "old"
    if mismatch == "missing_snapshot": run.pop("assessment_response")
    monkeypatch.setattr(workflow_run_repository, "read_workflow_run", lambda *_, **__: None if mismatch == "missing_run" else run)
    def forbidden(*_, **__):
        pytest.fail("A read must not run prediction or write workflow state")
    monkeypatch.setattr(case_routes, "run_workflow", forbidden)
    monkeypatch.setattr(case_routes, "_append_workflow_state", forbidden)
    result = case_routes.current_assessment(case.case_uid, ctx)
    if mismatch:
        assert result["assessment"] is None
    else:
        assert result["assessment"]["workflow_run_id"] == "run-original"
        assert result["assessment"]["final_acuity"] == 1
        assert "workflow_run_id" not in saved


def test_saved_assessment_survives_real_workflow_and_guarded_persistence(monkeypatch, tmp_path):
    """Exercise the actual DTO, model, fingerprint and redaction boundaries."""
    from app.api import case_resolver, case_routes
    from app.config import settings
    from app.notifications import service as notifications
    from app.security.identity import AuthContext
    from app.schemas.internal import EDTriageCase
    from app.schemas.workflow_run import triage_input_fingerprint

    monkeypatch.setenv("PATIENT_DATA_MODE", "false")
    monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "false")
    monkeypatch.setattr(settings, "processed_dir", tmp_path)
    monkeypatch.setattr(notifications, "sync_workflow_state", lambda *_a, **_k: None)
    case = case_resolver.list_cases("uhl", limit=1)[0]
    ctx = AuthContext(authenticated=True, user_id="local-test", roles=["triage_nurse"], source="test")
    assert case_routes.current_assessment(case.case_uid, ctx)["assessment"] is None
    logged = case_routes.run_assessment(case.case_uid, ctx=ctx)
    run_path = tmp_path / "workflow_runs.jsonl"
    before = run_path.read_bytes()
    stored = read_jsonl_dicts(run_path, strict=True)[0]
    assert stored["triage_input_sha256"] == triage_input_fingerprint(EDTriageCase(**case.case).to_triage_time_input())
    restored = case_routes.current_assessment(case.case_uid, ctx)["assessment"]
    assert restored["workflow_run_id"] == logged["workflow_run_id"]
    assert restored["final_acuity"] == logged["final_acuity"]
    assert restored["assessment_timestamp_utc"] == logged["assessment_timestamp_utc"]
    assert run_path.read_bytes() == before


def test_assessment_digest_redaction_keeps_only_valid_metadata():
    from app.security.redaction import redact_for_log
    digest = "1234567890" + "abcdef" * 9
    version = "12345678-f043-437f-96c8-7f02e322e40c"
    assert redact_for_log({"triage_input_sha256": digest})["triage_input_sha256"] == digest
    assert redact_for_log({"latest_model_version": version})["latest_model_version"] == version
    for key in ("triage_input_sha256", "latest_model_version"):
        assert "private@example.com" not in redact_for_log({key: "private@example.com"})[key]
        assert "1234567890" not in redact_for_log({key: "1234567890"})[key]


@pytest.mark.parametrize("filename", ["workflow_runs.jsonl", "human_reviews.jsonl", "workflow_reruns.jsonl"])
def test_complete_journey_refuses_damaged_decision_stream(monkeypatch, tmp_path, filename):
    from app.api import status_routes
    from app.storage.jsonl_io import EvidenceIntegrityError
    monkeypatch.setenv("PATIENT_DATA_MODE", "false")
    monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "false")
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(tmp_path))
    monkeypatch.setattr(status_routes.settings, "processed_dir", tmp_path)
    (tmp_path / filename).write_text('{"broken":', encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        status_routes._normalised_audit_records(require_complete=True)


def test_complete_journey_refuses_capped_current_application_proof(monkeypatch):
    from app.api import status_routes
    monkeypatch.setenv("PATIENT_DATA_MODE", "true")
    monkeypatch.setattr(status_routes, "_read_patient_durable_records", lambda record_kind, limit:
        [{"case_uid": "c", "state_revision": 1}] if record_kind == "case_workflow_state_current" else [])
    with pytest.raises(HTTPException) as error:
        status_routes._normalised_audit_records(limit_per_stream=1, require_complete=True)
    assert error.value.status_code == 503
    assert "current_application_states" in error.value.detail
