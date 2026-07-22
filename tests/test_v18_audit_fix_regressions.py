"""v18.2 audit-fix regressions.

Pins the fixes from the external-audit verification pass:
1. Durable (Azure Table) reads must not drop workflow-state rows that only
   carry updated_at_utc, and durable writes stamp a canonical timestamp_utc.
2. OVERRIDDEN establishes an authoritative case_status (case leaves triage).
3. CASE_CLOSED keeps its admitted disposition instead of collapsing into
   "discharged".
4. The overdue-vitals sweep is a mutating action: read-only workflow roles
   (governance auditor) must get 403.
"""
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
    # This module runs late in the alphabetical order, after modules that can
    # leave dataset-mode env vars or resolver caches behind. Strip every mode
    # flag and clear the caches so the seeded synthetic case is authoritative.
    for v in ("PATIENT_DATA_MODE", "AUTH_REQUIRED", "TRUSTED_AUTH_PROXY",
              "SECRETS_PROVIDER", "AUDIT_SINK", "AUTH_PROVIDER", "DEMO_ROLE",
              "FASTAPI_BASE_URL", "CORS_ALLOWED_ORIGINS",
              "AZURE_SUPERVISOR_DEMO_MODE", "ALLOW_DEMO_ROLE_SWITCHER",
              "MIMIC_FULL_ED_DIR", "LOCAL_CREDENTIALED_RESEARCH",
              "REAL_PATIENT_DATA", "ALLOW_FULL_MIMIC_IN_AZURE_DEMO",
              "ALLOW_LEGACY_RAW_ID_ROUTES", "MIMIC_FULL_MODEL_PATH"):
        monkeypatch.delenv(v, raising=False)
    from app.api import case_resolver as _cr
    _cr._CASE_CACHE.clear()
    _cr._PARTIAL_CASE_CACHE.clear()
    _cr._COUNT_CACHE.clear()
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
    # Same seeding pattern as tests/test_case_routes_v13.py.
    (proc / "frontend_cases_override.jsonl").write_text(json.dumps(case))
    # tests/test_provisional_mts_ruleset.py importlib.reload()s app.config,
    # which REBINDS app.config.settings to a new instance while every module
    # that did `from app.config import settings` (resolver, repositories)
    # keeps the ORIGINAL instance. Patch both objects so this module is
    # immune to its position in the run order.
    monkeypatch.setattr("app.config.settings.processed_dir", proc)
    monkeypatch.setattr(_cr.settings, "processed_dir", proc, raising=False)
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(proc))
    yield
    _cr._CASE_CACHE.clear()
    _cr._PARTIAL_CASE_CACHE.clear()
    _cr._COUNT_CACHE.clear()


class _FakeTableClient:
    """Minimal Azure TableClient stand-in: query_entities + upsert_entity."""

    def __init__(self, entities):
        self.entities = list(entities)
        self.upserted = []
        self.last_filter = None

    def query_entities(self, query_filter, results_per_page=None):
        self.last_filter = query_filter
        return list(self.entities)

    def upsert_entity(self, entity):
        self.upserted.append(dict(entity))


class TestDurableSinkTimestampCompatibility:
    def test_query_filter_matches_updated_at_utc_rows(self):
        from app.security.audit_sink import EncryptedDurableAuditSink
        legacy_row = {
            "PartitionKey": "case_workflow_state_current",
            "RowKey": "case-1",
            "case_uid": "case-1",
            "record_kind": "case_workflow_state_current",
            "case_status": "accepted",
            # Legacy workflow-state rows: updated_at_utc only, NO timestamp_utc.
            "updated_at_utc": "2026-07-21T10:00:00+00:00",
        }
        fake = _FakeTableClient([legacy_row])
        sink = EncryptedDurableAuditSink(client=fake)
        records = sink.read_recent(
            10, record_kind="case_workflow_state_current",
            since_utc="2026-06-21T00:00:00+00:00",
        )
        # Server-side filter must accept either timestamp field...
        assert "updated_at_utc ge" in fake.last_filter
        assert "timestamp_utc ge" in fake.last_filter
        # ...and the legacy row must survive the read (this is the row Azure
        # would have silently excluded with a timestamp_utc-only filter).
        assert any(r.get("case_uid") == "case-1" for r in records)

    def test_durable_write_stamps_canonical_timestamp(self):
        from app.security.audit_sink import EncryptedDurableAuditSink
        fake = _FakeTableClient([])
        sink = EncryptedDurableAuditSink(client=fake)
        ok = sink.write({
            "record_kind": "case_workflow_state_current",
            "case_uid": "case-2",
            "updated_at_utc": "2026-07-21T11:22:33+00:00",
        })
        assert ok is True
        assert len(fake.upserted) == 1
        # The entity now carries the canonical query field, taken from the
        # record's own clock.
        assert fake.upserted[0]["timestamp_utc"] == "2026-07-21T11:22:33+00:00"

    def test_read_sort_falls_back_to_updated_at_utc(self):
        from app.security.audit_sink import EncryptedDurableAuditSink
        rows = [
            {"PartitionKey": "k", "RowKey": "b", "id": "newer",
             "updated_at_utc": "2026-07-21T12:00:00+00:00"},
            {"PartitionKey": "k", "RowKey": "a", "id": "older",
             "updated_at_utc": "2026-07-21T09:00:00+00:00"},
        ]
        sink = EncryptedDurableAuditSink(client=_FakeTableClient(rows))
        records = sink.read_recent(10, record_kind="k")
        assert [r["id"] for r in records] == ["older", "newer"]


class TestReviewStateSemantics:
    def _uid(self, headers):
        return client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

    def test_override_establishes_case_status(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/reviews", headers=headers, json={
            "review_status": "OVERRIDDEN",
            "review_comment": "Override recorded.",
            "clinician_override": "Very urgent (priority 2)",
            "override_reason": "New hypotension on repeat obs.",
        })
        assert r.status_code == 200
        state = client.get(f"/cases/{cuid}", headers=headers).json()["workflow_state"]
        # The decision is authoritative: the case must not look unreviewed.
        assert state["case_status"] == "overridden"
        assert state["review_status"] == "overridden"
        assert state["overridden_by_role"] == "ed_doctor"

    def test_case_closed_keeps_admitted_disposition(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/reviews", headers=headers, json={
            "review_status": "CASE_CLOSED",
            "review_comment": "Admitted to AMU.",
        })
        assert r.status_code == 200
        state = client.get(f"/cases/{cuid}", headers=headers).json()["workflow_state"]
        assert state["case_status"] == "case_closed"
        assert state["closed_disposition"] == "admitted"
        assert "discharged_at" not in state
        # And a genuine discharge still records the discharge disposition.

    def test_discharge_still_records_discharge(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["clinical-supervisors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/reviews", headers=headers, json={
            "review_status": "DISCHARGED",
            "review_comment": "Discharged home with safety-netting.",
        })
        assert r.status_code == 200
        state = client.get(f"/cases/{cuid}", headers=headers).json()["workflow_state"]
        assert state["case_status"] == "discharged"
        assert state["closed_disposition"] == "discharged"
        assert state["discharged_at"]


class TestSweepPermission:
    def test_governance_auditor_cannot_trigger_mutating_sweep(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["governance-auditors"])}
        r = client.post("/workflow/overdue-vitals/sweep", headers=headers)
        assert r.status_code == 403

    def test_clinical_supervisor_can_still_sweep(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["clinical-supervisors"])}
        r = client.post("/workflow/overdue-vitals/sweep", headers=headers)
        assert r.status_code == 200


class TestTerminalReviewClearsContradictoryState:
    """A terminal review decision (accept/override) must not leave the case in
    two clinical states at once: an accepted case cannot still read as actively
    escalated, and must not carry a stale request-for-info."""

    def _uid(self, headers):
        return client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

    def test_accept_after_escalation_clears_escalation(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        # Escalate, then accept.
        client.post(f"/cases/{cuid}/reviews", headers=headers, json={
            "review_status": "ESCALATION_REQUIRED",
            "review_comment": "Senior review please.",
            "escalation_target_role": "clinical_supervisor",
        })
        client.post(f"/cases/{cuid}/reviews", headers=headers, json={
            "review_status": "ACCEPTED_AS_PRESENTED",
            "review_comment": "Accepting as presented.",
        })
        state = client.get(f"/cases/{cuid}", headers=headers).json()["workflow_state"]
        assert state["case_status"] == "accepted"
        # The impossible pair (accepted + still-requested escalation) must be gone.
        assert state.get("escalation_required") is False
        assert str(state.get("escalation_status")) != "requested"

    def test_accept_after_request_info_clears_requested_fields(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        client.post(f"/cases/{cuid}/reviews", headers=headers, json={
            "review_status": "REQUEST_MORE_INFORMATION",
            "review_comment": "Need an ECG.",
            "requested_fields": ["ECG"],
        })
        client.post(f"/cases/{cuid}/reviews", headers=headers, json={
            "review_status": "ACCEPTED_AS_PRESENTED",
            "review_comment": "Info no longer needed; accepting.",
        })
        state = client.get(f"/cases/{cuid}", headers=headers).json()["workflow_state"]
        assert state["case_status"] == "accepted"
        assert "requested_fields" not in state


class TestFollowupRejectsImpossibleVitals:
    def _uid(self, headers):
        return client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

    def test_zero_perfusing_vitals_rejected(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        for vital in ("heartrate", "resprate", "o2sat", "sbp", "dbp"):
            r = client.post(f"/cases/{cuid}/followups", headers=headers,
                            json={"updated_vitals": {vital: 0}})
            assert r.status_code == 422, f"{vital}=0 should be rejected, got {r.status_code}"

    def test_pain_zero_still_valid(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        # Pain 0 (no pain) is a real observation and must be accepted.
        r = client.post(f"/cases/{cuid}/followups", headers=headers,
                        json={"updated_vitals": {"pain": 0}})
        assert r.status_code == 200

    def test_survivable_low_vitals_still_accepted(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        # Genuine peri-arrest physiology must still be enterable.
        r = client.post(f"/cases/{cuid}/followups", headers=headers,
                        json={"updated_vitals": {"heartrate": 25, "sbp": 60, "o2sat": 78}})
        assert r.status_code == 200


class TestScanMetadataRejectsBlankFilename:
    def _uid(self, headers):
        return client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

    def test_whitespace_only_filename_rejected(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/followups", headers=headers,
                        json={"scan_uploads": [{"filename": "   "}]})
        assert r.status_code == 422

    def test_named_scan_metadata_accepted(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/followups", headers=headers,
                        json={"updated_context": "CXR reviewed.",
                              "scan_uploads": [{"filename": "chest-xray.png", "content_type": "image/png"}]})
        assert r.status_code == 200

    def test_supporting_upload_stores_bytes_and_returns_metadata(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(
            f"/cases/{cuid}/supporting-uploads",
            headers=headers,
            files={"file": ("chest-xray.png", b"scan-bytes", "image/png")},
        )
        assert r.status_code == 200
        payload = r.json()
        assert payload["filename"] == "chest-xray.png"
        assert payload["content_type"] == "image/png"
        assert payload["size_bytes"] == len(b"scan-bytes")
        assert payload["bytes_stored"] is True
        assert payload["analysis_status"] == "uploaded_pending_clinician_review"
        from app.config import settings
        assert list((settings.processed_dir / "supporting_uploads").rglob("*chest-xray.png"))


class TestAssessmentPreviewIsNonAuditing:
    def _uid(self, headers):
        return client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

    def test_preview_writes_no_workflow_run_record(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        from app.config import settings
        runs = settings.processed_dir / "workflow_runs.jsonl"
        before = runs.read_text().count("\n") if runs.exists() else 0
        # Preview must NOT create an audit-run record...
        r = client.post(f"/cases/{cuid}/assessments?preview=true", headers=headers)
        assert r.status_code == 200
        assert r.json().get("preview") is True
        mid = runs.read_text().count("\n") if runs.exists() else 0
        assert mid == before, "preview assessment must not write a workflow-run record"
        # ...but the real (default) assessment still does.
        client.post(f"/cases/{cuid}/assessments", headers=headers)
        after = runs.read_text().count("\n") if runs.exists() else 0
        assert after == before + 1, "real assessment should write exactly one run record"


class TestEscalationAfterAcceptanceSupersedes:
    """The inverse of the terminal-review case: escalating an already-accepted
    case must mark the prior acceptance superseded, not leave a contradictory
    accepted+escalated pair with no explanation."""

    def _uid(self, headers):
        return client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

    def test_escalate_after_accept_marks_acceptance_superseded(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        client.post(f"/cases/{cuid}/reviews", headers=headers, json={
            "review_status": "ACCEPTED_AS_PRESENTED",
            "review_comment": "Accepting as presented.",
        })
        client.post(f"/cases/{cuid}/reviews", headers=headers, json={
            "review_status": "ESCALATION_REQUIRED",
            "review_comment": "New troponin — escalating after all.",
            "escalation_target_role": "clinical_supervisor",
        })
        state = client.get(f"/cases/{cuid}", headers=headers).json()["workflow_state"]
        assert state["case_status"] == "escalation_requested"
        # The stale acceptance must be demoted and the supersession recorded.
        assert state.get("case_level_clinician_acceptance") is False
        assert state.get("prior_acceptance_superseded_by_escalation") is True


class TestReassessmentSemanticsAreHonest:
    """A context-only reassessment must not claim the acuity was recomputed."""

    def _uid(self, headers):
        return client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

    def test_context_only_reports_not_scored(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/followups", headers=headers, json={
            "updated_context": "Collateral history from family; patient usually independent.",
        })
        assert r.status_code == 200
        info = r.json()["additional_information"]
        assert info["acuity_recomputed_from_structured_inputs"] is False
        assert info["context_and_scan_are_recorded_not_scored"] is True

    def test_vitals_change_reports_recomputed(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/followups", headers=headers, json={
            "updated_vitals": {"heartrate": 130},
        })
        assert r.status_code == 200
        assert r.json()["additional_information"]["acuity_recomputed_from_structured_inputs"] is True


class TestMultiAgentAcuityExplanationForClinicians:
    """The multi-agent case-acuity explanation (triage-screen right column) is a
    clinician-facing feature, distinct from the ITD free-text chatbot. Clinical
    roles must be able to reach it; the free-text /explanations chatbot stays
    disabled on clinical screens."""

    def _uid(self, headers):
        return client.get("/cases", headers=headers).json()["cases"][0]["case_uid"]

    def _fake_team_pass(self):
        async def _fake_team(case_uid, evidence, user_question=None):
            return {
                "status": "PASS",
                "agent_turns": [
                    {"agent": "IntakeAgent", "text": "Facts from verified evidence."},
                    {"agent": "ValidationAgent", "text": "No missing fields."},
                    {"agent": "SafetyReviewAgent", "text": "Human clinical review required."},
                    {"agent": "ExplanationAgent", "text": "Category reasoning. Clinician review required."},
                ],
                "final_explanation": "Category reasoning. Clinician review required.",
                "safety_failures": [],
            }
        return _fake_team

    def test_triage_nurse_can_get_multiagent_explanation(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setattr(
            "app.agents.autogen_multi_agent_team.run_case_uid_team_explanation",
            self._fake_team_pass(),
        )
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["triage-nurses"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/multiagent-explanations", headers=headers,
                        json={"question": "Why this acuity?"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["multiagent"] is True
        assert body["status"] == "PASS"
        assert len(body["agent_turns"]) == 4

    def test_ed_doctor_can_get_multiagent_explanation(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setattr(
            "app.agents.autogen_multi_agent_team.run_case_uid_team_explanation",
            self._fake_team_pass(),
        )
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/multiagent-explanations", headers=headers, json={})
        assert r.status_code == 200, r.text
        assert r.json()["multiagent"] is True

    def test_researcher_cannot_get_multiagent_explanation(self, monkeypatch):
        # Researcher works with aggregate/model evidence, not per-case clinical
        # explanation — must NOT have the case-acuity explanation permission.
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["researchers"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/multiagent-explanations", headers=headers, json={})
        assert r.status_code == 403

    def test_free_text_chatbot_route_still_disabled_on_clinical_screens(self, monkeypatch):
        # The single-agent /explanations chatbot is the ITD tool and stays
        # disabled by default even for a clinical role that could otherwise ask.
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.delenv("ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE", raising=False)
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["security-admins"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/explanations", headers=headers, json={"question": "hi"})
        assert r.status_code == 403

    def test_multiagent_explanation_can_be_disabled_by_flag(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("ALTER_DISABLE_MULTIAGENT_EXPLANATION", "true")
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/multiagent-explanations", headers=headers, json={})
        assert r.status_code == 403

    def test_multiagent_explanation_degrades_when_llm_not_configured(self, monkeypatch):
        # With no Azure OpenAI config and no mock, the runner returns a clean
        # NOT_CONFIGURED result (HTTP 200 with an honest message), not a crash.
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        for v in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setattr(
            "app.agents.autogen_multi_agent_team.load_azure_config", lambda: None
        )
        headers = {"X-MS-CLIENT-PRINCIPAL": _principal(["ed-doctors"])}
        cuid = self._uid(headers)
        r = client.post(f"/cases/{cuid}/multiagent-explanations", headers=headers, json={})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "NOT_CONFIGURED"
