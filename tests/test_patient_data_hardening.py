"""v12 hardening: refuse dev pseudonym salt in patient-data mode; durable audit
and Key Vault fail closed (loudly) in patient-data mode when no client is wired."""
import hashlib
import hmac

import pytest

from app.security.redaction import pseudonymous_case_uid, PatientDataLeakError
from app.security.secrets_provider import (
    KeyVaultSecretsProvider, SecretsProviderNotConfiguredError, EnvSecretsProvider,
)
from app.security.audit_sink import (
    AuditSinkReadError,
    EncryptedDurableAuditSink,
    AuditSinkNotConfiguredError,
    LocalJsonlAuditSink,
)


@pytest.fixture(autouse=True)
def _reset_durable_probe_cache():
    import app.security.security_status as ss
    ss._DURABLE_AUDIT_PROBE_CACHE["signature"] = None
    ss._DURABLE_AUDIT_PROBE_CACHE["result"] = None
    yield
    ss._DURABLE_AUDIT_PROBE_CACHE["signature"] = None
    ss._DURABLE_AUDIT_PROBE_CACHE["result"] = None


class TestPseudonymSaltHardening:
    def test_demo_mode_uses_dev_salt(self, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("PSEUDONYM_SECRET", raising=False)
        assert pseudonymous_case_uid("MIMIC", 1).startswith("MIMIC~")

    def test_patient_mode_refuses_dev_salt(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.delenv("PSEUDONYM_SECRET", raising=False)
        with pytest.raises(PatientDataLeakError):
            pseudonymous_case_uid("MIMIC", 1)

    def test_patient_mode_with_real_salt_works(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("PSEUDONYM_SECRET", "real-secret")
        assert pseudonymous_case_uid("MIMIC", 1).startswith("MIMIC~")

    def test_patient_keyvault_mode_uses_keyvault_secret(self, monkeypatch):
        import app.security.secrets_provider as sp

        class FakeProvider:
            def get_secret(self, name):
                assert name == "PSEUDONYM_SECRET"
                return "vault-secret"

        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("SECRETS_PROVIDER", "keyvault")
        monkeypatch.delenv("PSEUDONYM_SECRET", raising=False)
        monkeypatch.setattr(sp, "get_secrets_provider", lambda: FakeProvider())

        uid = pseudonymous_case_uid("MIMIC", 1)
        expected = hmac.new(b"vault-secret", b"MIMIC:1", hashlib.sha256).hexdigest()[:24]
        assert uid == f"MIMIC~{expected}"

    def test_patient_keyvault_mode_refuses_plain_env_secret(self, monkeypatch):
        import app.security.secrets_provider as sp

        class FakeProvider:
            def get_secret(self, name):
                return "vault-secret"

        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("SECRETS_PROVIDER", "keyvault")
        monkeypatch.setenv("PSEUDONYM_SECRET", "env-secret")
        monkeypatch.setattr(sp, "get_secrets_provider", lambda: FakeProvider())

        with pytest.raises(PatientDataLeakError, match="plain environment PSEUDONYM_SECRET"):
            pseudonymous_case_uid("MIMIC", 1)


class TestSecretsProviderHardening:
    def test_keyvault_demo_returns_none(self, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        assert KeyVaultSecretsProvider().get_secret("x") is None

    def test_keyvault_patient_mode_raises_without_client(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        with pytest.raises(SecretsProviderNotConfiguredError):
            KeyVaultSecretsProvider().get_secret("PSEUDONYM_SECRET")

    def test_keyvault_with_client_reads(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        class FakeSecret:
            value = "v"
        class FakeClient:
            def get_secret(self, n): return FakeSecret()
        assert KeyVaultSecretsProvider(client=FakeClient()).get_secret("x") == "v"


class TestAuditSinkHardening:
    def test_durable_demo_returns_false(self, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        assert EncryptedDurableAuditSink().write({"a": 1}) is False

    def test_durable_patient_mode_raises_without_client(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        with pytest.raises(AuditSinkNotConfiguredError):
            EncryptedDurableAuditSink().write({"action": "x"})

    def test_durable_with_client_redacts_and_writes(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        captured = {}
        class FakeClient:
            def upsert(self, rec): captured.update(rec)
        ok = EncryptedDurableAuditSink(client=FakeClient()).write(
            {"subject_id": 9, "action": "run", "case_uid": "MIMIC#a"})
        assert ok is True
        assert "subject_id" not in captured  # redacted
        assert captured["action"] == "run"

    def test_durable_accepts_azure_table_upsert_entity_shape(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        captured = {}
        class FakeTableClient:
            def upsert_entity(self, entity): captured.update(entity)
        ok = EncryptedDurableAuditSink(client=FakeTableClient()).write({
            "timestamp_utc": "2026-06-27T22:02:18+00:00",
            "record_kind": "access_audit",
            "action": "view_audit_events",
            "roles": ["clinical_supervisor"],
            "subject_id": 9,
        })
        assert ok is True
        assert "subject_id" not in captured
        assert captured["timestamp_utc"] == "2026-06-27T22:02:18+00:00"
        assert captured["PartitionKey"] == "access_audit"
        assert captured["RowKey"] == "2026-06-27T22:02:18+00:00"

    def test_durable_probe_requires_write_and_readback(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        store = {}
        class FakeTableClient:
            def upsert_entity(self, entity):
                store[(entity["PartitionKey"], entity["RowKey"])] = dict(entity)
            def get_entity(self, partition_key, row_key):
                return store[(partition_key, row_key)]
        assert EncryptedDurableAuditSink(client=FakeTableClient()).probe_write_read() is True

    def test_durable_probe_rejects_write_only_client(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        class WriteOnlyClient:
            def upsert(self, rec):
                return None
        assert EncryptedDurableAuditSink(client=WriteOnlyClient()).probe_write_read() is False

    def test_durable_read_recent_uses_azure_table_query_and_redacts(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        captured = {}

        class FakeTableClient:
            def query_entities(self, query_filter, results_per_page=None):
                captured["query_filter"] = query_filter
                captured["results_per_page"] = results_per_page
                return [
                    {
                        "PartitionKey": "access_audit",
                        "RowKey": "1",
                        "timestamp_utc": "2026-06-27T01:00:00+00:00",
                        "action": "older",
                        "roles": "[\"clinical_supervisor\"]",
                        "subject_id": 12345678,
                    },
                    {
                        "PartitionKey": "access_audit",
                        "RowKey": "2",
                        "timestamp_utc": "2026-06-27T02:00:00+00:00",
                        "action": "newer",
                        "roles": "[\"security_admin\"]",
                        "detail": "call 555-123-4567",
                    },
                ]

        events = EncryptedDurableAuditSink(client=FakeTableClient()).read_recent(
            limit=1,
            record_kind="access_audit",
            since_utc="2026-06-27T00:00:00+00:00",
        )
        assert "PartitionKey eq 'access_audit'" in captured["query_filter"]
        assert "timestamp_utc ge '2026-06-27T00:00:00+00:00'" in captured["query_filter"]
        assert captured["results_per_page"] == 1
        assert len(events) == 1
        assert events[0]["action"] == "newer"
        assert events[0]["roles"] == ["security_admin"]
        assert events[0]["detail"] == "call [REDACTED_NUM]"
        assert "subject_id" not in events[0]

    def test_patient_durable_read_rejects_unbounded_list_only_client(self, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")

        class ListOnlyClient:
            def list_entities(self):
                return [{"PartitionKey": "access_audit", "RowKey": "1"}]

        with pytest.raises(AuditSinkReadError, match="bounded query_entities"):
            EncryptedDurableAuditSink(client=ListOnlyClient()).read_recent(limit=1)


class TestPatientDataStartupServiceChecks:
    def test_keyvault_secret_retrieval_replaces_env_secret_requirement(self, monkeypatch):
        import app.security.secrets_provider as sp
        import app.security.audit_sink as sinks
        from app.security.security_status import unsafe_combinations

        class FakeProvider:
            def get_secret(self, name):
                assert name == "PSEUDONYM_SECRET"
                return "from-key-vault"

        store = {}
        class FakeDurableClient:
            def upsert_entity(self, entity):
                store[(entity["PartitionKey"], entity["RowKey"])] = dict(entity)
            def get_entity(self, partition_key, row_key):
                return store[(partition_key, row_key)]

        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "azure")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("SECRETS_PROVIDER", "keyvault")
        monkeypatch.setenv("AUDIT_SINK", "durable")
        monkeypatch.setenv("PROVISIONAL_MTS_MODE", "off")
        monkeypatch.delenv("PSEUDONYM_SECRET", raising=False)
        monkeypatch.setattr(sp, "get_secrets_provider", lambda: FakeProvider())
        monkeypatch.setattr(
            sinks,
            "get_audit_sink",
            lambda *a, **k: sinks.EncryptedDurableAuditSink(client=FakeDurableClient()),
        )

        problems = unsafe_combinations()
        assert not any("PSEUDONYM_SECRET" in p for p in problems), problems
        assert not any("durable audit" in p for p in problems), problems

    def test_security_status_uses_cached_durable_probe_without_writing(self, monkeypatch):
        import app.security.secrets_provider as sp
        import app.security.audit_sink as sinks
        from app.security import security_status as ss

        class FakeProvider:
            def get_secret(self, name):
                assert name == "PSEUDONYM_SECRET"
                return "from-key-vault"

        calls = {"probe": 0}

        class FakeSink(sinks.EncryptedDurableAuditSink):
            def __init__(self):
                super().__init__(client=object())

            def probe_write_read(self):
                calls["probe"] += 1
                return True

        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "azure")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("SECRETS_PROVIDER", "keyvault")
        monkeypatch.setenv("AUDIT_SINK", "durable")
        monkeypatch.setenv("PROVISIONAL_MTS_MODE", "off")
        monkeypatch.setattr(sp, "get_secrets_provider", lambda: FakeProvider())
        monkeypatch.setattr(sinks, "get_audit_sink", lambda *a, **k: FakeSink())

        status_before_startup_probe = ss.build_security_status()
        assert calls["probe"] == 0
        assert status_before_startup_probe["durable_audit_startup_probe_ran"] is False
        assert status_before_startup_probe["durable_audit_write_read_probe_ok"] is False
        assert status_before_startup_probe["runtime_pseudonym_secret_source"] == "keyvault"
        assert any(
            "write/read startup probe" in p
            for p in status_before_startup_probe["unsafe_combinations"]
        )

        problems = ss.unsafe_combinations()
        assert calls["probe"] == 1
        assert not any("durable audit" in p for p in problems), problems

        status_after_startup_probe = ss.build_security_status()
        assert calls["probe"] == 1
        assert status_after_startup_probe["durable_audit_startup_probe_ran"] is True
        assert status_after_startup_probe["durable_audit_write_read_probe_ok"] is True
        assert status_after_startup_probe["runtime_pseudonym_secret_source"] == "keyvault"

    def test_patient_mode_flags_write_only_durable_audit_client(self, monkeypatch):
        import app.security.secrets_provider as sp
        import app.security.audit_sink as sinks
        from app.security.security_status import unsafe_combinations

        class FakeProvider:
            def get_secret(self, name):
                return "from-key-vault"

        class WriteOnlyClient:
            def upsert(self, rec):
                return None

        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "azure")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("SECRETS_PROVIDER", "keyvault")
        monkeypatch.setenv("AUDIT_SINK", "durable")
        monkeypatch.setenv("PROVISIONAL_MTS_MODE", "off")
        monkeypatch.setattr(sp, "get_secrets_provider", lambda: FakeProvider())
        monkeypatch.setattr(
            sinks,
            "get_audit_sink",
            lambda *a, **k: sinks.EncryptedDurableAuditSink(client=WriteOnlyClient()),
        )

        problems = unsafe_combinations()
        assert any("write/read startup probe" in p for p in problems), problems

    def test_keyvault_mode_flags_missing_vault_secret_even_if_env_secret_exists(self, monkeypatch):
        from app.security.security_status import unsafe_combinations

        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "azure")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("SECRETS_PROVIDER", "keyvault")
        monkeypatch.setenv("AUDIT_SINK", "durable")
        monkeypatch.setenv("PROVISIONAL_MTS_MODE", "off")
        monkeypatch.setenv("PSEUDONYM_SECRET", "env-secret-is-not-the-keyvault-proof")

        problems = unsafe_combinations()
        assert any("Key Vault retrieval" in p for p in problems)
        assert any("plain env PSEUDONYM_SECRET" in p for p in problems)

    def test_keyvault_mode_flags_env_secret_even_if_vault_secret_exists(self, monkeypatch):
        import app.security.secrets_provider as sp
        import app.security.audit_sink as sinks
        from app.security.security_status import unsafe_combinations

        class FakeProvider:
            def get_secret(self, name):
                return "from-key-vault"

        store = {}

        class FakeDurableClient:
            def upsert_entity(self, entity):
                store[(entity["PartitionKey"], entity["RowKey"])] = dict(entity)

            def get_entity(self, partition_key, row_key):
                return store[(partition_key, row_key)]

        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("AUTH_REQUIRED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "azure")
        monkeypatch.setenv("TRUSTED_AUTH_PROXY", "true")
        monkeypatch.setenv("SECRETS_PROVIDER", "keyvault")
        monkeypatch.setenv("AUDIT_SINK", "durable")
        monkeypatch.setenv("PROVISIONAL_MTS_MODE", "off")
        monkeypatch.setenv("PSEUDONYM_SECRET", "env-secret-is-not-allowed")
        monkeypatch.setattr(sp, "get_secrets_provider", lambda: FakeProvider())
        monkeypatch.setattr(
            sinks,
            "get_audit_sink",
            lambda *a, **k: sinks.EncryptedDurableAuditSink(client=FakeDurableClient()),
        )

        problems = unsafe_combinations()
        assert any("plain env PSEUDONYM_SECRET" in p for p in problems), problems
