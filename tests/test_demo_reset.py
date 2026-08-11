"""Tests for the explicit, audited demo reset.

This is the only destructive-looking action in the app, so the refusal paths and
the archive-not-delete guarantee matter more than the happy path.
"""
from __future__ import annotations

import json

import pytest

from app.storage.demo_reset import (
    CONFIRMATION_PHRASE,
    RESETTABLE_FILENAMES,
    DemoResetRefused,
    assert_reset_allowed,
    reset_demo_state,
    resolve_audit_log_path,
)


@pytest.fixture(autouse=True)
def _isolate_audit_path(tmp_path, monkeypatch):
    """Keep the audit log inside tmp_path.

    reset_demo_state resolves the access-audit file through the audit writer,
    which uses ACCESS_AUDIT_DIR (or a RELATIVE "data/processed" default) rather
    than processed_dir. Without pinning it, these tests would archive the real
    audit log of whoever runs them.
    """
    audit_dir = tmp_path / "audit_home"
    audit_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(audit_dir))
    monkeypatch.delenv("LOCAL_CREDENTIALED_RESEARCH", raising=False)
    return audit_dir


def _seed(processed_dir, records_per_file: int = 3) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    for name in RESETTABLE_FILENAMES:
        target = (
            resolve_audit_log_path() if name == "access_audit.jsonl"
            else processed_dir / name
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n".join(json.dumps({"n": i, "file": name}) for i in range(records_per_file)),
            encoding="utf-8",
        )


class TestResetRefusals:
    def test_refused_in_patient_data_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        with pytest.raises(DemoResetRefused, match="PATIENT_DATA_MODE"):
            assert_reset_allowed()

    def test_refused_with_real_patient_data(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REAL_PATIENT_DATA", "true")
        with pytest.raises(DemoResetRefused, match="REAL_PATIENT_DATA"):
            assert_reset_allowed()

    def test_refusal_leaves_every_file_untouched(self, tmp_path, monkeypatch):
        processed = tmp_path / "processed"
        _seed(processed)
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        with pytest.raises(DemoResetRefused):
            reset_demo_state(processed)
        for name in RESETTABLE_FILENAMES:
            live = (
                resolve_audit_log_path() if name == "access_audit.jsonl"
                else processed / name
            )
            assert live.exists(), f"{name} must survive a refused reset"


class TestResetArchivesRatherThanDeletes:
    def test_files_are_moved_not_destroyed(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("REAL_PATIENT_DATA", raising=False)
        processed = tmp_path / "processed"
        _seed(processed, records_per_file=4)

        manifest = reset_demo_state(processed, actor_user_id="demo-itd", actor_role="security_admin")

        assert manifest["status"] == "reset_complete"
        assert manifest["deleted_anything"] is False
        assert manifest["records_archived"] == 4 * len(RESETTABLE_FILENAMES)

        archive = tmp_path / "processed" / "_archived_resets"
        assert archive.exists()
        for name in RESETTABLE_FILENAMES:
            live = (
                resolve_audit_log_path() if name == "access_audit.jsonl"
                else processed / name
            )
            # Cleared from the live location...
            assert not live.exists(), name
            # ...but still recoverable, with content intact.
            archived = list(archive.rglob(name))
            assert len(archived) == 1, name
            assert len(archived[0].read_text(encoding="utf-8").strip().splitlines()) == 4

    def test_actor_is_recorded_in_the_manifest(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("REAL_PATIENT_DATA", raising=False)
        processed = tmp_path / "processed"
        _seed(processed)
        manifest = reset_demo_state(
            processed, actor_user_id="demo-aoibhinn", actor_role="security_admin"
        )
        assert manifest["actor_user_id"] == "demo-aoibhinn"
        assert manifest["actor_role"] == "security_admin"
        assert manifest["reset_at_utc"]

    def test_dry_run_changes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("REAL_PATIENT_DATA", raising=False)
        processed = tmp_path / "processed"
        _seed(processed)
        manifest = reset_demo_state(processed, dry_run=True)
        assert manifest["status"] == "dry_run"
        assert manifest["archived"], "dry run should still report what it would archive"
        for name in RESETTABLE_FILENAMES:
            live = (
                resolve_audit_log_path() if name == "access_audit.jsonl"
                else processed / name
            )
            assert live.exists()

    def test_missing_files_are_reported_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("REAL_PATIENT_DATA", raising=False)
        processed = tmp_path / "processed"
        processed.mkdir(parents=True)
        manifest = reset_demo_state(processed)
        assert manifest["archived"] == []
        assert set(manifest["not_present"]) == set(RESETTABLE_FILENAMES)

    def test_back_to_back_resets_do_not_overwrite_archived_evidence(
        self, tmp_path, monkeypatch
    ):
        """Two resets in the same second must not share an archive directory.

        The previous version of this test ended in `... or True`, which made it
        impossible to fail: it claimed to guard against archive collisions while
        asserting nothing. Path.replace() overwrites silently, so a collision
        would destroy the first reset's evidence with no error.
        """
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("REAL_PATIENT_DATA", raising=False)
        processed = tmp_path / "processed"

        dirs = []
        for marker in range(6):          # tight loop: same wall-clock second
            _seed(processed, records_per_file=marker + 1)
            dirs.append(reset_demo_state(processed)["archive_directory"])

        assert len(set(dirs)) == len(dirs), f"archive directories collided: {dirs}"

        # Every reset's evidence must still be on disk, with its own row count.
        archive_root = processed / "_archived_resets"
        found = sorted(
            len(f.read_text(encoding="utf-8").strip().splitlines())
            for f in archive_root.rglob("human_reviews.jsonl")
        )
        assert found == [1, 2, 3, 4, 5, 6], found

    def test_collision_guard_holds_when_the_clock_does_not_move(
        self, tmp_path, monkeypatch
    ):
        """Freeze the clock so every reset gets an identical timestamp."""
        import app.storage.demo_reset as mod
        from datetime import datetime, timezone

        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("REAL_PATIENT_DATA", raising=False)
        frozen = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen

        monkeypatch.setattr(mod, "datetime", _FrozenDatetime)
        processed = tmp_path / "processed"
        dirs = []
        for _ in range(4):
            _seed(processed)
            dirs.append(reset_demo_state(processed)["archive_directory"])
        assert len(set(dirs)) == 4, f"collided under a frozen clock: {dirs}"


class TestResetRouteContract:
    def test_confirmation_phrase_is_required(self):
        # The phrase is what makes this a deliberate act rather than a stray POST.
        assert CONFIRMATION_PHRASE == "RESET DEMO DATA"

    def test_audit_log_is_archived_not_exempted(self):
        """The access audit is cleared for a clean demo, but never destroyed."""
        assert "access_audit.jsonl" in RESETTABLE_FILENAMES
