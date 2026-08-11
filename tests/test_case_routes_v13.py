"""v13 canonical case_uid-keyed API + resolver + security status.
Synthetic/public-demo data only. Verifies pseudonymous routing, RBAC, no raw-id
leakage, and the security-status builder."""
import base64
import json

import pytest
from starlette.testclient import TestClient

from app.main import app

client = TestClient(app)


def _principal(groups):
    claims = [
        {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "u1"},
        {"typ": "name", "val": "T"},
    ] + [{"typ": "groups", "val": g} for g in groups]
    return base64.b64encode(json.dumps({"claims": claims}).encode()).decode()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for v in ("PATIENT_DATA_MODE", "AUTH_REQUIRED", "TRUSTED_AUTH_PROXY",
              "SECRETS_PROVIDER", "AUDIT_SINK", "AUTH_PROVIDER", "DEMO_ROLE",
              "FASTAPI_BASE_URL", "CORS_ALLOWED_ORIGINS"):
        monkeypatch.delenv(v, raising=False)
    # Seed a synthetic MIMIC-IV-ED-Full case for the (full-MIMIC-only) resolver.
    proc = tmp_path / "processed"
    proc.mkdir()
    case = {
        "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000001,
        "subject_id": 10000001,
        "edstay": {"subject_id": 10000001, "stay_id": 30000001, "gender": "F",
                   "arrival_transport": "AMBULANCE", "disposition": "HOME"},
        "triage": {"subject_id": 10000001, "stay_id": 30000001, "heartrate": 88.0,
                   "chiefcomplaint": "CHEST PAIN", "acuity": 2},
        "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
    }
    (proc / "frontend_cases_override.jsonl").write_text(json.dumps(case))
    monkeypatch.setattr("app.config.settings.processed_dir", proc)
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(proc))
    yield


class TestCaseUidRouting:
    def test_list_cases_uids_are_pseudonymous(self):
        r = client.get("/cases")
        assert r.status_code == 200
        cases = r.json()["cases"]
        assert cases, "expected some KTAS cases"
        for c in cases[:20]:
            assert "~" in c["case_uid"]          # pseudonymous format
            assert ":" not in c["case_uid"]      # not the old raw format

    def test_get_and_assessment_round_trip_no_raw_id(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        assert client.get(f"/cases/{cuid}", headers=headers).status_code == 200
        a = client.post(f"/cases/{cuid}/assessments", headers=headers)
        assert a.status_code == 200
        assert a.json().get("case_uid") == cuid
        assert "stay_id" not in a.json()         # raw id never returned

    def test_multiagent_explanation_is_case_uid_keyed_and_no_raw_id(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["security-admins"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        async def _fake_team(case_uid, evidence, user_question=None):
            blob = json.dumps(evidence)
            assert case_uid == cuid
            assert evidence["case_uid"] == cuid
            assert "stay_id" not in blob
            assert "subject_id" not in blob
            assert "category_reasoning" in evidence
            assert "primary_drivers" in evidence["category_reasoning"]
            assert "model_probability_summary" in evidence["category_reasoning"]
            return {
                "status": "PASS",
                "agent_turns": [
                    {"agent": "IntakeAgent", "text": "Facts from verified evidence."},
                    {"agent": "ValidationAgent", "text": "No missing fields."},
                    {"agent": "SafetyReviewAgent", "text": "Human clinical review required."},
                    {"agent": "ExplanationAgent", "text": "Summary. Clinician review required."},
                ],
                "final_explanation": "Summary. Clinician review required.",
                "safety_failures": [],
            }

        monkeypatch.setattr(
            "app.agents.autogen_multi_agent_team.run_case_uid_team_explanation",
            _fake_team,
        )
        r = client.post(
            f"/cases/{cuid}/multiagent-explanations",
            headers=headers,
            json={"question": "Summarise the case"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["case_uid"] == cuid
        assert body["multiagent"] is True
        assert body["status"] == "PASS"
        assert "stay_id" not in json.dumps(body)
        from app.version import APP_VERSION, PACKAGE_CHECKPOINT
        assert body["app_version"] == APP_VERSION
        assert body["package_checkpoint"] == PACKAGE_CHECKPOINT

    def test_followup_single_agent_explanation_is_not_multiagent(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["security-admins"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        def _fake_llm(evidence, clinician_question=None):
            blob = json.dumps(evidence)
            assert "followup_comparison" in evidence
            assert evidence["followup_comparison"]["changed_fields"] == ["heartrate"]
            assert "stay_id" not in blob
            assert "subject_id" not in blob
            assert clinician_question == "Which edited vital mattered?"
            from app.schemas.workflow import ExplanationResult
            return ExplanationResult(
                explanation_status="PASS",
                explanation_text="Direct answer from one explanation agent.",
            )

        monkeypatch.setattr(
            "app.agents.llm_explanation_agent.run_llm_explanation",
            _fake_llm,
        )
        r = client.post(
            f"/cases/{cuid}/followups/explanations",
            headers=headers,
            json={
                "updated_vitals": {"heartrate": 180},
                "question": "Which edited vital mattered?",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["case_uid"] == cuid
        assert body["multiagent"] is False
        assert body["explanation_status"] == "PASS"
        assert body["explanation"] == "Direct answer from one explanation agent."
        assert "agent_turns" not in body
        assert "final_explanation" not in body
        assert "stay_id" not in json.dumps(body)

    def test_free_text_chatbot_route_disabled_but_multiagent_explanation_enabled(self, monkeypatch):
        # v18.5 design: the single-agent free-text /explanations chatbot stays
        # disabled on clinical screens (it is the ITD tool), but the clinician
        # MULTI-AGENT case-acuity explanation IS enabled (triage-screen right
        # column). They are separate features with separate permissions.
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.delenv("ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE", raising=False)
        # security_admin holds can_ask_chatbot, so reaching the /explanations
        # route exercises the DISABLED check specifically (not a missing perm).
        admin_headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["security-admins"])}
        cuid = client.get("/cases", headers=admin_headers).json()["cases"][0]["case_uid"]

        # Free-text chatbot on a clinical screen: still disabled by design.
        r_chat = client.post(
            f"/cases/{cuid}/explanations", headers=admin_headers,
            json={"question": "Summarise the case"},
        )
        assert r_chat.status_code == 403
        assert "disabled" in r_chat.json()["detail"]

        # Multi-agent case-acuity explanation: enabled for a clinician. With no
        # LLM configured it returns a clean NOT_CONFIGURED (200), not a 403.
        monkeypatch.setattr(
            "app.agents.autogen_multi_agent_team.load_azure_config", lambda: None
        )
        doctor_headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        r_ma = client.post(
            f"/cases/{cuid}/multiagent-explanations", headers=doctor_headers,
            json={"question": "Why this acuity?"},
        )
        assert r_ma.status_code == 200
        assert r_ma.json()["status"] == "NOT_CONFIGURED"

    def test_unknown_case_uid_404(self):
        assert client.get("/cases/kaggle-ktas~deadbeefdead").status_code == 404


class TestRbacOnCaseRoutes:
    def test_researcher_no_clinical_content(self):
        # demo stub is researcher; list returns cases but without clinical body
        first = client.get("/cases").json()["cases"][0]
        assert "case" not in first              # no clinical content for researcher

    def test_clinician_gets_clinical_content(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        r = client.get("/cases",
                       headers={"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])})
        assert r.status_code == 200
        first = r.json()["cases"][0]
        assert "triage" in first              # clinical content present (safe DTO)
        # and still no raw identifiers anywhere
        import json as _json
        assert "subject_id" not in _json.dumps(first)
        assert "stay_id" not in _json.dumps(first)

    def test_researcher_cannot_submit_review(self, monkeypatch):
        cuid = client.get("/cases").json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews",
                        json={"review_status": "ACCEPTED_AS_PRESENTED", "review_comment": "x"})
        assert r.status_code == 403            # researcher lacks submit_review

    def test_override_requires_reason(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews",
                        headers=headers,
                        json={"review_status": "OVERRIDDEN", "review_comment": "x",
                              "clinician_override": "Red"})
        assert r.status_code == 422            # override needs override_reason

    def test_invalid_review_status_rejected(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews",
                        headers=headers,
                        json={"review_status": "HACKED", "review_comment": "x"})
        assert r.status_code == 422

    def test_unbounded_review_comment_rejected(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        r = client.post(f"/cases/{cuid}/reviews",
                        headers=headers,
                        json={"review_status": "REVIEWED",
                              "review_comment": "x" * 5000})
        assert r.status_code == 422

    def test_accept_review_persists_case_state_and_audit(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        r = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "ACCEPTED_AS_PRESENTED",
                "review_comment": "Accepted for this individual case review.",
            },
        )
        assert r.status_code == 200
        state = r.json()["workflow_state"]
        assert state["review_status"] == "accepted_as_presented"
        assert state["case_level_clinician_acceptance"] is True
        assert state["acceptance_scope"] == "individual_case_review_only"

        from app.config import settings
        assert (settings.processed_dir / "human_reviews.jsonl").exists()
        assert (settings.processed_dir / "case_workflow_state.jsonl").exists()
        refreshed = client.get(f"/cases/{cuid}", headers=headers).json()
        assert refreshed["workflow_state"]["review_status"] == "accepted_as_presented"

    def test_request_more_information_persists_requested_fields(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        r = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "REQUEST_MORE_INFORMATION",
                "review_comment": "Need confirmation before review.",
                "requested_fields": ["repeat vitals", "clarify pain score"],
            },
        )
        assert r.status_code == 200
        state = r.json()["workflow_state"]
        assert state["review_status"] == "information_requested"
        assert state["requested_fields"] == ["repeat vitals", "clarify pain score"]
        refreshed = client.get(f"/cases/{cuid}", headers=headers).json()
        assert refreshed["workflow_state"]["requested_fields"] == [
            "repeat vitals",
            "clarify pain score",
        ]

    def test_review_escalation_persists_requested_state(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        r = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "ESCALATION_REQUIRED",
                "review_comment": "Needs senior review before disposition.",
                "escalation_target_role": "ed_doctor",
                "system_prediction": "Acuity 3",
                "clinician_decision": "Escalate",
            },
        )
        assert r.status_code == 200
        state = r.json()["workflow_state"]
        assert state["escalation_required"] is True
        assert state["case_status"] == "escalation_requested"
        assert state["escalation_state"] == "requested"
        assert state["escalation_status"] == "requested"
        assert state["escalation_target_role"] == "ed_doctor"
        assert state["escalation_reason"] == "Needs senior review before disposition."
        assert state["escalation_evidence"]["review_status"] == "ESCALATION_REQUIRED"

        refreshed = client.get(f"/cases/{cuid}", headers=headers).json()
        assert refreshed["workflow_state"]["escalation_required"] is True
        assert refreshed["workflow_state"]["escalation_state"] == "requested"
        assert refreshed["workflow_state"]["escalation_status"] == "requested"
        assert refreshed["workflow_state"]["escalation_target_role"] == "ed_doctor"

    def test_review_escalation_rejects_invalid_target_role(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        r = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "ESCALATION_REQUIRED",
                "review_comment": "Needs senior review before disposition.",
                "escalation_target_role": "emergency_physician",
            },
        )
        assert r.status_code == 422
        assert "Invalid escalation_target_role" in r.json()["detail"]

    def test_followup_escalation_persists_state_and_rerun_audit(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        class _Workflow:
            def __init__(self, acuity):
                self.acuity = acuity

            def model_dump(self, mode="json"):
                return {"final_acuity_assessment": {"final_acuity": self.acuity}}

        def _fake_followup(rc, updated_vitals, updated_complaint=None):
            return (
                _Workflow(3),
                _Workflow(2),
                [{"field": "heartrate", "previous": 88.0, "new": 140.0}],
            )

        import app.api.case_routes as case_routes
        monkeypatch.setattr(case_routes, "_run_followup_workflows", _fake_followup)

        r = client.post(
            f"/cases/{cuid}/followups",
            headers=headers,
            json={"updated_vitals": {"heartrate": 140}},
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["change_direction"] == "escalation"
        assert payload["escalation_required"] is True
        assert payload["workflow_state"]["escalation_state"] == "requested"
        assert payload["workflow_state"]["escalation_status"] == "requested"

        from app.config import settings
        assert (settings.processed_dir / "workflow_reruns.jsonl").exists()
        refreshed = client.get(f"/cases/{cuid}", headers=headers).json()
        assert refreshed["workflow_state"]["escalation_required"] is True
        assert refreshed["workflow_state"]["escalation_target_role"] == "clinical_supervisor"

    def test_escalation_confirm_requires_requested_state_and_persists(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        no_active = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "ESCALATION_CONFIRMED",
                "review_comment": "Confirm without request.",
            },
        )
        assert no_active.status_code == 409

        requested = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "ESCALATION_REQUIRED",
                "review_comment": "Needs senior review.",
            },
        )
        assert requested.status_code == 200

        confirmed = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "ESCALATION_CONFIRMED",
                "review_comment": "Senior clinician accepted escalation.",
            },
        )
        assert confirmed.status_code == 200
        state = confirmed.json()["workflow_state"]
        assert state["case_status"] == "escalated"
        assert state["review_status"] == "escalation_confirmed"
        assert state["escalation_state"] == "confirmed"
        assert state["escalation_status"] == "confirmed"
        assert state["escalation_confirmation_note"] == "Senior clinician accepted escalation."

    def test_triage_nurse_cannot_confirm_escalation_or_discharge(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        nurse_headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["triage-nurses"])}
        cuid = client.get("/cases", headers=nurse_headers).json()["cases"][0]["case_uid"]

        requested = client.post(
            f"/cases/{cuid}/reviews",
            headers=nurse_headers,
            json={
                "review_status": "ESCALATION_REQUIRED",
                "review_comment": "Needs senior review.",
            },
        )
        assert requested.status_code == 200

        confirm = client.post(
            f"/cases/{cuid}/reviews",
            headers=nurse_headers,
            json={
                "review_status": "ESCALATION_CONFIRMED",
                "review_comment": "Nurse should not confirm escalation.",
            },
        )
        assert confirm.status_code == 403

        discharge = client.post(
            f"/cases/{cuid}/reviews",
            headers=nurse_headers,
            json={
                "review_status": "DISCHARGED",
                "review_comment": "Nurse should not close case.",
            },
        )
        assert discharge.status_code == 403

    def test_researcher_cannot_access_operational_workflow_queue(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["researchers"])}
        r = client.get("/workflow/queue", headers=headers)
        assert r.status_code == 403

    def test_patient_mode_workflow_queue_fails_closed_when_state_read_hits_cap(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["clinical-supervisors"])}

        import app.storage.case_state_repository as repo
        import app.api.case_routes as case_routes
        monkeypatch.setattr(case_routes, "_patient_data_mode", lambda: True)

        def _fake_read_case_states(path, *, limit=50000):
            assert limit == 50000
            return [{"case_uid": f"case-{i}", "updated_at_utc": "2026-07-20T00:00:00+00:00"}
                    for i in range(limit)]

        monkeypatch.setattr(repo, "read_case_states", _fake_read_case_states)
        r = client.get("/workflow/queue", headers=headers)
        assert r.status_code == 503
        assert "safety cap" in r.json()["detail"]

    def test_terminal_escalation_and_discharge_actions_are_idempotent_conflicts(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        requested = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "ESCALATION_REQUIRED",
                "review_comment": "Needs senior review.",
            },
        )
        assert requested.status_code == 200

        first_confirm = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "ESCALATION_CONFIRMED",
                "review_comment": "First confirmation.",
            },
        )
        assert first_confirm.status_code == 200

        second_confirm = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "ESCALATION_CONFIRMED",
                "review_comment": "Second confirmation should not overwrite.",
            },
        )
        assert second_confirm.status_code == 409

        discharge_case = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "DISCHARGED",
                "review_comment": "Close once.",
            },
        )
        assert discharge_case.status_code == 200

        repeat_discharge = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "DISCHARGED",
                "review_comment": "Second close should not overwrite.",
            },
        )
        assert repeat_discharge.status_code == 409

    def test_discharge_closes_case_and_blocks_followup(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        r = client.post(
            f"/cases/{cuid}/reviews",
            headers=headers,
            json={
                "review_status": "DISCHARGED",
                "review_comment": "No longer active in triage queue.",
            },
        )
        assert r.status_code == 200
        state = r.json()["workflow_state"]
        assert state["case_status"] == "discharged"
        assert state["review_status"] == "discharged"
        assert state["overdue_vitals_alert_active"] is False
        assert state["notifications_suppressed"] is True

        followup = client.post(
            f"/cases/{cuid}/followups",
            headers=headers,
            json={"updated_vitals": {"heartrate": 90}},
        )
        assert followup.status_code == 409

    def test_overdue_vitals_alert_can_be_created_once_and_acknowledged(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["triage-nurses"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        from app.config import settings
        from app.storage.case_state_repository import append_case_state
        append_case_state(settings.processed_dir / "case_workflow_state.jsonl", {
            "case_uid": cuid,
            "source_dataset": "MIMIC-IV-ED-Full-v2.2",
            "updated_at_utc": "2000-01-01T00:00:00+00:00",
            "last_action": "INITIAL_CLOCK",
            "last_vitals_checked_at": "2000-01-01T00:00:00+00:00",
        })

        created = client.post(f"/cases/{cuid}/vitals/mark-overdue-alert", headers=headers)
        assert created.status_code == 200
        assert created.json()["status"] == "created"
        assert created.json()["workflow_state"]["overdue_vitals_alert_active"] is True

        duplicate = client.post(f"/cases/{cuid}/vitals/mark-overdue-alert", headers=headers)
        assert duplicate.status_code == 200
        assert duplicate.json()["status"] == "already_active"

        ack = client.post(f"/cases/{cuid}/vitals/acknowledge-overdue", headers=headers)
        assert ack.status_code == 200
        assert ack.json()["status"] == "acknowledged"
        assert ack.json()["workflow_state"]["overdue_vitals_alert_active"] is False
        assert ack.json()["workflow_state"]["overdue_vitals_acknowledged_at"]

        recreated = client.post(f"/cases/{cuid}/vitals/mark-overdue-alert", headers=headers)
        assert recreated.status_code == 409
        assert "already been acknowledged" in recreated.json()["detail"]

    def test_overdue_vitals_sweep_creates_due_alert_without_case_button(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["clinical-supervisors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        from app.config import settings
        from app.storage.case_state_repository import append_case_state
        append_case_state(settings.processed_dir / "case_workflow_state.jsonl", {
            "case_uid": cuid,
            "source_dataset": "MIMIC-IV-ED-Full-v2.2",
            "updated_at_utc": "2000-01-01T00:00:00+00:00",
            "last_action": "INITIAL_CLOCK",
            "last_vitals_checked_at": "2000-01-01T00:00:00+00:00",
        })

        swept = client.post("/workflow/overdue-vitals/sweep", headers=headers)
        assert swept.status_code == 200
        assert swept.json()["created"] == 1

        refreshed = client.get(f"/cases/{cuid}", headers=headers).json()
        state = refreshed["workflow_state"]
        assert state["overdue_vitals_alert_active"] is True
        assert state["overdue_vitals_alert_created_by_role"] == "system_overdue_vitals_sweeper"

    def test_overdue_vitals_acknowledge_requires_active_alert(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["triage-nurses"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        ack = client.post(f"/cases/{cuid}/vitals/acknowledge-overdue", headers=headers)
        assert ack.status_code == 409
        assert "no active overdue-vitals notification" in ack.json()["detail"]

    def test_overdue_vitals_alert_rejects_fresh_vitals_clock(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["triage-nurses"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        from datetime import datetime, timezone
        from app.config import settings
        from app.storage.case_state_repository import append_case_state
        now = datetime.now(timezone.utc).isoformat()
        append_case_state(settings.processed_dir / "case_workflow_state.jsonl", {
            "case_uid": cuid,
            "source_dataset": "MIMIC-IV-ED-Full-v2.2",
            "updated_at_utc": now,
            "last_action": "VITALS_UPDATED",
            "last_vitals_checked_at": now,
            "last_vitals_updated_at": now,
        })

        r = client.post(f"/cases/{cuid}/vitals/mark-overdue-alert", headers=headers)
        assert r.status_code == 409
        assert "210-minute" in r.json()["detail"]

    def test_followup_updated_vitals_are_visible_on_subsequent_case_read(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

        r = client.post(
            f"/cases/{cuid}/followups",
            headers=headers,
            json={"updated_vitals": {"heartrate": 140}},
        )
        assert r.status_code == 200
        assert r.json()["workflow_state"]["latest_triage_updates"]["heartrate"] == 140

        refreshed = client.get(f"/cases/{cuid}", headers=headers)
        assert refreshed.status_code == 200
        assert refreshed.json()["triage"]["heartrate"] == 140

    def test_followup_additional_info_and_scan_metadata_persist(self, monkeypatch, tmp_path):
        headers = {"X-Demo-Role": "ed_doctor"}
        cuid = client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]
        monkeypatch.setenv("ACCESS_AUDIT_DIR", str(tmp_path))
        monkeypatch.setattr("app.config.settings.processed_dir", tmp_path)

        class _Workflow:
            def __init__(self, acuity):
                self.acuity = acuity

            def model_dump(self, mode="json"):
                return {"final_acuity_assessment": {"final_acuity": self.acuity}}

        def _fake_followup(rc, updated_vitals, updated_complaint=None):
            return (_Workflow(3), _Workflow(3), [])

        import app.api.case_routes as case_routes
        monkeypatch.setattr(case_routes, "_run_followup_workflows", _fake_followup)

        r = client.post(
            f"/cases/{cuid}/followups",
            headers=headers,
            json={
                "updated_vitals": {},
                "updated_complaint": "worse abdominal pain",
                "updated_context": "repeat triage note",
                "scan_uploads": [
                    {
                        "filename": "scan.png",
                        "content_type": "image/png",
                        "size_bytes": 123,
                        "sha256": "a" * 64,
                    }
                ],
            },
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["workflow_state"]["review_status"] == "reassessment_complete"
        assert "chiefcomplaint" in payload["workflow_state"]["changed_fields"]
        assert "supporting_scan_metadata" in payload["workflow_state"]["changed_fields"]
        assert payload["workflow_state"]["scan_uploads"][0]["analysis_status"] == (
            "metadata_recorded_pending_multimodal_analysis"
        )
        assert payload["workflow_state"]["scan_uploads"][0]["bytes_stored"] is False


class TestDisplayIdentity:
    def test_display_identity_uses_real_source_ids_only(self):
        from app.api.safe_dto import safe_display_identity

        out = safe_display_identity({
            "subject_id": 10003199,
            "stay_id": 39219866,
            "edstay": {"subject_id": 10003199, "stay_id": 39219866},
        })
        assert out["patient_display_label"] == "Patient 10003199"
        assert out["encounter_display_label"] == "Stay 39219866"

    def test_display_identity_does_not_fabricate_missing_ids(self):
        from app.api.safe_dto import safe_display_identity

        assert safe_display_identity({"edstay": {}, "triage": {}}) == {}

    def test_display_identity_prefers_real_name_when_present(self):
        from app.api.safe_dto import safe_display_identity

        out = safe_display_identity({
            "patient": {"full_name": "Alex Murphy"},
            "stay_id": 39219866,
        })
        assert out["patient_display_label"] == "Alex Murphy"
        assert out["patient_display_name"] == "Alex Murphy"
        assert out["encounter_display_label"] == "Stay 39219866"


class TestSecurityStatus:
    def test_demo_status_is_safe(self):
        from app.security.security_status import build_security_status
        s = build_security_status()
        assert s["current_mode"] == "public_demo"
        assert s["is_safe"] is True
        assert s["demo_role_switcher_enabled"] is True
        # never leak a path/secret
        assert "full_mimic_path" not in s
        assert "pseudonym_secret" not in s

    def test_patient_mode_flags_unsafe(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        from app.security.security_status import build_security_status
        s = build_security_status()
        assert s["current_mode"] == "secured_research"
        assert s["is_safe"] is False
        assert any("AUTH_REQUIRED" in p for p in s["unsafe_combinations"])

    def test_cors_wildcard_flagged(self, monkeypatch):
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
        from app.security.security_status import unsafe_combinations
        assert any("CORS" in p for p in unsafe_combinations())

    def test_security_status_endpoint_rbac(self, monkeypatch):
        # researcher (demo stub) lacks view_security_status
        assert client.get("/security/status").status_code == 403
        # security_admin can view
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        r = client.get("/security/status",
                       headers={"X-MS-CLIENT-PRINCIPAL": _principal(["security-admins"])})
        assert r.status_code == 200
        assert r.json()["current_mode"] == "public_demo"


class TestResolverConsistency:
    def test_uid_built_in_ui_matches_resolver(self):
        from app.schemas.workflow_run import make_case_uid
        from app.api import case_resolver
        cases = case_resolver.list_cases()
        rc = cases[0]
        ui_uid = make_case_uid(rc.source_dataset, rc.stay_id)
        assert ui_uid == rc.case_uid
        assert case_resolver.resolve(ui_uid).stay_id == rc.stay_id
