"""Security layer: identity boundary (fail-closed) + RBAC authorization."""
import base64
import json
import importlib
from pathlib import Path

import pytest

from app.security import identity as idmod
from app.security import authz
from app.security.identity import (
    AuthContext, AzureTrustedHeaderProvider, LocalStubProvider,
    resolve_auth_context, map_groups_to_roles,
    ROLE_ED_NURSE, ROLE_TRIAGE_NURSE, ROLE_ED_DOCTOR, ROLE_RESEARCHER,
    ROLE_SECURITY_ADMIN, ROLE_GOVERNANCE_AUDITOR,
)


def _principal_header(claims):
    return base64.b64encode(json.dumps({"auth_typ": "aad", "claims": claims}).encode()).decode()


# ── Fail-closed semantics (the safety-critical part) ────────────────────────
class TestFailClosed:
    def test_patient_data_mode_refuses_stub(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.delenv("TRUSTED_AUTH_PROXY", raising=False)
        ctx = resolve_auth_context(request_headers={})
        # No trusted proxy + patient-data mode => NOT authenticated (fail closed).
        assert ctx.authenticated is False
        assert ctx.is_demo_stub is False
        assert authz.permissions_for(ctx) == set()

    def test_auth_required_refuses_stub(self, monkeypatch):
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("TRUSTED_AUTH_PROXY", raising=False)
        ctx = resolve_auth_context(request_headers={})
        assert ctx.authenticated is False

    def test_local_demo_mode_allows_stub(self, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("AUTH_REQUIRED", raising=False)
        monkeypatch.delenv("TRUSTED_AUTH_PROXY", raising=False)
        ctx = resolve_auth_context(request_headers={})
        assert ctx.authenticated is True
        assert ctx.is_demo_stub is True  # clearly marked

    def test_untrusted_headers_not_trusted(self, monkeypatch):
        # Even with a well-formed principal header, if not behind a trusted proxy
        # the header MUST NOT be trusted.
        monkeypatch.delenv("TRUSTED_AUTH_PROXY", raising=False)
        hdr = _principal_header([{"typ": "name", "val": "Attacker"},
                                 {"typ": "groups", "val": "ed-doctors"}])
        prov = AzureTrustedHeaderProvider()
        ctx = prov.get_context({"X-MS-CLIENT-PRINCIPAL": hdr})
        assert ctx.authenticated is False


# ── Azure trusted-header decoding (when genuinely behind the proxy) ─────────
class TestAzureHeaderProvider:
    def test_decodes_principal_and_maps_roles(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        hdr = _principal_header([
            {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier", "val": "user-123"},
            {"typ": "name", "val": "Dr Smith"},
            {"typ": "emails", "val": "smith@uhl.ie"},
            {"typ": "groups", "val": "ed-doctors"},
        ])
        ctx = AzureTrustedHeaderProvider().get_context({"X-MS-CLIENT-PRINCIPAL": hdr})
        assert ctx.authenticated is True
        assert ctx.user_id == "user-123"
        assert ctx.email == "smith@uhl.ie"
        assert ROLE_ED_DOCTOR in ctx.roles
        assert ctx.is_demo_stub is False

    def test_malformed_header_fails_closed(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        ctx = AzureTrustedHeaderProvider().get_context({"X-MS-CLIENT-PRINCIPAL": "!!!not-base64!!!"})
        assert ctx.authenticated is False

    def test_no_userid_fails_closed(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        hdr = _principal_header([{"typ": "name", "val": "No Id"}])
        ctx = AzureTrustedHeaderProvider().get_context({"X-MS-CLIENT-PRINCIPAL": hdr})
        assert ctx.authenticated is False

    def test_unknown_group_grants_no_role(self):
        assert map_groups_to_roles(["not-a-real-group"]) == []
        assert map_groups_to_roles(["ed-doctors", "triage-nurses"]) == [ROLE_ED_DOCTOR, ROLE_TRIAGE_NURSE]


# ── RBAC matrix ─────────────────────────────────────────────────────────────
class TestRBAC:
    def test_frontend_role_fixture_matches_backend_contract(self):
        fixture_path = (
            Path(__file__).parents[1]
            / "frontend-react" / "src" / "__tests__" / "fixtures" / "roles.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        assert set(fixture) == set(authz.ROLE_PERMISSIONS)
        for role, permissions in authz.ROLE_PERMISSIONS.items():
            assert set(fixture[role]["permissions"]) == permissions
            ctx = AuthContext(authenticated=True, user_id="fixture-check", roles=[role])
            assert set(fixture[role]["visible_tabs"]) == set(authz.visible_tabs_for(ctx))
        assert "clinical_supervisor" not in fixture

    def test_nurse_permissions(self):
        ctx = AuthContext(authenticated=True, user_id="n", roles=[ROLE_TRIAGE_NURSE])
        assert authz.can_run_assessment(ctx)
        assert authz.can_accept_acuity(ctx)
        assert authz.can_override_acuity(ctx)
        assert authz.can_request_information(ctx)
        assert authz.can_escalate_case(ctx)
        assert not authz.can_update_vitals(ctx)
        assert authz.can_view_workflow_queue(ctx)
        assert authz.can_view_clinical_content(ctx)   # needs evidence to review
        assert not authz.can_ask_chatbot(ctx)
        assert not authz.can_view_audit_log(ctx)
        assert not authz.can_export_data(ctx)
        assert not authz.can_view_security_status(ctx)
        tabs = authz.visible_tabs_for(ctx)
        assert "explainability" in tabs
        assert "maintainability" in tabs
        assert "itd_ask_tools" not in tabs
        assert "audit_dashboard" not in tabs
        assert "followup_comparison" not in tabs
        assert "cost_runtime" not in tabs
        assert "system_status" not in tabs

    def test_ed_nurse_is_observation_only(self):
        ctx = AuthContext(authenticated=True, user_id="edn", roles=[ROLE_ED_NURSE])
        assert authz.can_record_vitals(ctx)
        assert authz.can_update_vitals(ctx)
        assert authz.can_provide_requested_information(ctx)
        assert authz.can_acknowledge_overdue_vitals(ctx)
        assert not authz.can_run_assessment(ctx)
        assert not authz.can_accept_acuity(ctx)
        assert not authz.can_override_acuity(ctx)
        assert not authz.can_resolve_escalation(ctx)

    def test_researcher_export_is_deidentified_only(self):
        ctx = AuthContext(authenticated=True, user_id="r", roles=[ROLE_RESEARCHER])
        assert authz.can_export_deidentified(ctx)
        assert not authz.can_export_identifiable(ctx)   # never identifiable
        assert not authz.can_accept_acuity(ctx)
        assert not authz.can_view_workflow_queue(ctx)
        assert not authz.can_view_clinical_content(ctx)  # de-identified view only
        assert not authz.can_view_audit_log(ctx)
        assert "audit_dashboard" not in authz.visible_tabs_for(ctx)
        assert "model_performance" in authz.visible_tabs_for(ctx)
        assert "cost_runtime" not in authz.visible_tabs_for(ctx)
        assert "followup_comparison" not in authz.visible_tabs_for(ctx)

    def test_security_admin_itd_full_access(self):
        ctx = AuthContext(authenticated=True, user_id="s", roles=[ROLE_SECURITY_ADMIN])
        assert authz.can_view_security_status(ctx)
        assert authz.can_view_audit_log(ctx)
        assert authz.can_view_clinical_content(ctx)
        assert authz.can_run_assessment(ctx)
        assert authz.can_accept_acuity(ctx)
        assert authz.can_update_vitals(ctx)
        assert authz.can_resolve_escalation(ctx)
        assert authz.can_view_workflow_queue(ctx)
        assert authz.can_ask_chatbot(ctx)
        assert authz.role_display_name(ROLE_SECURITY_ADMIN) == "ITD"
        assert "itd_ask_tools" in authz.visible_tabs_for(ctx)
        assert "cost_runtime" not in authz.visible_tabs_for(ctx)
        assert "followup_comparison" not in authz.visible_tabs_for(ctx)
        assert "system_status" not in authz.visible_tabs_for(ctx)

    def test_governance_auditor_is_read_only(self):
        ctx = AuthContext(authenticated=True, user_id="g", roles=[ROLE_GOVERNANCE_AUDITOR])
        assert authz.can_view_audit_log(ctx)
        assert authz.can_view_workflow_queue(ctx)
        assert authz.can_view_model_performance(ctx)
        assert "audit_dashboard" in authz.visible_tabs_for(ctx)
        assert not authz.can_run_assessment(ctx)
        assert not authz.can_accept_acuity(ctx)
        assert not authz.can_view_security_status(ctx)

    def test_ed_doctor_is_final_escalation_authority_not_observation_role(self):
        doctor = AuthContext(authenticated=True, user_id="d", roles=[ROLE_ED_DOCTOR])
        assert authz.can_review_escalation(doctor)
        assert authz.can_resolve_escalation(doctor)
        assert authz.can_request_information(doctor)
        assert authz.can_view_workflow_queue(doctor)
        assert not authz.can_update_vitals(doctor)
        assert not authz.can_accept_acuity(doctor)
        assert not authz.can_view_security_status(doctor)

    def test_require_permission_raises_when_absent(self):
        ctx = AuthContext(authenticated=True, user_id="n", roles=[ROLE_TRIAGE_NURSE])
        with pytest.raises(authz.AuthorizationError):
            authz.require_permission(ctx, authz.PERM_EXPORT_IDENTIFIABLE)

    def test_unauthenticated_has_no_permissions(self):
        assert authz.permissions_for(AuthContext(authenticated=False)) == set()


class TestScheduledDemoDefaults:
    @pytest.mark.parametrize(
        "function_name",
        ["_overdue_vitals_sweeper_enabled", "_monthly_retraining_exports_enabled"],
    )
    def test_background_jobs_are_on_in_azure_demo_but_off_in_plain_tests(
        self, monkeypatch, function_name,
    ):
        from app import main

        explicit = {
            "_overdue_vitals_sweeper_enabled": "ENABLE_OVERDUE_VITALS_SWEEPER",
            "_monthly_retraining_exports_enabled": "ENABLE_MONTHLY_RETRAINING_EXPORTS",
        }[function_name]
        monkeypatch.delenv(explicit, raising=False)
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("AZURE_SUPERVISOR_DEMO_MODE", raising=False)
        monkeypatch.delenv("ALLOW_DEMO_ROLE_SWITCHER", raising=False)
        function = getattr(main, function_name)
        assert function() is False
        monkeypatch.setenv("AZURE_SUPERVISOR_DEMO_MODE", "true")
        assert function() is True
        monkeypatch.setenv(explicit, "false")
        assert function() is False


class TestAccessAudit:
    def test_access_audit_follows_alter_data_root_by_default(self, tmp_path, monkeypatch):
        from app.security.access_audit import _audit_path

        monkeypatch.delenv("ACCESS_AUDIT_DIR", raising=False)
        monkeypatch.delenv("LOCAL_CREDENTIALED_RESEARCH", raising=False)
        monkeypatch.setenv("ALTER_DATA_ROOT", str(tmp_path))
        assert _audit_path() == tmp_path / "processed" / "access_audit.jsonl"

    def test_check_and_audit_logs_allowed_and_denied(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_AUDIT_DIR", str(tmp_path))
        from app.security.guard import check_and_audit
        from app.security.access_audit import read_access_audit
        nurse = AuthContext(authenticated=True, user_id="n1", roles=[ROLE_TRIAGE_NURSE],
                            source="local_stub", is_demo_stub=True)
        # allowed action
        assert check_and_audit(nurse, authz.PERM_RUN_TRIAGE_ASSESSMENT, "run_assessment",
                               page="Triage Review", case_uid="MIMIC-IV-ED-Demo-v2.2:1") is True
        # denied action
        assert check_and_audit(nurse, authz.PERM_VIEW_AUDIT_LOG, "view_audit_log",
                               page="Audit Log") is False
        recs = read_access_audit(tmp_path / "access_audit.jsonl")
        assert len(recs) == 2
        decisions = {r.action: r.decision for r in recs}
        assert decisions["run_assessment"] == "ALLOWED"
        assert decisions["view_audit_log"] == "DENIED"
        # demo identity is flagged, case_uid recorded, no raw identifiers
        allow = next(r for r in recs if r.action == "run_assessment")
        assert allow.is_demo_identity is True
        assert allow.case_uid == "MIMIC-IV-ED-Demo-v2.2:1"
        assert allow.roles == [ROLE_TRIAGE_NURSE]

    def test_audit_events_endpoint_preserves_iso_timestamps(self, tmp_path, monkeypatch):
        from datetime import datetime
        from fastapi.testclient import TestClient
        from app.main import app

        monkeypatch.setenv("ACCESS_AUDIT_DIR", str(tmp_path))
        client = TestClient(app)
        r = client.get("/audit/events", headers={"X-Demo-Role": ROLE_GOVERNANCE_AUDITOR})
        assert r.status_code == 200
        events = r.json()["events"]
        assert events
        ts = events[-1]["timestamp_utc"]
        assert "[REDACTED" not in ts
        datetime.fromisoformat(ts)

    def test_access_audit_reader_skips_malformed_legacy_jsonl_lines(self, tmp_path):
        import orjson
        from app.security.access_audit import AccessAuditRecord, read_access_audit

        valid = AccessAuditRecord(
            timestamp_utc="2026-07-22T20:00:00+00:00",
            user_id="local-user",
            roles=["clinical_supervisor"],
            action="view_audit_log",
            page="/audit/events",
            decision="ALLOWED",
            permission=authz.PERM_VIEW_AUDIT_LOG,
            is_demo_identity=True,
            auth_source="local_fixed_role",
        )
        path = tmp_path / "access_audit.jsonl"
        path.write_bytes(
            b'xed_role","detail":""}\n'
            + orjson.dumps(valid.model_dump(mode="json"))
            + b"\n"
            + b'"auth_source":"local_fixed_role","detail":""}\n'
        )

        recs = read_access_audit(path)

        assert len(recs) == 1
        assert recs[0].action == "view_audit_log"
