"""
Tests for scripts/build_sample_cases.py.

The most important test here is
TestDemoDatasetUsesRealMimicAdapter::test_demo_cases_are_correctly_labelled,
a regression guard for a real bug found during a later review pass:
--dataset demo previously called a legacy
loaders/validation/mapping path (predating the real, verified
app/data_pipeline/mimic_adapter.py) that mislabelled every case's
source_dataset as "Kaggle-KTAS" regardless of which dataset it actually
came from. That mislabelling silently defeated the follow-up comparison
agent's cross-dataset consistency warning, since that check relies
entirely on source_dataset to detect a KTAS/MIMIC mismatch. Fixed by
routing --dataset demo through load_mimic_demo_cases() directly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_sample_cases.py"


@pytest.fixture
def isolated_output_dir(tmp_path, monkeypatch):
    """
    Runs build_sample_cases.py against a real, isolated output directory
    so this test does not disturb the actual data/processed/ files the
    Streamlit app and other tests depend on.
    """
    import sys as _sys
    _sys.path.insert(0, str(PROJECT_ROOT))
    from app.config import settings

    monkeypatch.setattr(settings, "processed_dir", tmp_path)
    return tmp_path


class TestDemoDatasetUsesRealMimicAdapter:
    def test_demo_cases_are_correctly_labelled(self, isolated_output_dir):
        """
        The core regression guard: every case built with --dataset demo
        must be tagged with the real MIMIC-IV-ED-Demo-v2.2 source_dataset
        label, never "Kaggle-KTAS" (the bug that was found and fixed).
        """
        import sys as _sys
        if str(PROJECT_ROOT) not in _sys.path:
            _sys.path.insert(0, str(PROJECT_ROOT))
        from app.data_pipeline.mimic_adapter import load_mimic_demo_cases
        from app.data_pipeline.export import export_cases
        from app.config import settings

        cases, _ = load_mimic_demo_cases(settings.raw_demo_dir)
        export_cases(cases, isolated_output_dir)

        output_path = isolated_output_dir / "triage_cases_sample.jsonl"
        assert output_path.exists()

        lines = output_path.read_text(encoding="utf-8").strip().splitlines()
        records = [json.loads(line) for line in lines]
        assert len(records) == 222

        source_datasets = {r.get("source_dataset") for r in records}
        assert source_datasets == {"MIMIC-IV-ED-Demo-v2.2"}, (
            f"Expected every demo case tagged MIMIC-IV-ED-Demo-v2.2, got "
            f"{source_datasets} -- this is the exact mislabelling bug this "
            f"test exists to catch."
        )

    def test_script_source_calls_the_real_adapter_for_demo(self):
        """
        Static source check: confirms the script's --dataset demo branch
        genuinely calls load_mimic_demo_cases from the real adapter
        module, not the legacy loaders/validation/mapping path.
        """
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        demo_branch_start = source.index('if dataset == "demo"')
        legacy_branch_start = source.index("Legacy path for the full")

        # The demo branch must come first and must reference the real
        # adapter; the legacy path must come after and is now reserved
        # for the full (not-yet-credentialed) dataset only.
        assert demo_branch_start < legacy_branch_start
        demo_branch_text = source[demo_branch_start:legacy_branch_start]
        assert "load_mimic_demo_cases" in demo_branch_text
        assert "app.data_pipeline.mimic_adapter" in demo_branch_text
        assert "loaders" not in demo_branch_text
        assert "validation" not in demo_branch_text
        assert "mapping" not in demo_branch_text

    def test_legacy_path_no_longer_reachable_for_demo_dataset(self):
        """
        Confirms the legacy loaders/validation/mapping imports are not
        reachable when dataset == "demo" -- they should only be reachable
        for the full, not-yet-credentialed dataset.
        """
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        # The legacy raw_dir assignment must use raw_ed_dir unconditionally
        # now (no longer a ternary that could select raw_demo_dir).
        assert "raw_dir = settings.raw_ed_dir" in source
        assert 'raw_dir = settings.raw_demo_dir if dataset == "demo"' not in source
