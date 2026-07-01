"""Phase 6: full-MIMIC seam guards — fail closed, require patient-data mode,
anti-copy guard. The credentialed data is NOT present here; we test the GUARDS.

We patch settings.mimic_full_ed_dir and the patient_data_mode() the loader uses,
rather than reloading modules (which breaks the settings-by-reference import)."""
from pathlib import Path

import pytest

from app.config import settings
from app.data_pipeline import mimic_full_loader as full
from app.data_pipeline.mimic_full_loader import CredentialedDataError


def test_fails_closed_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "mimic_full_ed_dir", None, raising=False)
    assert full.is_full_mimic_available() is False
    with pytest.raises(CredentialedDataError):
        full.validate_full_mimic_dir()


def test_requires_a_credentialed_profile(monkeypatch, tmp_path):
    ed = tmp_path / "ed"; ed.mkdir()
    monkeypatch.setattr(settings, "mimic_full_ed_dir", ed, raising=False)
    # neither production nor local-research profile active
    monkeypatch.setattr(full, "credentialed_data_access_allowed", lambda: False)
    with pytest.raises(CredentialedDataError) as e:
        full.validate_full_mimic_dir()
    msg = str(e.value)
    assert "Credentialed-data access is not enabled" in msg
    assert "LOCAL_CREDENTIALED_RESEARCH" in msg and "PATIENT_DATA_MODE" in msg


def test_anti_copy_guard_rejects_path_inside_repo(monkeypatch):
    repo_root = Path(full.__file__).resolve().parents[2]
    inside = repo_root / "data" / "raw"
    monkeypatch.setattr(settings, "mimic_full_ed_dir", inside, raising=False)
    monkeypatch.setattr(full, "credentialed_data_access_allowed", lambda: True)
    with pytest.raises(CredentialedDataError) as e:
        full.validate_full_mimic_dir()
    assert "inside the repository" in str(e.value)


def test_validates_when_safe(monkeypatch, tmp_path):
    ed = tmp_path / "credentialed" / "ed"; ed.mkdir(parents=True)
    # the schema guard requires the core tables to be present
    (ed / "edstays.csv.gz").write_bytes(b"")
    (ed / "triage.csv.gz").write_bytes(b"")
    monkeypatch.setattr(settings, "mimic_full_ed_dir", ed, raising=False)
    monkeypatch.setattr(full, "credentialed_data_access_allowed", lambda: True)
    report = full.validate_full_mimic_dir()
    assert "edstays.csv.gz" in report["present_files"]
    assert report["ready"] is False
    assert full.is_full_mimic_available() is True
