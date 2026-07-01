"""Full-MIMIC adapter validation against the documented schema, using SYNTHETIC
fixtures only (no credentialed data). Confirms the adapter reads the real
.csv.gz schema, labels full cases correctly, and degrades gracefully if the
edstays demographic columns are absent."""
import csv
import gzip
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import settings
from app.data_pipeline.mimic_adapter import (
    validate_mimic_tables, load_mimic_table, dataframe_to_cases,
    SOURCE_DATASET_LABEL_FULL, SOURCE_DATASET_LABEL,
)
import app.data_pipeline.mimic_full_loader as full


def _write_gz(path, header, rows):
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f); w.writerow(header)
        for r in rows:
            w.writerow(r)


def _make_full_fixture(tmp_path, with_demographics=True):
    ed = tmp_path / "ed"; ed.mkdir()
    edstays_header = ["subject_id", "hadm_id", "stay_id", "intime", "outtime"]
    edstays_row = ["10000001", "", "30000001", "2180-01-01 10:00:00", "2180-01-01 14:00:00"]
    if with_demographics:
        edstays_header += ["gender", "race", "arrival_transport", "disposition"]
        edstays_row += ["F", "WHITE", "AMBULANCE", "HOME"]
    _write_gz(ed / "edstays.csv.gz", edstays_header, [edstays_row])
    _write_gz(ed / "triage.csv.gz",
              ["subject_id", "stay_id", "temperature", "heartrate", "resprate",
               "o2sat", "sbp", "dbp", "pain", "acuity", "chiefcomplaint"],
              [["10000001", "30000001", "98.6", "88", "18", "98", "120", "80", "3", "2", "CHEST PAIN"]])
    _write_gz(ed / "vitalsign.csv.gz",
              ["subject_id", "stay_id", "charttime", "temperature", "heartrate",
               "resprate", "o2sat", "sbp", "dbp", "rhythm", "pain"],
              [["10000001", "30000001", "2180-01-01 11:00:00", "98.6", "85", "17", "99", "118", "78", "Sinus", "2"]])
    _write_gz(ed / "diagnosis.csv.gz",
              ["subject_id", "stay_id", "seq_num", "icd_code", "icd_version", "icd_title"],
              [["10000001", "30000001", "1", "R079", "10", "Chest pain, unspecified"]])
    _write_gz(ed / "medrecon.csv.gz",
              ["subject_id", "stay_id", "charttime", "name", "gsn", "ndc", "etc_rn", "etccode", "etcdescription"],
              [["10000001", "30000001", "2180-01-01 10:30:00", "Aspirin", "004640", "00536338101", "1", "00000", "Analgesics"]])
    _write_gz(ed / "pyxis.csv.gz",
              ["subject_id", "stay_id", "charttime", "med_rn", "name", "gsn_rn", "gsn"],
              [["10000001", "30000001", "2180-01-01 11:30:00", "1", "Aspirin 325mg", "1", "004640"]])
    return ed


def test_adapter_reads_full_schema(tmp_path):
    ed = _make_full_fixture(tmp_path, with_demographics=True)
    report = validate_mimic_tables(ed)
    assert report["all_required_present"] is True
    assert report["tables"]["triage"]["status"] == "OK"
    assert report["tables"]["edstays"]["status"] == "OK"


def test_full_cases_labelled_full_not_demo(tmp_path):
    ed = _make_full_fixture(tmp_path, with_demographics=True)
    dfs = {t: load_mimic_table(ed, t)
           for t in ["edstays", "triage", "vitalsign", "diagnosis", "medrecon", "pyxis"]}
    cases = dataframe_to_cases(
        dfs["edstays"], dfs["triage"], dfs["vitalsign"], dfs["diagnosis"],
        dfs["medrecon"], dfs["pyxis"], source_dataset_label=SOURCE_DATASET_LABEL_FULL)
    assert len(cases) == 1
    assert cases[0].source_dataset == "MIMIC-IV-ED-Full-v2.2"
    # vitals parsed correctly
    assert cases[0].triage.heartrate == 88.0
    assert cases[0].triage.acuity == 2.0


def test_default_label_is_full_mimic(tmp_path):
    # Full-MIMIC-only parser default: no implicit public-demo label.
    ed = _make_full_fixture(tmp_path, with_demographics=True)
    dfs = {t: load_mimic_table(ed, t)
           for t in ["edstays", "triage", "vitalsign", "diagnosis", "medrecon", "pyxis"]}
    cases = dataframe_to_cases(dfs["edstays"], dfs["triage"], dfs["vitalsign"],
                               dfs["diagnosis"], dfs["medrecon"], dfs["pyxis"])
    assert cases[0].source_dataset == SOURCE_DATASET_LABEL_FULL
    assert SOURCE_DATASET_LABEL == SOURCE_DATASET_LABEL_FULL


def test_graceful_degradation_without_demographics(tmp_path):
    # edstays with only the 5 README-documented columns: tolerated, not fatal
    ed = _make_full_fixture(tmp_path, with_demographics=False)
    report = validate_mimic_tables(ed)
    assert report["all_required_present"] is True   # subject_id/stay_id present
    eds = report["tables"]["edstays"]
    assert eds["status"] == "TOLERABLE_DIFF"
    assert set(eds["missing_columns"]) == {"gender", "race", "arrival_transport", "disposition"}
    assert eds["required_missing"] == []
    # cases still build; demographics are None
    dfs = {t: load_mimic_table(ed, t)
           for t in ["edstays", "triage", "vitalsign", "diagnosis", "medrecon", "pyxis"]}
    cases = dataframe_to_cases(dfs["edstays"], dfs["triage"], dfs["vitalsign"],
                               dfs["diagnosis"], dfs["medrecon"], dfs["pyxis"])
    assert cases[0].edstay.gender is None


def test_missing_required_column_flagged(tmp_path):
    # triage without acuity (a required col) -> REQUIRED_MISSING, all_required False
    ed = _make_full_fixture(tmp_path, with_demographics=True)
    _write_gz(ed / "triage.csv.gz",
              ["subject_id", "stay_id", "temperature", "heartrate", "chiefcomplaint"],
              [["10000001", "30000001", "98.6", "88", "CHEST PAIN"]])
    report = validate_mimic_tables(ed)
    assert report["all_required_present"] is False
    assert report["tables"]["triage"]["status"] == "REQUIRED_MISSING"
    assert "acuity" in report["tables"]["triage"]["required_missing"]


def test_full_loader_through_guards(tmp_path, monkeypatch):
    ed = _make_full_fixture(tmp_path, with_demographics=True)
    monkeypatch.setattr(settings, "mimic_full_ed_dir", ed, raising=False)
    monkeypatch.setattr(full, "credentialed_data_access_allowed", lambda: True)
    cases = full.load_mimic_full_cases()
    assert len(cases) == 1
    assert cases[0].source_dataset == "MIMIC-IV-ED-Full-v2.2"
    schema = full.validate_full_mimic_schema()
    assert schema["all_required_present"] is True
