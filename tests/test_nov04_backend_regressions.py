from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.fixture
def recoverable_review(monkeypatch, tmp_path):
    from app.api import case_resolver, case_routes
    from app.config import settings
    from app.notifications import service as notification_service
    from app.security.identity import AuthContext
    from app.storage.case_state_repository import append_case_state

    monkeypatch.setenv("PATIENT_DATA_MODE", "false")
    monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "false")
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "processed_dir", tmp_path)
    case = SimpleNamespace(case_uid="test~review-case", stay_id=7, source_dataset="fixture", case={})
    monkeypatch.setattr(case_routes, "_resolve_or_404", lambda _uid: case)
    monkeypatch.setattr(case_resolver, "resolve", lambda _uid: case)
    monkeypatch.setattr(notification_service, "sync_workflow_state", lambda *_a, **_k: None)
    append_case_state(tmp_path / "case_workflow_state.jsonl", {
        "case_uid": case.case_uid, "latest_workflow_run_id": "run-original",
        "latest_system_acuity": 2, "latest_system_prediction": "2", "state_revision": 1,
    })
    ctx = AuthContext(authenticated=True, user_id="triage-one", roles=["triage_nurse"], source="test")
    body = case_routes.ReviewBody(
        action_id="retry-review", workflow_run_id="run-original",
        review_status="REQUEST_MORE_INFORMATION", requested_fields=["respiratory rate"],
    )
    return case, ctx, body


def test_retry_repairs_projection_without_duplicate_evidence(recoverable_review, monkeypatch, tmp_path):
    from app.api import case_routes
    from app.storage.case_state_repository import append_case_state, latest_case_state
    from app.storage.human_review_repository import read_human_reviews

    case, ctx, body = recoverable_review
    original = case_routes._append_workflow_state
    attempts = 0

    def fail_first_projection(state):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("fixture write failure")
        return original(state)

    monkeypatch.setattr(case_routes, "_append_workflow_state", fail_first_projection)
    with pytest.raises(OSError):
        case_routes.submit_review(case.case_uid, body, ctx)
    state_path = tmp_path / "case_workflow_state.jsonl"
    review_path = tmp_path / "human_reviews.jsonl"
    assert latest_case_state(state_path, case.case_uid)["state_revision"] == 1
    assert len(read_human_reviews(review_path)) == 1

    response = case_routes.submit_review(case.case_uid, body, ctx)
    state = latest_case_state(state_path, case.case_uid)
    assert response["review_id"] == read_human_reviews(review_path)[0].review_id
    assert response["status"] == "already_recorded"
    assert state["state_revision"] == 2
    assert state["requested_fields"] == ["respiratory rate"]
    assert len(read_human_reviews(review_path)) == 1
    assert attempts == 2

    # A later review must not make a retry replace the current revision.
    append_case_state(state_path, {**state, "last_review_id": "review-later", "state_revision": 3})
    replay = case_routes.submit_review(case.case_uid, body, ctx)
    assert replay["workflow_state"]["state_revision"] == 3
    assert attempts == 2
    assert len(read_human_reviews(review_path)) == 1


def test_pending_review_cannot_replay_over_a_newer_assessment_or_as_another_actor(
    recoverable_review, monkeypatch, tmp_path,
):
    from dataclasses import replace
    from app.api import case_routes
    from app.storage.case_state_repository import append_case_state, latest_case_state
    from app.storage.human_review_repository import read_human_reviews

    case, ctx, body = recoverable_review
    with monkeypatch.context() as patch:
        def failed_projection(_state):
            raise OSError("fixture write failure")

        patch.setattr(case_routes, "_append_workflow_state", failed_projection)
        with pytest.raises(OSError):
            case_routes.submit_review(case.case_uid, body, ctx)
    with pytest.raises(HTTPException) as another_actor:
        case_routes.submit_review(case.case_uid, body, replace(ctx, user_id="triage-two"))
    assert another_actor.value.status_code == 409

    state_path = tmp_path / "case_workflow_state.jsonl"
    append_case_state(state_path, {
        "case_uid": case.case_uid, "latest_workflow_run_id": "run-newer", "state_revision": 2,
    })
    with pytest.raises(HTTPException, match="workflow has advanced") as stale:
        case_routes.submit_review(case.case_uid, body, ctx)
    assert stale.value.status_code == 409
    assert latest_case_state(state_path, case.case_uid)["latest_workflow_run_id"] == "run-newer"
    assert len(read_human_reviews(tmp_path / "human_reviews.jsonl")) == 1


def test_legacy_action_is_not_duplicated_after_a_newer_transition(recoverable_review, tmp_path):
    from app.api import case_routes
    from app.schemas.review import HumanReviewRecord
    from app.storage.human_review_repository import append_human_review, read_human_reviews

    case, ctx, body = recoverable_review
    review_path = tmp_path / "human_reviews.jsonl"
    append_human_review(review_path, HumanReviewRecord(
        case_uid=case.case_uid, action_id=body.action_id, review_id="older-review",
        workflow_run_id=body.workflow_run_id, review_status=body.review_status,
        review_comment="", created_at_utc="2026-11-01T10:00:00+00:00",
    ))
    with pytest.raises(HTTPException, match="legacy action_id is already recorded"):
        case_routes.submit_review(case.case_uid, body, ctx)
    assert len(read_human_reviews(review_path)) == 1


def test_durable_row_keys_distinguish_reviews_and_current_state_on_same_run():
    from app.security.audit_sink import _as_table_entity

    common = {"workflow_run_id": "run-same", "case_uid": "test~one"}
    first = _as_table_entity({**common, "record_kind": "human_review", "review_id": "review-a"})
    second = _as_table_entity({**common, "record_kind": "human_review", "review_id": "review-b"})
    current = _as_table_entity({**common, "record_kind": "case_workflow_state_current", "state_id": "test~one"})
    assert first["RowKey"] == "review-a"
    assert second["RowKey"] == "review-b"
    assert current["RowKey"] == "test~one"
    history_a = _as_table_entity({**common, "record_kind": "case_workflow_state", "updated_at_utc": "2026-11-01T10:00:00+00:00"})
    history_b = _as_table_entity({**common, "record_kind": "case_workflow_state", "updated_at_utc": "2026-11-01T10:01:00+00:00"})
    assert history_a["RowKey"] != history_b["RowKey"]


def test_mixed_role_clinical_action_is_attributed_to_clinical_role(recoverable_review, tmp_path):
    from app.api import case_routes
    from app.services.observation_update import observation_transition
    from app.storage.human_review_repository import read_human_reviews

    case, ctx, body = recoverable_review
    ctx.roles.insert(0, "security_admin")
    response = case_routes.submit_review(case.case_uid, body, ctx)
    assert response["workflow_state"]["requesting_role"] == "triage_nurse"
    record = read_human_reviews(tmp_path / "human_reviews.jsonl")[0]
    assert record.reviewer_role == "triage_nurse"
    assert record.reviewer_roles == ["security_admin", "triage_nurse"]
    state = observation_transition({}, ctx=ctx, now="2026-11-01T10:00:00+00:00", assessed_measurements=["heartrate"])
    assert state["observation_provenance"]["heartrate"]["recorded_by_role"] == "triage_nurse"


def test_current_cases_do_not_age_out_of_the_event_window(monkeypatch, tmp_path):
    from app.storage.case_state_repository import latest_case_state, read_case_states, read_latest_case_states

    monkeypatch.setenv("PATIENT_DATA_MODE", "false")
    path = tmp_path / "case_workflow_state.jsonl"
    old = {"case_uid": "test~old", "case_status": "discharged"}
    with path.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(old) + "\n")
        for revision in range(50001):
            stream.write(json.dumps({"case_uid": "test~new", "state_revision": revision}) + "\n")
    assert old not in read_case_states(path)
    assert latest_case_state(path, "test~old") == old
    assert read_latest_case_states(path, limit=2) == {
        "test~old": old, "test~new": {"case_uid": "test~new", "state_revision": 50000},
    }


def test_local_history_cap_counts_audit_events_and_refuses_damaged_state(monkeypatch, tmp_path):
    from app.storage.case_state_repository import latest_case_state, read_local_case_state_history
    from app.storage.jsonl_io import EvidenceIntegrityError

    monkeypatch.setenv("PATIENT_DATA_MODE", "false")
    path = tmp_path / "case_workflow_state.jsonl"
    records = [
        {"record_kind": kind, "case_uid": "test~case", "state_revision": revision}
        for revision in range(1, 4) for kind in ("case_workflow_state_current", "case_workflow_state")
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    assert [r["state_revision"] for r in read_local_case_state_history(path, limit=4)] == [1, 2, 3]
    with path.open("a", encoding="utf-8") as stream:
        stream.write("{broken\n")
    with pytest.raises(EvidenceIntegrityError):
        latest_case_state(path, "test~case")
    with pytest.raises(EvidenceIntegrityError):
        read_local_case_state_history(path, limit=4, strict=True)


def test_histogram_counts_unique_cases_within_each_bucket():
    from app.analytics.itd_general_query import _execute_one

    events = [
        {"case_uid": case, "timestamp_utc": f"2026-11-0{day}T12:00:00+00:00"}
        for day, case in [(1, "case-a"), (1, "case-a"), (1, ""), (2, "case-a"), (2, "case-b")]
    ]
    plan = {"operation": "time_histogram", "interval": "day", "limit": 30}
    raw = _execute_one(events, plan)
    distinct = _execute_one(events, {**plan, "distinct_field": "case_uid"})
    assert [entry["count"] for entry in raw["groups"]] == [3, 2]
    assert [entry["count"] for entry in distinct["groups"]] == [1, 2]
    assert distinct["distinct_field"] == "case_uid"


def test_assessment_pagination_searches_entire_source_and_anchors_against_appends(monkeypatch, tmp_path):
    from app.api.mts_routes import reviewable_assessments
    from app.config import settings
    from app.constants import DATASET_SOURCE
    from app.schemas.workflow_run import WorkflowRunRecord

    monkeypatch.setenv("PATIENT_DATA_MODE", "false")
    monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "false")
    monkeypatch.setattr(settings, "processed_dir", tmp_path)
    path = tmp_path / "workflow_runs.jsonl"

    def row(index, *, source=DATASET_SOURCE):
        return WorkflowRunRecord(
            workflow_run_id=f"run-{index}", timestamp_utc="2026-11-01T12:00:00+00:00",
            case_uid=f"test~case-{index}", source_dataset=source,
            mts_assessment={"presenting_complaint": "Chest pain" if index < 2 else "Other"},
        ).model_dump_json() + "\n"

    path.write_text("".join(row(i) for i in range(1005)) + row(9000, source="excluded"), encoding="utf-8")
    first = reviewable_assessments(limit=1000, cursor=None, q=None, ctx=None)
    assert len(first["assessments"]) == 1000 and first["matched_count"] == 1005
    assert first["assessments"][0]["workflow_run_id"] == "run-1004"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(row(1005))
    next_page = reviewable_assessments(limit=1000, cursor=first["next_cursor"], q=None, ctx=None)
    assert [item["workflow_run_id"] for item in next_page["assessments"]] == [f"run-{i}" for i in range(4, -1, -1)]
    assert next_page["next_cursor"] is None
    searched = reviewable_assessments(limit=1, cursor=None, q="chest", ctx=None)
    assert searched["matched_count"] == 2 and searched["next_cursor"] == "run-1"
    with pytest.raises(HTTPException, match="no longer available"):
        reviewable_assessments(limit=1, cursor="run-1005", q="chest", ctx=None)


def test_cloud_patient_evidence_requires_opt_in_and_display_fields_are_omitted(monkeypatch):
    from app.agents.llm_explanation_agent import _build_prompt
    from app.security.identity import cloud_llm_egress_allowed

    monkeypatch.setenv("PATIENT_DATA_MODE", "true")
    monkeypatch.delenv("ALLOW_CLOUD_LLM_IN_PATIENT_DATA", raising=False)
    monkeypatch.delenv("APPROVED_CLOUD_LLM_DATA_PROCESSING", raising=False)
    assert not cloud_llm_egress_allowed()
    monkeypatch.setenv("APPROVED_CLOUD_LLM_DATA_PROCESSING", "true")
    assert not cloud_llm_egress_allowed()
    monkeypatch.setenv("ALLOW_CLOUD_LLM_IN_PATIENT_DATA", "true")
    assert cloud_llm_egress_allowed()
    prompt = _build_prompt({
        "patient_display_name": "Synthetic Name Canary",
        "nested": {"display_identifier": "Synthetic Identifier Canary", "heart_rate": 72},
    })
    assert "Canary" not in prompt
    assert "'heart_rate': 72" in prompt


def test_optional_team_deadline_cancels_provider_and_closes_client(monkeypatch):
    from app.agents import autogen_multi_agent_team as agent

    events = []

    class HangingTeam:
        async def run(self, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                events.append("provider_cancelled")

    class Client:
        async def close(self):
            events.append("client_closed")

    monkeypatch.setattr(agent, "load_azure_config", lambda: {"present": True})
    monkeypatch.setattr(agent, "build_model_client", lambda _config: Client())
    monkeypatch.setattr(agent, "build_case_uid_intake_validation_safety_explanation_team", lambda *_args: HangingTeam())
    monkeypatch.setattr(agent, "EXPLANATION_DEADLINE_SECONDS", 0.01)
    result = asyncio.run(agent.run_case_uid_team_explanation("test~case", {}))
    assert result["status"] == "ERROR"
    assert result["safety_failures"] == ["TEAM_RUN_FAILED: TimeoutError"]
    assert result["agent_turns"] == []
    assert events == ["provider_cancelled", "client_closed"]


def test_optional_single_agent_deadline_cancels_async_provider(monkeypatch):
    import openai
    from app.agents import llm_explanation_agent as agent

    events = []

    class HangingClient:
        def __init__(self, *, timeout, max_retries, **_kwargs):
            assert timeout == 15
            assert max_retries == 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            events.append("client_closed")

        async def create(self, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                events.append("provider_cancelled")

    monkeypatch.setattr(openai, "AsyncAzureOpenAI", HangingClient)
    monkeypatch.setattr(agent, "EXPLANATION_DEADLINE_SECONDS", 0.01)
    monkeypatch.setattr(agent, "_load_azure_config", lambda: {
        "endpoint": "https://example.invalid", "api_key": "dummy", "api_version": "test",
        "deployment": "test", "model": "test",
    })
    result = agent.run_llm_explanation({"heart_rate": 72})
    assert result.explanation_status == "ERROR"
    assert result.safety_failures == ["LLM_CALL_FAILED: TimeoutError"]
    assert events == ["provider_cancelled", "client_closed"]
