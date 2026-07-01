"""
Priority-1 hardening proofs for the LOCAL_CREDENTIALED_RESEARCH profile:
  E. cannot start on a non-loopback bind (and fails closed if bind is undeclared)
  F. all cloud/LLM/W&B egress is blocked by default in that profile
Plus the mutual-exclusivity and data-guard invariants.
"""
import pytest


class TestLoopbackBindEnforcement:  # Proof E
    def test_startup_backend_uses_checked_bind_host(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "startup-backend.sh").read_text()
        assert 'BACKEND_BIND_HOST="${BACKEND_BIND_HOST:-127.0.0.1}"' in text
        assert '--host "${BACKEND_BIND_HOST}"' in text
        assert "--host 0.0.0.0" not in text

    def test_startup_frontend_requires_backend_url_in_local_research(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[1] / "startup-frontend.sh").read_text()
        assert 'LOCAL_CREDENTIALED_RESEARCH}" = "true"' in text
        assert "-z \"${FASTAPI_BASE_URL}\"" in text
        assert "ALLOW_IN_PROCESS_BACKEND_FOR_LOCAL_CREDENTIALED_RESEARCH" in text
        assert "requires FASTAPI_BASE_URL" in text

    def test_non_loopback_bind_refused(self, monkeypatch):
        from app.security.identity import assert_local_research_bind_is_loopback
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.setenv("BACKEND_BIND_HOST", "0.0.0.0")
        with pytest.raises(RuntimeError) as e:
            assert_local_research_bind_is_loopback()
        assert "loopback" in str(e.value).lower()

    def test_loopback_bind_allowed(self, monkeypatch):
        from app.security.identity import assert_local_research_bind_is_loopback
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        for host in ("127.0.0.1", "::1", "localhost", "127.0.0.5"):
            monkeypatch.setenv("BACKEND_BIND_HOST", host)
            assert_local_research_bind_is_loopback()  # no raise

    def test_undeclared_bind_fails_closed(self, monkeypatch):
        from app.security.identity import assert_local_research_bind_is_loopback
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.delenv("BACKEND_BIND_HOST", raising=False)
        with pytest.raises(RuntimeError):
            assert_local_research_bind_is_loopback()

    def test_guard_is_noop_outside_local_research(self, monkeypatch):
        from app.security.identity import assert_local_research_bind_is_loopback
        monkeypatch.delenv("LOCAL_CREDENTIALED_RESEARCH", raising=False)
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("BACKEND_BIND_HOST", "0.0.0.0")
        assert_local_research_bind_is_loopback()  # no raise (profile not active)

    def test_is_loopback_host(self):
        from app.security.identity import is_loopback_host
        assert is_loopback_host("127.0.0.1")
        assert is_loopback_host("::1")
        assert is_loopback_host("localhost")
        assert is_loopback_host("127.0.0.9")
        assert not is_loopback_host("0.0.0.0")
        assert not is_loopback_host("10.0.0.5")
        assert not is_loopback_host("192.168.1.10")
        assert not is_loopback_host("")


class TestCloudEgressBlockedInLocalResearch:  # Proof F
    def test_cloud_egress_off_by_default(self, monkeypatch):
        from app.security.identity import cloud_egress_allowed
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.delenv("ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH", raising=False)
        assert cloud_egress_allowed() is False

    def test_cloud_egress_requires_optin_and_governance_approval(self, monkeypatch):
        from app.security.identity import cloud_egress_allowed
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.setenv("ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH", "true")
        monkeypatch.delenv("APPROVED_CLOUD_LLM_DATA_PROCESSING", raising=False)
        assert cloud_egress_allowed() is False
        monkeypatch.setenv("APPROVED_CLOUD_LLM_DATA_PROCESSING", "true")
        assert cloud_egress_allowed() is True

    def test_cloud_egress_allowed_in_demo_and_production(self, monkeypatch):
        from app.security.identity import cloud_egress_allowed
        # demo
        for v in ("LOCAL_CREDENTIALED_RESEARCH", "PATIENT_DATA_MODE"):
            monkeypatch.delenv(v, raising=False)
        assert cloud_egress_allowed() is True
        # production patient-data mode (gated by its own config, not this switch)
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        assert cloud_egress_allowed() is True

    def test_azure_config_blocked_in_local_research(self, monkeypatch):
        from app.agents.autogen_team import load_azure_config
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.delenv("ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH", raising=False)
        for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
                  "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION"):
            monkeypatch.setenv(k, "x")
        # creds present but profile blocks egress -> None
        assert load_azure_config() is None

    def test_wandb_blocked_in_local_research(self, monkeypatch):
        from app.governance.wandb_logging import wandb_configured
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.delenv("ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH", raising=False)
        monkeypatch.setenv("WANDB_API_KEY", "x")
        assert wandb_configured() is False


class TestMutualExclusivity:
    def test_production_wins_over_local_research(self, monkeypatch):
        from app.security.identity import (
            local_credentialed_research_mode, patient_data_mode,
        )
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        assert patient_data_mode() is True
        assert local_credentialed_research_mode() is False


class TestLocalResearchAuditFailClosed:
    def test_access_audit_write_failure_raises(self, monkeypatch, tmp_path):
        from app.security.access_audit import AccessAuditError, record_access
        from app.security.identity import AuthContext
        import app.security.audit_sink as audit_sink

        class FailingSink:
            def write(self, record):
                return False

        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.setenv("BACKEND_BIND_HOST", "127.0.0.1")
        monkeypatch.setenv("LOCAL_CREDENTIALED_OUTPUT_DIR", str(tmp_path / "out"))
        monkeypatch.setattr(audit_sink, "get_audit_sink", lambda *a, **k: FailingSink())
        ctx = AuthContext(
            authenticated=True,
            user_id="local-researcher",
            roles=["triage_nurse"],
            source="local_stub",
            is_demo_stub=True,
        )
        with pytest.raises(AccessAuditError):
            record_access("run_assessment", "ALLOWED", ctx, case_uid="MIMIC-IV-ED-Full-v2.2~abc")
