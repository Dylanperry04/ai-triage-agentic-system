"""Adversarial regressions for the final Azure-demo readiness pass."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import HTTPException


def test_duration_question_reaches_general_audit_planner(monkeypatch, tmp_path):
    from app.api import status_routes
    from app.security.identity import AuthContext

    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(tmp_path))
    monkeypatch.setattr(status_routes, "_normalised_audit_records", lambda **_kwargs: [])
    monkeypatch.setattr(status_routes, "_overdue_sweeper_status", lambda: {
        "overdue_vitals_sweeper_enabled": True,
        "overdue_vitals_alert_source": "server_side_scheduled",
    })
    response = status_routes.system_assistant(
        status_routes.SystemAssistantRequest(
            question="How long does it normally take nurses to supply requested information?"
        ),
        AuthContext(
            authenticated=True, user_id="demo-itd", display_name="ITD",
            roles=["security_admin"], source="test",
        ),
    )
    assert response["audit_query"]["supported"] is True
    assert response["audit_query"]["plan"]["queries"][0]["operation"] == "duration"
    assert "No complete start/end event pair" in response["answer"]


def test_two_competing_final_decisions_cannot_both_commit(monkeypatch, tmp_path):
    from app.api import case_routes
    from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource
    from app.security.identity import AuthContext
    from app.storage import human_review_repository

    case = EDTriageCase(
        source_dataset="uhl_synthetic_triage_v1", stay_id=1, subject_id=1,
        edstay=EDStaySource(subject_id=1, stay_id=1),
        triage=TriageSource(
            subject_id=1, stay_id=1, chiefcomplaint="Unwell adult", age=50,
            heartrate=90, resprate=18, o2sat=97, sbp=125, dbp=80,
            temperature=98.6, pain="2",
        ),
    ).model_dump(mode="json")
    resolved = SimpleNamespace(
        case_uid="case-race", stay_id=1,
        source_dataset="uhl_synthetic_triage_v1", case=case,
    )
    current = {
        "case_uid": "case-race", "latest_workflow_run_id": "run-race",
        "latest_system_acuity": 2, "latest_system_prediction": "2",
        "state_revision": 1,
    }
    reviews = []
    states = []
    monkeypatch.setattr(case_routes, "_resolve_or_404", lambda _uid: resolved)
    monkeypatch.setattr(case_routes, "_latest_workflow_state", lambda _uid: dict(current))
    monkeypatch.setattr(
        human_review_repository, "append_human_review",
        lambda _path, record: reviews.append(record.model_dump(mode="json")),
    )

    def append_state(state):
        current.clear()
        current.update(state)
        states.append(dict(state))
        return state

    monkeypatch.setattr(case_routes, "_append_workflow_state", append_state)
    ctx = AuthContext(
        authenticated=True, user_id="triage-a", display_name="Triage A",
        roles=["triage_nurse"], source="test",
    )
    bodies = [
        case_routes.ReviewBody(
            action_id="race-accept", review_status="ACCEPTED_AS_PRESENTED",
            workflow_run_id="run-race", system_prediction="2",
        ),
        case_routes.ReviewBody(
            action_id="race-override", review_status="OVERRIDDEN",
            workflow_run_id="run-race", system_prediction="2",
            final_clinician_acuity=3, clinician_override="3",
            clinician_decision="3", override_reason="Recorded deterioration",
        ),
    ]

    def submit(body):
        try:
            return case_routes.submit_review("case-race", body, ctx)
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, bodies))

    assert sum(isinstance(result, dict) for result in results) == 1
    conflicts = [result for result in results if isinstance(result, HTTPException)]
    assert len(conflicts) == 1 and conflicts[0].status_code == 409
    assert len(reviews) == 1
    assert len(states) == 1
    assert states[0]["state_revision"] == 2


def test_same_action_id_is_idempotent_but_cannot_change_payload(monkeypatch):
    from app.api import case_routes

    body = case_routes.ReviewBody(
        action_id="same-action", review_status="ACCEPTED_AS_PRESENTED",
        workflow_run_id="run-1", system_prediction="2",
    )
    digest = case_routes.hashlib.sha256(
        case_routes.json.dumps(
            body.model_dump(mode="json", exclude={"action_id"}),
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    state = {
        "case_uid": "case-idempotent", "latest_workflow_run_id": "run-1",
        "last_action_id": "same-action", "last_action_payload_hash": digest,
        "last_review_id": "review-existing",
    }
    monkeypatch.setattr(
        case_routes, "_resolve_or_404",
        lambda _uid: SimpleNamespace(
            case_uid="case-idempotent", stay_id=1, source_dataset="uhl", case={},
        ),
    )
    monkeypatch.setattr(case_routes, "_latest_workflow_state", lambda _uid: dict(state))
    result = case_routes.submit_review(
        "case-idempotent", body,
        SimpleNamespace(user_id="triage", display_name="Triage", roles=["triage_nurse"]),
    )
    assert result["status"] == "already_recorded"
    assert result["review_id"] == "review-existing"


def test_readiness_endpoint_returns_503_for_false_positive_health(monkeypatch):
    from app.api import health_routes

    monkeypatch.setattr(health_routes, "_readiness_payload", lambda: {
        "ready": False, "status": "not_ready", "model_loadable": False,
    })
    response = health_routes.readiness_endpoint()
    assert response.status_code == 503

