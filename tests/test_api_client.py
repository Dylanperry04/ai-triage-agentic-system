"""v13 Streamlit->FastAPI API client: in-process transport routes through the
real FastAPI enforcement; errors propagate as BackendError."""
import pytest

from frontend import api_client


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for v in ("PATIENT_DATA_MODE", "AUTH_REQUIRED", "TRUSTED_AUTH_PROXY",
              "FASTAPI_BASE_URL", "DEMO_ROLE", "LOCAL_CREDENTIALED_RESEARCH",
              "ALLOW_IN_PROCESS_BACKEND_FOR_LOCAL_CREDENTIALED_RESEARCH"):
        monkeypatch.delenv(v, raising=False)
    import json as _json
    from app.config import settings
    proc = tmp_path / "processed"
    proc.mkdir()
    # Seed a synthetic MIMIC-IV-ED-Full case so the (full-MIMIC-only) resolver
    # finds cases. No credentialed data is used.
    case = {
        "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000001,
        "subject_id": 10000001,
        "edstay": {"subject_id": 10000001, "stay_id": 30000001, "gender": "F",
                   "arrival_transport": "AMBULANCE", "disposition": "HOME"},
        "triage": {"subject_id": 10000001, "stay_id": 30000001, "heartrate": 88.0,
                   "chiefcomplaint": "CHEST PAIN", "acuity": 2},
        "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
    }
    (proc / "frontend_cases_override.jsonl").write_text(_json.dumps(case))
    monkeypatch.setattr("app.config.settings.processed_dir", proc)
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(proc))
    yield


def test_health_in_process():
    h = api_client.health()
    assert h["clinical_use"] == "not_for_clinical_use"


def test_list_and_assessment(monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "triage_nurse")
    cases = api_client.list_cases()["cases"]
    assert cases
    cuid = cases[0]["case_uid"]
    a = api_client.run_assessment(cuid)
    assert a["case_uid"] == cuid
    assert "stay_id" not in a


def test_multiagent_explanation_client(monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "security_admin")
    monkeypatch.setenv("ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE", "true")

    async def _fake_team(case_uid, evidence, user_question=None):
        return {
            "status": "PASS",
            "agent_turns": [{"agent": "ExplanationAgent", "text": "Summary."}],
            "final_explanation": "Summary. Clinician review required.",
            "safety_failures": [],
        }

    monkeypatch.setattr(
        "app.agents.autogen_multi_agent_team.run_case_uid_team_explanation",
        _fake_team,
    )
    cuid = api_client.list_cases()["cases"][0]["case_uid"]
    out = api_client.multiagent_explain_case(cuid, "Summarise")
    assert out["case_uid"] == cuid
    assert out["multiagent"] is True
    assert out["status"] == "PASS"
    assert "stay_id" not in out


def test_followup_single_agent_explanation_client(monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "security_admin")
    monkeypatch.setenv("ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE", "true")

    def _fake_llm(evidence, clinician_question=None):
        from app.schemas.workflow import ExplanationResult
        return ExplanationResult(
            explanation_status="PASS",
            explanation_text="One-agent follow-up answer.",
        )

    monkeypatch.setattr(
        "app.agents.llm_explanation_agent.run_llm_explanation",
        _fake_llm,
    )
    cuid = api_client.list_cases()["cases"][0]["case_uid"]
    out = api_client.followup_explain_case(
        cuid,
        {"heartrate": 180},
        "Which edited vital mattered?",
    )
    assert out["case_uid"] == cuid
    assert out["multiagent"] is False
    assert out["explanation_status"] == "PASS"
    assert out["explanation"] == "One-agent follow-up answer."
    assert "agent_turns" not in out
    assert "stay_id" not in out


def test_multiagent_backend_exception_becomes_backend_error(monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "security_admin")
    monkeypatch.setenv("ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE", "true")

    async def _broken_team(case_uid, evidence, user_question=None):
        raise RuntimeError("simulated backend failure")

    monkeypatch.setattr(
        "app.agents.autogen_multi_agent_team.run_case_uid_team_explanation",
        _broken_team,
    )
    cuid = api_client.list_cases()["cases"][0]["case_uid"]

    with pytest.raises(api_client.BackendError) as exc:
        api_client.multiagent_explain_case(cuid, "Summarise")

    assert exc.value.status_code == 500


def test_free_text_chatbot_client_disabled_by_default(monkeypatch):
    # v18.5: the free-text /explanations chatbot stays disabled on clinical
    # screens; the multi-agent case-acuity explanation is a separate, enabled
    # clinician feature (covered in the backend regression suite).
    monkeypatch.setenv("DEMO_ROLE", "security_admin")
    monkeypatch.delenv("ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE", raising=False)
    cuid = api_client.list_cases()["cases"][0]["case_uid"]

    with pytest.raises(api_client.BackendError) as exc:
        api_client.explain_case(cuid, "Summarise")

    assert exc.value.status_code == 403


def test_researcher_review_raises_403():
    cuid = api_client.list_cases()["cases"][0]["case_uid"]
    with pytest.raises(api_client.BackendError) as e:
        api_client.submit_review(cuid, {"review_status": "ACCEPTED_AS_PRESENTED",
                                        "review_comment": "x"})
    assert e.value.status_code == 403


def test_demo_role_env_lets_nurse_submit(monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "triage_nurse")
    cuid = api_client.list_cases()["cases"][0]["case_uid"]
    out = api_client.submit_review(cuid, {"review_status": "ACCEPTED_AS_PRESENTED",
                                          "review_comment": "x"})
    assert out["status"] == "recorded"
    assert out["case_uid"] == cuid


def test_overdue_vitals_client_wrappers(monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "triage_nurse")
    cuid = api_client.list_cases()["cases"][0]["case_uid"]
    from app.config import settings
    from app.storage.case_state_repository import append_case_state
    append_case_state(settings.processed_dir / "case_workflow_state.jsonl", {
        "case_uid": cuid,
        "source_dataset": "MIMIC-IV-ED-Full-v2.2",
        "updated_at_utc": "2000-01-01T00:00:00+00:00",
        "last_action": "INITIAL_CLOCK",
        "last_vitals_checked_at": "2000-01-01T00:00:00+00:00",
    })
    created = api_client.mark_overdue_vitals_alert(cuid)
    assert created["status"] == "created"
    assert created["workflow_state"]["overdue_vitals_alert_active"] is True
    swept = api_client.sweep_overdue_vitals()
    assert swept["status"] == "completed"
    acknowledged = api_client.acknowledge_overdue_vitals(cuid)
    assert acknowledged["status"] == "acknowledged"
    assert acknowledged["workflow_state"]["overdue_vitals_alert_active"] is False


def test_system_assistant_client_is_itd_only(monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "triage_nurse")
    with pytest.raises(api_client.BackendError) as denied:
        api_client.system_assistant("What is the model status?")
    assert denied.value.status_code == 403

    monkeypatch.setenv("DEMO_ROLE", "security_admin")
    out = api_client.system_assistant("What is the model status?")
    assert out["status"] == "answered"
    assert out["evidence_scope"] == "system_admin_only"


def test_reads_must_use_backend_flag(monkeypatch):
    assert api_client.reads_must_use_backend() is False
    monkeypatch.setenv("PATIENT_DATA_MODE", "true")
    assert api_client.reads_must_use_backend() is True


def test_local_credentialed_requires_real_backend_url(monkeypatch):
    monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
    monkeypatch.delenv("FASTAPI_BASE_URL", raising=False)
    monkeypatch.delenv(
        "ALLOW_IN_PROCESS_BACKEND_FOR_LOCAL_CREDENTIALED_RESEARCH",
        raising=False,
    )
    monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
    with pytest.raises(api_client.BackendError) as e:
        api_client.health()
    assert e.value.status_code == 503
    assert "FASTAPI_BASE_URL is required in LOCAL_CREDENTIALED_RESEARCH" in e.value.detail


def test_local_credentialed_in_process_requires_explicit_override(monkeypatch, tmp_path):
    monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
    monkeypatch.delenv("FASTAPI_BASE_URL", raising=False)
    monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
    monkeypatch.setenv(
        "ALLOW_IN_PROCESS_BACKEND_FOR_LOCAL_CREDENTIALED_RESEARCH",
        "true",
    )
    monkeypatch.setenv("BACKEND_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("PSEUDONYM_SECRET", "test-local-secret")
    monkeypatch.setenv("LOCAL_CREDENTIALED_OUTPUT_DIR", str(tmp_path / "local_out"))
    h = api_client.health()
    assert h["clinical_use"] == "not_for_clinical_use"
