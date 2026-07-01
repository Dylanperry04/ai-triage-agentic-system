"""Phase 3: pseudonymisation, log redaction, secrets/audit-sink seams."""
import os
import tempfile
from pathlib import Path

import orjson
import pytest

from app.security.redaction import (
    pseudonymous_case_uid, redact_for_log, redact_text,
    assert_no_raw_identifiers, PatientDataLeakError, IDENTIFIER_KEYS,
)
from app.security.secrets_provider import (
    get_secrets_provider, EnvSecretsProvider, KeyVaultSecretsProvider,
)
from app.security.audit_sink import (
    get_audit_sink, LocalJsonlAuditSink, EncryptedDurableAuditSink,
)


class TestPseudonymisation:
    def test_stable_and_dataset_visible(self):
        a = pseudonymous_case_uid("MIMIC-IV-ED-Demo-v2.2", 37887480)
        b = pseudonymous_case_uid("MIMIC-IV-ED-Demo-v2.2", 37887480)
        assert a == b                       # stable
        assert a.startswith("MIMIC-IV-ED-Demo-v2.2~")  # dataset readable
        assert "37887480" not in a          # raw id not exposed
        digest = a.rsplit("~", 1)[1]
        assert len(digest) == 24            # 96-bit truncated HMAC
        assert all(c in "0123456789abcdef" for c in digest)

    def test_distinct_cases_distinct_tokens(self):
        assert (pseudonymous_case_uid("MIMIC", 1) != pseudonymous_case_uid("MIMIC", 2))
        assert (pseudonymous_case_uid("MIMIC", 1) != pseudonymous_case_uid("KTAS", 1))

    def test_salt_changes_token(self, monkeypatch):
        monkeypatch.setenv("PSEUDONYM_SECRET", "salt-A")
        a = pseudonymous_case_uid("MIMIC", 1)
        monkeypatch.setenv("PSEUDONYM_SECRET", "salt-B")
        b = pseudonymous_case_uid("MIMIC", 1)
        assert a != b  # different salt -> different pseudonym


class TestRedaction:
    def test_drops_all_identifier_keys(self):
        rec = {k: "x" for k in IDENTIFIER_KEYS}
        rec["final_category"] = "Red"
        red = redact_for_log(rec)
        assert red == {"final_category": "Red"}

    def test_scrubs_freetext(self):
        assert "[REDACTED_EMAIL]" in redact_text("contact john@x.com please")
        assert "[REDACTED_NUM]" in redact_text("MRN 12345678")

    def test_nested_redaction(self):
        rec = {"outer": {"subject_id": 1, "note": "call 0871234567"},
               "items": [{"mrn": "x"}, {"ok": 1}]}
        red = redact_for_log(rec)
        assert "subject_id" not in red["outer"]
        assert "[REDACTED_NUM]" in red["outer"]["note"]
        assert red["items"][0] == {}        # mrn dropped
        assert red["items"][1] == {"ok": 1}

    def test_keeps_pseudonymous_case_uid_intact(self):
        uid = pseudonymous_case_uid("MIMIC-IV-ED-Full-v2.2", 32259573)
        red = redact_for_log({"case_uid": uid, "note": "MRN 12345678"})
        assert red["case_uid"] == uid
        assert red["note"] == "MRN [REDACTED_NUM]"

    def test_preserves_safe_audit_metadata(self):
        record = {
            "timestamp_utc": "2026-06-27T22:02:18.123456+00:00",
            "created_at_utc": "2026-06-27T22:03:19+00:00",
            "workflow_run_id": "run-20260627-220218",
            "review_id": "review-123456789",
            "rerun_id": "rerun-987654321",
            "training_run_id": "train-20260627-abcdef",
            "model_hash": "a" * 64,
            "feature_schema_hash": "b" * 64,
            "app_version": "16.0.0",
            "package_checkpoint": "codex_fixed_v11",
            "route": "/cases/MIMIC-IV-ED-Full-v2.2~abcdef/assessments",
            "method": "POST",
            "action": "run_assessment",
            "role": "clinical_supervisor",
        }
        red = redact_for_log(record)
        assert red == record

    def test_redacts_identifiers_but_scrubs_known_free_text(self):
        uid = pseudonymous_case_uid("MIMIC-IV-ED-Full-v2.2", 32259573)
        red = redact_for_log({
            "case_uid": uid,
            "stay_id": 32259573,
            "subject_id": 19999999,
            "hadm_id": 28888888,
            "chiefcomplaint": "Chest pain for John Smith MRN 12345678",
            "review_comment": "Call patient at 0871234567",
        })
        assert red["case_uid"] == uid
        assert "stay_id" not in red
        assert "subject_id" not in red
        assert "hadm_id" not in red
        assert "[REDACTED_NAME]" in red["chiefcomplaint"]
        assert "[REDACTED_NUM]" in red["chiefcomplaint"]
        assert "[REDACTED_NUM]" in red["review_comment"]

    def test_leak_guard_raises(self):
        with pytest.raises(PatientDataLeakError):
            assert_no_raw_identifiers({"a": {"subject_id": 1}})
        # a clean record passes
        assert_no_raw_identifiers({"case_uid": "MIMIC#abc", "action": "view"})


class TestSecretsProvider:
    def test_env_provider(self, monkeypatch):
        monkeypatch.setenv("SECRETS_PROVIDER", "env")
        monkeypatch.setenv("MY_SECRET", "v")
        assert get_secrets_provider().get_secret("MY_SECRET") == "v"

    def test_keyvault_fails_closed_without_client(self):
        assert KeyVaultSecretsProvider().get_secret("anything") is None


class TestAuditSink:
    def test_local_sink_redacts_before_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.jsonl"
            sink = LocalJsonlAuditSink(p)
            assert sink.write({"subject_id": 1, "action": "view", "case_uid": "MIMIC#a"}) is True
            written = orjson.loads(p.read_bytes().strip())
            assert "subject_id" not in written
            assert written["action"] == "view"

    def test_durable_sink_fails_closed_without_client(self):
        assert EncryptedDurableAuditSink().write({"action": "x"}) is False

    def test_durable_sink_redacts_when_client_present(self):
        captured = {}
        class FakeClient:
            def upsert(self, rec): captured.update(rec)
        sink = EncryptedDurableAuditSink(client=FakeClient())
        assert sink.write({"subject_id": 9, "action": "run", "case_uid": "MIMIC#a"}) is True
        assert "subject_id" not in captured
        assert captured["action"] == "run"
