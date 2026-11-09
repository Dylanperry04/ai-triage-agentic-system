"""An explanation of a displayed run must use that run's immutable evidence."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import case_routes
from app.config import settings
from app.security.identity import AuthContext
from app.storage.workflow_run_repository import read_workflow_run


def prepare(monkeypatch, tmp_path, rows):
    monkeypatch.setattr(settings, "processed_dir", tmp_path)
    (tmp_path / "workflow_runs.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
    monkeypatch.delenv("LOCAL_CREDENTIALED_RESEARCH", raising=False)
    monkeypatch.setattr(case_routes, "_require_multiagent_acuity_explanation_enabled", lambda _: None)
    monkeypatch.setattr(case_routes, "_resolve_or_404", lambda _: SimpleNamespace(
        case_uid="case-a", source_dataset="test-source", case={
            "stay_id": 1, "subject_id": 1,
            "edstay": {"stay_id": 1, "subject_id": 1},
        }))
    monkeypatch.setattr(case_routes, "_latest_workflow_state", lambda _: {})
    monkeypatch.setattr(case_routes, "run_workflow", lambda *a, **kw: pytest.fail("must not recompute the displayed run"))


@pytest.mark.parametrize("question", [None, "Which recorded acuity was displayed?"])
def test_recorded_evidence_is_used_even_when_current_case_would_differ(monkeypatch, tmp_path, question):
    evidence = {"case_uid": "case-a", "source_dataset": "test-source",
                "final_acuity_assessment": {"final_acuity": 3},
                "discriminator_evidence": {"status": "EVALUATED"}}
    prepare(monkeypatch, tmp_path, [{"case_uid": "case-a", "workflow_run_id": "old-run", "explanation_evidence": evidence}])
    monkeypatch.setattr("app.security.agent_gateway.authorise_agent_call", lambda *a, **kw: SimpleNamespace(allowed=True, minimised_evidence=kw["evidence"]))
    seen = []

    async def explain(uid, package, question):
        seen.append(package)
        return {"status": "PASS", "final_explanation": "The recorded acuity was 3. Clinician review is required."}

    monkeypatch.setattr("app.agents.autogen_multi_agent_team.run_case_uid_team_explanation", explain)
    result = asyncio.run(case_routes.multiagent_explain_case("case-a",
        case_routes.ExplanationRequest(workflow_run_id="old-run", question=question), AuthContext()))
    if question:
        assert seen == [evidence]
        assert result["explanation_source"] == "model_generated_review_required"
    else:
        assert seen == []
        assert result["explanation_source"] == "recorded_evidence"
        assert "Acuity 3" in result["final_explanation"]
        assert "has no model availability status" in result["final_explanation"]
        assert all(link["claim"] != "model_estimate" for link in result["evidence_links"])
    assert result["workflow_run_id"] == "old-run"


def test_recorded_explanation_refuses_a_conflicting_model_selection():
    from app.agents.recorded_explanation import explain_recorded_decision

    evidence = {
        "final_acuity_assessment": {"final_acuity": 2},
        "ml_research_estimate": {"available": True, "predicted_mimic_acuity": 2},
        "discriminator_evidence": {"model_policy": {"ml_acuity": 4}},
    }
    with pytest.raises(ValueError, match="selection policy disagrees"):
        explain_recorded_decision(evidence)


@pytest.mark.parametrize("rows", [[],
    [{"case_uid": "case-b", "workflow_run_id": "old-run", "explanation_evidence": {"case_uid": "case-b"}}],
    [{"case_uid": "case-a", "workflow_run_id": "old-run"}],
    [{"case_uid": "case-a", "workflow_run_id": "old-run", "explanation_evidence": {"case_uid": "case-b"}}],
])
def test_missing_historical_or_wrong_case_evidence_is_never_replaced(monkeypatch, tmp_path, rows):
    prepare(monkeypatch, tmp_path, rows)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(case_routes.multiagent_explain_case("case-a",
            case_routes.ExplanationRequest(workflow_run_id="old-run"), AuthContext()))
    assert exc.value.status_code == 409


def test_patient_mode_reads_durable_history_and_refuses_ambiguous_run(monkeypatch, tmp_path):
    monkeypatch.setenv("PATIENT_DATA_MODE", "true")
    row = {"case_uid": "case-a", "workflow_run_id": "run-a"}
    seen = []

    def read(path, *, record_kind, limit):
        seen.append(record_kind)
        return [row, row]

    monkeypatch.setattr("app.storage.case_state_repository._read_patient_records", read)
    assert read_workflow_run(tmp_path / "must-not-open.jsonl", "run-a", "case-a") is None
    assert seen == ["workflow_run"]
