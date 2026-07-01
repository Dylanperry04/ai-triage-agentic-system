"""Security layer: identity boundary (fail-closed) + RBAC authorization."""
import base64
import json
import importlib

import pytest

from app.security import identity as idmod
from app.security import authz
from app.security.identity import (
    AuthContext, AzureTrustedHeaderProvider, LocalStubProvider,
    resolve_auth_context, map_groups_to_roles,
    ROLE_TRIAGE_NURSE, ROLE_ED_DOCTOR, ROLE_RESEARCHER, ROLE_SECURITY_ADMIN,
    ROLE_GOVERNANCE_AUDITOR, ROLE_CLINICAL_SUPERVISOR,
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
    def test_nurse_permissions(self):
        ctx = AuthContext(authenticated=True, user_id="n", roles=[ROLE_TRIAGE_NURSE])
        assert authz.can_run_assessment(ctx)
        assert authz.can_submit_review(ctx)
        assert authz.can_view_clinical_content(ctx)   # needs evidence to review
        assert not authz.can_view_audit_log(ctx)
        assert not authz.can_export_data(ctx)
        assert not authz.can_view_security_status(ctx)

    def test_researcher_export_is_deidentified_only(self):
        ctx = AuthContext(authenticated=True, user_id="r", roles=[ROLE_RESEARCHER])
        assert authz.can_export_deidentified(ctx)
        assert not authz.can_export_identifiable(ctx)   # never identifiable
        assert not authz.can_submit_review(ctx)
        assert not authz.can_view_clinical_content(ctx)  # de-identified view only

    def test_security_admin_no_clinical_content(self):
        ctx = AuthContext(authenticated=True, user_id="s", roles=[ROLE_SECURITY_ADMIN])
        assert authz.can_view_security_status(ctx)
        assert authz.can_view_audit_log(ctx)
        assert not authz.can_view_clinical_content(ctx)  # excluded from patient content
        assert not authz.can_run_assessment(ctx)
        assert not authz.can_submit_review(ctx)

    def test_governance_auditor_is_read_only(self):
        ctx = AuthContext(authenticated=True, user_id="g", roles=[ROLE_GOVERNANCE_AUDITOR])
        assert authz.can_view_audit_log(ctx)
        assert authz.can_view_model_performance(ctx)
        assert not authz.can_run_assessment(ctx)
        assert not authz.can_submit_review(ctx)
        assert not authz.can_view_security_status(ctx)

    def test_clinical_supervisor_separate_from_security_admin(self):
        sup = AuthContext(authenticated=True, user_id="c", roles=[ROLE_CLINICAL_SUPERVISOR])
        assert authz.can_view_audit_log(sup)
        assert authz.can_view_model_performance(sup)
        assert authz.can_submit_review(sup)
        # supervisor is a CLINICAL role, not an infra one:
        assert not authz.can_view_security_status(sup)
        assert not authz.can_export_identifiable(sup)

    def test_require_permission_raises_when_absent(self):
        ctx = AuthContext(authenticated=True, user_id="n", roles=[ROLE_TRIAGE_NURSE])
        with pytest.raises(authz.AuthorizationError):
            authz.require_permission(ctx, authz.PERM_EXPORT_IDENTIFIABLE)

    def test_unauthenticated_has_no_permissions(self):
        assert authz.permissions_for(AuthContext(authenticated=False)) == set()


class TestAccessAudit:
    def test_check_and_audit_logs_allowed_and_denied(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ACCESS_AUDIT_DIR", str(tmp_path))
        from app.security.guard import check_and_audit
        from app.security.access_audit import read_access_audit
        nurse = AuthContext(authenticated=True, user_id="n1", roles=[ROLE_TRIAGE_NURSE],
                            source="local_stub", is_demo_stub=True)
        # allowed action
        assert check_and_audit(nurse, authz.PERM_RUN_ASSESSMENT, "run_assessment",
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
        r = client.get("/audit/events", headers={"X-Demo-Role": ROLE_CLINICAL_SUPERVISOR})
        assert r.status_code == 200
        events = r.json()["events"]
        assert events
        ts = events[-1]["timestamp_utc"]
        assert "[REDACTED" not in ts
        datetime.fromisoformat(ts)
