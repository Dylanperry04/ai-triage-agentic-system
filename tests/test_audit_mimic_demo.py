"""
Tests for scripts/audit_mimic_demo.py.

The most important test here is test_vitalsign_vital_columns_are_excluded,
which guards against a real bug found while manually testing this script:
TRIAGE_INPUT_COLUMNS contains generic vital names ("temperature",
"heartrate", etc.) that exist in BOTH triage.csv and vitalsign.csv. An
earlier version of the classification logic checked that list before
checking which table the column came from, so vitalsign's vitals were
incorrectly classified as TRIAGE_TIME_SAFE -- silently contradicting the
script's own stated policy of excluding vitalsign.csv from triage-time
input. Fixed by checking the table name first, unconditionally, before
falling through to the generic column-name lists.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit_mimic_demo import audit_mimic_demo


@pytest.fixture
def fake_mimic_dir(tmp_path, monkeypatch):
    """
    Builds a tiny, real gzip-compressed CSV set on disk matching the exact
    verified MIMIC column shapes, then points the adapter's table loader
    at it via monkeypatching load_mimic_table's underlying file reads.
    """
    ed_dir = tmp_path / "ed"
    ed_dir.mkdir()

    tables = {
        "edstays": pd.DataFrame([
            {"subject_id": 1, "hadm_id": 100.0, "stay_id": 9001,
             "intime": "2125-01-01 10:00:00", "outtime": "2125-01-01 14:00:00",
             "gender": "F", "race": "WHITE", "arrival_transport": "AMBULANCE",
             "disposition": "ADMITTED"},
        ]),
        "triage": pd.DataFrame([
            {"subject_id": 1, "stay_id": 9001, "temperature": 99.0, "heartrate": 90.0,
             "resprate": 18.0, "o2sat": 97.0, "sbp": 120.0, "dbp": 80.0, "pain": "5",
             "acuity": 2.0, "chiefcomplaint": "FEVER"},
        ]),
        "vitalsign": pd.DataFrame([
            {"subject_id": 1, "stay_id": 9001, "charttime": "2125-01-01 11:00:00",
             "temperature": 99.5, "heartrate": 95.0, "resprate": 20.0, "o2sat": 96.0,
             "sbp": 118.0, "dbp": 78.0, "rhythm": "Sinus", "pain": "4"},
        ]),
        "diagnosis": pd.DataFrame([
            {"subject_id": 1, "stay_id": 9001, "seq_num": 1, "icd_code": "A00",
             "icd_version": 10, "icd_title": "TEST DIAGNOSIS"},
        ]),
        "medrecon": pd.DataFrame([
            {"subject_id": 1, "stay_id": 9001, "charttime": "2125-01-01 10:05:00",
             "name": "TEST MED", "gsn": "1", "ndc": "1", "etc_rn": 1,
             "etccode": None, "etcdescription": None},
        ]),
        "pyxis": pd.DataFrame([
            {"subject_id": 1, "stay_id": 9001, "charttime": "2125-01-01 10:10:00",
             "med_rn": 1, "name": "TEST MED", "gsn_rn": 1, "gsn": 1.0},
        ]),
    }
    for name, df in tables.items():
        df.to_csv(ed_dir / f"{name}.csv.gz", index=False, compression="gzip")

    return ed_dir


class TestAuditMimicDemo:
    def test_runs_without_error_on_real_shaped_data(self, fake_mimic_dir):
        report = audit_mimic_demo(fake_mimic_dir)
        assert report["dataset"] == "MIMIC-IV-ED-Demo-v2.2"
        assert report["is_demo_not_full_dataset"] is True

    def test_vitalsign_vital_columns_are_excluded_not_marked_safe(self, fake_mimic_dir):
        """
        The specific regression guard for the bug described in this file's
        module docstring. Every vitalsign.* column -- including the ones
        that share a name with a triage.* column -- must be classified as
        EXCLUDED_FROM_TRIAGE_INPUT, never TRIAGE_TIME_SAFE.
        """
        report = audit_mimic_demo(fake_mimic_dir)
        fc = report["field_classification"]
        for col in ["temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain"]:
            key = f"vitalsign.{col}"
            assert key in fc, f"{key} missing from field_classification"
            assert "EXCLUDED_FROM_TRIAGE_INPUT" in fc[key], (
                f"{key} was classified as {fc[key]!r}, expected "
                f"EXCLUDED_FROM_TRIAGE_INPUT -- this is the exact bug this "
                f"test exists to catch."
            )

    def test_triage_vital_columns_are_marked_safe(self, fake_mimic_dir):
        """The same column names in triage.csv (the correct triage-time
        source) must still be classified as TRIAGE_TIME_SAFE."""
        report = audit_mimic_demo(fake_mimic_dir)
        fc = report["field_classification"]
        for col in ["temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp", "pain"]:
            key = f"triage.{col}"
            assert fc[key] == "TRIAGE_TIME_SAFE"

    def test_acuity_and_disposition_marked_retrospective(self, fake_mimic_dir):
        report = audit_mimic_demo(fake_mimic_dir)
        fc = report["field_classification"]
        assert fc["triage.acuity"] == "RETROSPECTIVE_OR_LEAKAGE"
        assert fc["edstays.disposition"] == "RETROSPECTIVE_OR_LEAKAGE"
        assert fc["edstays.outtime"] == "RETROSPECTIVE_OR_LEAKAGE"

    def test_manchester_mapping_explicitly_not_implemented(self, fake_mimic_dir):
        report = audit_mimic_demo(fake_mimic_dir)
        assert "NOT_IMPLEMENTED" in report["manchester_mapping"]

    def test_clinical_use_explicitly_blocked(self, fake_mimic_dir):
        report = audit_mimic_demo(fake_mimic_dir)
        assert report["clinical_use"] == "NOT_FOR_CLINICAL_USE"
