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


def _make_large_core_fixture(tmp_path, n_rows=301):
    ed = tmp_path / "ed_large"; ed.mkdir()
    edstay_rows = []
    triage_rows = []
    for i in range(n_rows):
        subject_id = 10000000 + i
        stay_id = 30000000 + i
        edstay_rows.append([
            str(subject_id), "", str(stay_id), "2180-01-01 10:00:00",
            "2180-01-01 14:00:00", "F", "WHITE", "WALK IN", "HOME",
        ])
        triage_rows.append([
            str(subject_id), str(stay_id), "98.6", "80", "18", "98",
            "120", "80", "2", "3", "SORE THROAT",
        ])
    _write_gz(ed / "edstays.csv.gz",
              ["subject_id", "hadm_id", "stay_id", "intime", "outtime",
               "gender", "race", "arrival_transport", "disposition"],
              edstay_rows)
    _write_gz(ed / "triage.csv.gz",
              ["subject_id", "stay_id", "temperature", "heartrate", "resprate",
               "o2sat", "sbp", "dbp", "pain", "acuity", "chiefcomplaint"],
              triage_rows)
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


def test_triage_time_page_loader_streams_matching_triage_rows(tmp_path, monkeypatch):
    ed = tmp_path / "ed_streaming"
    ed.mkdir()
    _write_gz(
        ed / "edstays.csv.gz",
        ["subject_id", "hadm_id", "stay_id", "intime", "outtime",
         "gender", "race", "arrival_transport", "disposition"],
        [
            ["10000001", "", "30000001", "2180-01-01 10:00:00",
             "2180-01-01 14:00:00", "F", "WHITE", "WALK IN", "HOME"],
            ["10000002", "", "30000002", "2180-01-01 10:05:00",
             "2180-01-01 14:05:00", "M", "WHITE", "AMBULANCE", "HOME"],
        ],
    )
    _write_gz(
        ed / "triage.csv.gz",
        ["subject_id", "stay_id", "temperature", "heartrate", "resprate",
         "o2sat", "sbp", "dbp", "pain", "acuity", "chiefcomplaint"],
        [
            ["19999999", "39999999", "98.6", "75", "18", "98",
             "120", "80", "1", "5", "NOT SELECTED"],
            ["10000002", "30000002", "99.0", "90", "20", "97",
             "128", "82", "4", "2", "HEADACHE"],
            ["10000001", "30000001", "98.1", "80", "18", "99",
             "118", "78", "2", "3", "SORE THROAT"],
        ],
    )
    monkeypatch.setattr(settings, "mimic_full_ed_dir", ed, raising=False)
    monkeypatch.setattr(full, "credentialed_data_access_allowed", lambda: True)

    import app.data_pipeline.mimic_adapter as adapter

    real_load_mimic_table = adapter.load_mimic_table

    def guarded_load_mimic_table(path, table_name, **kwargs):
        if table_name == "triage":
            raise AssertionError("paged serving must not load the full triage table")
        return real_load_mimic_table(path, table_name, **kwargs)

    monkeypatch.setattr(adapter, "load_mimic_table", guarded_load_mimic_table)

    cases = full.load_mimic_full_cases_triage_time(n=2)

    assert [c.stay_id for c in cases] == [30000001, 30000002]
    assert [c.triage.chiefcomplaint for c in cases] == ["SORE THROAT", "HEADACHE"]


def test_public_mimic_sample_loader_is_view_only(tmp_path, monkeypatch):
    ed = _make_full_fixture(tmp_path, with_demographics=True)
    monkeypatch.setenv("ALLOW_MIMIC_ED_PUBLIC_SAMPLE_VIEW", "true")
    monkeypatch.setenv("MIMIC_ED_PUBLIC_SAMPLE_DIR", str(ed))
    monkeypatch.delenv("LOCAL_CREDENTIALED_RESEARCH", raising=False)
    monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)

    from app.data_pipeline.mimic_public_sample_loader import load_public_mimic_ed_cases
    cases = load_public_mimic_ed_cases()

    assert len(cases) == 1
    assert cases[0]["source_dataset"] == "MIMIC-IV-ED-Demo-v2.2"
    assert cases[0]["public_mimic_demo"] is True
    assert "view-only" in cases[0]["demo_data_notice"].lower()


def test_public_mimic_sample_loader_refuses_full_sized_mimic_shape(tmp_path, monkeypatch):
    ed = _make_large_core_fixture(tmp_path, n_rows=301)
    monkeypatch.setenv("ALLOW_MIMIC_ED_PUBLIC_SAMPLE_VIEW", "true")
    monkeypatch.setenv("MIMIC_ED_PUBLIC_SAMPLE_DIR", str(ed))
    monkeypatch.delenv("LOCAL_CREDENTIALED_RESEARCH", raising=False)
    monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)

    from app.data_pipeline.mimic_public_sample_loader import load_public_mimic_ed_cases

    with pytest.raises(ValueError, match="more rows than expected"):
        load_public_mimic_ed_cases(n=3)


def test_public_mimic_sample_resolver_opt_in_does_not_enable_prediction(tmp_path, monkeypatch):
    ed = _make_full_fixture(tmp_path, with_demographics=True)
    monkeypatch.setenv("ALLOW_MIMIC_ED_PUBLIC_SAMPLE_VIEW", "true")
    monkeypatch.setenv("MIMIC_ED_PUBLIC_SAMPLE_DIR", str(ed))
    monkeypatch.delenv("LOCAL_CREDENTIALED_RESEARCH", raising=False)
    monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
    monkeypatch.delenv("MIMIC_FULL_MODEL_PATH", raising=False)

    import app.api.case_resolver as cr
    cr._CASE_CACHE.clear()
    cr._PARTIAL_CASE_CACHE.clear()
    cr._COUNT_CACHE.clear()

    rows = cr.list_cases(limit=5)
    assert len(rows) == 1
    assert rows[0].source_dataset == "MIMIC-IV-ED-Demo-v2.2"
    assert cr.count_cases() == 1

    from app.agents.ml_prediction_agent import run_ml_prediction
    from app.schemas.internal import EDTriageCase
    result = run_ml_prediction(EDTriageCase(**rows[0].case).to_triage_time_input())
    assert result.prediction_available is False
    assert result.predicted_mimic_acuity is None
