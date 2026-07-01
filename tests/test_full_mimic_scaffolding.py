"""v13 full-MIMIC scaffolding: safety guards reject unsafe environments; report
functions are correct on SYNTHETIC labels; artefact compatibility check works.
No credentialed data is used anywhere."""
from pathlib import Path

import pytest

from ml_training.full_mimic._safety import (
    require_safe_environment, assert_no_raw_rows, UnsafeEnvironmentError, REPO_ROOT,
    assert_not_synthetic_demo_path,
)
from ml_training.full_mimic.reports import (
    under_over_triage, high_acuity_recall_report, calibration_report,
    ordinal_acuity_metrics,
)


class TestSafetyGuards:
    def test_refuses_without_env(self, monkeypatch):
        monkeypatch.delenv("MIMIC_FULL_ED_DIR", raising=False)
        with pytest.raises(UnsafeEnvironmentError):
            require_safe_environment()

    def test_refuses_without_credentialed_profile(self, monkeypatch, tmp_path):
        ed = tmp_path / "ed"; ed.mkdir()
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(ed))
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("LOCAL_CREDENTIALED_RESEARCH", raising=False)
        with pytest.raises(UnsafeEnvironmentError) as e:
            require_safe_environment(output_dir=str(tmp_path / "out"))
        assert "LOCAL_CREDENTIALED_RESEARCH" in str(e.value)

    def test_refuses_repo_local_data_path(self, monkeypatch, tmp_path):
        inside = REPO_ROOT / "data" / "raw"
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(inside))
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        with pytest.raises(UnsafeEnvironmentError) as e:
            require_safe_environment(output_dir=str(tmp_path / "out"))
        assert "inside the repository" in str(e.value)

    def test_refuses_repo_local_output(self, monkeypatch, tmp_path):
        ed = tmp_path / "ed"; ed.mkdir()
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(ed))
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        inside_out = REPO_ROOT / "ml_training" / "full_mimic" / "out"
        with pytest.raises(UnsafeEnvironmentError):
            require_safe_environment(output_dir=str(inside_out))

    def test_accepts_safe_paths(self, monkeypatch, tmp_path):
        ed = tmp_path / "ed"; ed.mkdir()
        out = tmp_path / "out"
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(ed))
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        paths = require_safe_environment(output_dir=str(out))
        assert paths["ed_dir"] == ed.resolve()
        assert paths["output_dir"] == out.resolve()

    def test_accepts_local_credentialed_research_profile(self, monkeypatch, tmp_path):
        ed = tmp_path / "ed"; ed.mkdir()
        out = tmp_path / "out"
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(ed))
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        paths = require_safe_environment(output_dir=str(out))
        assert paths["ed_dir"] == ed.resolve()
        assert paths["output_dir"] == out.resolve()

    def test_assert_no_raw_rows_blocks_patient_rows(self):
        with pytest.raises(UnsafeEnvironmentError):
            assert_no_raw_rows([{"subject_id": 1, "stay_id": 2, "hr": 80}])

    def test_assert_no_raw_rows_allows_aggregate(self):
        assert_no_raw_rows({"n": 5, "accuracy": 0.5})  # no raise

    def test_refuses_bundled_demo_or_fixture_training_paths(self):
        for path in (
            REPO_ROOT / "data" / "demo" / "azure_supervisor_demo_cases.jsonl",
            REPO_ROOT / "tests" / "fixtures" / "sample_mimic_full_cases.jsonl",
            REPO_ROOT.parent / "synthetic_mimic_fixture" / "ed",
        ):
            with pytest.raises(UnsafeEnvironmentError):
                assert_not_synthetic_demo_path(path)


class TestReports:
    def test_under_over_triage(self):
        # truth vs pred (1=most urgent). pred>true = under-triage.
        r = under_over_triage([1, 1, 2, 3], [2, 1, 2, 1])
        assert r["under_triage_count"] == 1     # 1->2
        assert r["over_triage_count"] == 1      # 3->1
        assert r["exact_count"] == 2
        assert r["under_triage_rate"] == 0.25
        assert r["severe_under_triage_count"] == 0
        assert r["within_1_acuity_level_accuracy"] == 0.75

    def test_ordinal_acuity_metrics(self):
        r = ordinal_acuity_metrics([1, 2, 4, 5], [1, 3, 2, 5])
        assert r["mae"] == 0.75
        assert r["within_1_acuity_level_accuracy"] == 0.75
        assert "quadratic_weighted_kappa" in r

    def test_high_acuity_recall(self):
        r = high_acuity_recall_report([1, 2, 3, 4], [1, 3, 3, 4])
        assert r["n_high_acuity"] == 2          # the 1 and 2
        assert r["caught_as_high_acuity"] == 1  # 1 caught, 2 missed
        assert r["recall"] == 0.5

    def test_calibration_brier(self):
        r = calibration_report([1, 2], [[0.9, 0.1], [0.1, 0.9]], [1, 2])
        assert 0.0 <= r["brier_mean"] <= 1.0


class TestLabelRemapClassifier:
    def test_predict_exposes_original_acuity_labels(self):
        import numpy as np
        from sklearn.base import BaseEstimator, ClassifierMixin
        from ml_training.full_mimic.label_remap import LabelRemapClassifier

        class AlwaysEncodedTwo(BaseEstimator, ClassifierMixin):
            def fit(self, X, y):
                self.seen_labels_ = sorted(set(y))
                return self

            def predict(self, X):
                return np.full(len(X), 2)

            def predict_proba(self, X):
                out = np.zeros((len(X), 3))
                out[:, 2] = 1.0
                return out

        wrapped = LabelRemapClassifier(AlwaysEncodedTwo())
        wrapped.fit(np.zeros((3, 1)), np.array([1, 3, 5]))

        assert wrapped.estimator_.seen_labels_ == [0, 1, 2]
        assert wrapped.predict(np.zeros((2, 1))).tolist() == [5, 5]
        assert wrapped.classes_.tolist() == [1, 3, 5]

    def test_empty_inputs_safe(self):
        assert under_over_triage([], [])["under_triage_rate"] is None
        assert high_acuity_recall_report([], [])["recall"] is None


class TestArtifactCompatibility:
    def test_detects_version(self, tmp_path):
        import joblib
        import sklearn
        from ml_training.full_mimic.check_artifact_compatibility import check_artifact
        art = tmp_path / "m.joblib"
        joblib.dump({"model": object(), "feature_names": ["a", "b"],
                     "sklearn_version": sklearn.__version__}, art)
        r = check_artifact(art)
        assert r["compatible"] is True
        assert r["feature_count"] == 2

    def test_flags_missing_version(self, tmp_path):
        import joblib
        from ml_training.full_mimic.check_artifact_compatibility import check_artifact
        art = tmp_path / "m.joblib"
        joblib.dump({"model": object()}, art)
        r = check_artifact(art)
        assert r["compatible"] is False
        assert "version" in r["reason"]

    def test_flags_mismatch(self, tmp_path):
        import joblib
        from ml_training.full_mimic.check_artifact_compatibility import check_artifact
        art = tmp_path / "m.joblib"
        joblib.dump({"model": object(), "sklearn_version": "0.1"}, art)
        r = check_artifact(art)
        assert r["compatible"] is False
        assert "mismatch" in r["reason"]


def test_disabled_download_script_refuses():
    # The old unsafe downloader must refuse to run.
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "scripts/load_full_mimic_ed.py"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert result.returncode == 2
    assert "DISABLED" in result.stderr


class TestScaffoldingRunsOnSyntheticData:
    """The full-MIMIC scripts must execute on SYNTHETIC MIMIC-shaped fixtures
    (no credentialed data). Verifies build_feature_frame and the script entry
    points work and produce aggregate-only artefacts."""

    def _make_fixture(self, tmp_path, n=30):
        import csv
        import gzip
        ed = tmp_path / "ed"; ed.mkdir()
        out = tmp_path / "out"

        def wz(name, header, rows):
            with gzip.open(ed / name, "wt", newline="") as f:
                w = csv.writer(f); w.writerow(header)
                for r in rows:
                    w.writerow(r)

        eds, tri = [], []
        for i in range(n):
            sid = 30000000 + i
            subj = 10000000 + i
            acuity = (i % 5) + 1  # spread across 1..5
            eds.append([subj, "", sid, "2180-01-01 10:00:00", "2180-01-01 14:00:00",
                        "F" if i % 2 else "M", "WHITE", "AMBULANCE", "HOME"])
            tri.append([subj, sid, "98.6", 80 + i, 18, 98, 120, 80, str(i % 10),
                        acuity, "CHEST PAIN"])
        wz("edstays.csv.gz", ["subject_id", "hadm_id", "stay_id", "intime", "outtime",
                              "gender", "race", "arrival_transport", "disposition"], eds)
        wz("triage.csv.gz", ["subject_id", "stay_id", "temperature", "heartrate",
                             "resprate", "o2sat", "sbp", "dbp", "pain", "acuity",
                             "chiefcomplaint"], tri)
        for t, h in [("vitalsign.csv.gz", ["subject_id", "stay_id", "charttime",
                       "temperature", "heartrate", "resprate", "o2sat", "sbp", "dbp",
                       "rhythm", "pain"]),
                     ("diagnosis.csv.gz", ["subject_id", "stay_id", "seq_num",
                       "icd_code", "icd_version", "icd_title"]),
                     ("medrecon.csv.gz", ["subject_id", "stay_id", "charttime", "name",
                       "gsn", "ndc", "etc_rn", "etccode", "etcdescription"]),
                     ("pyxis.csv.gz", ["subject_id", "stay_id", "charttime", "med_rn",
                       "name", "gsn_rn", "gsn"])]:
            wz(t, h, [])
        return ed, out

    def test_build_feature_frame_on_synthetic(self, tmp_path, monkeypatch):
        ed, out = self._make_fixture(tmp_path)
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(ed))
        monkeypatch.setenv("MIMIC_FULL_OUTPUT_DIR", str(out))
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        from app.config import settings
        monkeypatch.setattr(settings, "mimic_full_ed_dir", ed, raising=False)
        from app.data_pipeline.mimic_full_loader import load_mimic_full_cases
        from ml_training.feature_engineering import build_feature_frame
        cases = load_mimic_full_cases()
        X, y, names = build_feature_frame(cases)
        assert X.shape[0] == 30
        assert len(names) > 0
        assert sum(1 for v in y if v is not None) == 30

    def test_build_features_script_writes_aggregate_only(self, tmp_path, monkeypatch):
        ed, out = self._make_fixture(tmp_path)
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(ed))
        monkeypatch.setenv("MIMIC_FULL_OUTPUT_DIR", str(out))
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        from app.config import settings
        monkeypatch.setattr(settings, "mimic_full_ed_dir", ed, raising=False)
        import importlib
        import ml_training.full_mimic.build_features as bf
        importlib.reload(bf)
        assert bf.main() == 0
        import json
        summary = json.loads((out / "full_mimic_feature_summary.json").read_text())
        # aggregate only: counts, no raw rows / identifiers
        assert summary["n_cases"] == 30
        assert "subject_id" not in json.dumps(summary)
        assert "stay_id" not in json.dumps(summary)

    def test_train_script_is_deprecated(self, tmp_path, monkeypatch):
        ed, out = self._make_fixture(tmp_path, n=40)
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(ed))
        monkeypatch.setenv("MIMIC_FULL_OUTPUT_DIR", str(out))
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        from app.config import settings
        monkeypatch.setattr(settings, "mimic_full_ed_dir", ed, raising=False)
        import importlib
        import ml_training.full_mimic.train as tr
        importlib.reload(tr)
        assert tr.main() == 2
        assert not (out / "mimic_full_acuity_rf.joblib").exists()


class TestCompareModelsSafetyFirst:
    """compare_models.py must run end-to-end on synthetic MIMIC-shaped data (no
    credentialed data), select by triage-safety metrics, and write an aggregate
    comparison + a model artefact with no raw rows."""

    def _synthetic_cases(self, n=240):
        import random
        rng = random.Random(7)
        cases = []
        for i in range(n):
            # Make acuity learnable from vitals so models actually differ in skill:
            # very abnormal vitals => high acuity (1/2), normal => low (4/5).
            hr = rng.choice([45, 60, 80, 110, 140, 180])
            o2 = rng.choice([78, 88, 94, 97, 99])
            sbp = rng.choice([70, 85, 110, 130, 160])
            danger = (hr >= 140) + (o2 <= 88) + (sbp <= 85)
            acuity = 1 if danger >= 2 else 2 if danger == 1 else rng.choice([3, 4, 5])
            cases.append({
                "source_dataset": "MIMIC-IV-ED-Full-v2.2",
                "stay_id": 30000000 + i, "subject_id": 10000000 + i,
                "edstay": {"subject_id": 10000000 + i, "stay_id": 30000000 + i,
                           "gender": rng.choice(["M", "F"]),
                           "arrival_transport": rng.choice(["AMBULANCE", "WALK IN"]),
                           "disposition": "HOME"},
                "triage": {"subject_id": 10000000 + i, "stay_id": 30000000 + i,
                           "heartrate": float(hr), "o2sat": float(o2), "sbp": float(sbp),
                           "resprate": 18.0, "dbp": 75.0, "temperature": 98.6,
                           "pain": "5", "chiefcomplaint": "CHEST PAIN",
                           "acuity": acuity},
                "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
            })
        return cases

    def test_compare_models_runs_and_selects(self, monkeypatch, tmp_path):
        import json
        from ml_training.full_mimic import compare_models

        ed = tmp_path / "ed"; ed.mkdir()
        out = tmp_path / "out"
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(ed))
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        monkeypatch.setenv("MIMIC_FULL_OUTPUT_DIR", str(out))
        monkeypatch.delenv("MIMIC_COMPARE_CANDIDATES", raising=False)
        cases = self._synthetic_cases()
        monkeypatch.setattr(compare_models, "load_mimic_full_cases", lambda: cases,
                            raising=False)
        import app.data_pipeline.mimic_full_loader as _mfl
        monkeypatch.setattr(_mfl, "load_mimic_full_cases", lambda: cases)

        # --quick-test keeps estimators small so this finishes fast (no hang).
        rc = compare_models.main(["--quick-test"])
        assert rc == 0
        comp = json.loads((out / "full_mimic_model_comparison.json").read_text())
        # selection is safety-first, not accuracy
        assert "safety-first" in comp["selection_criterion"]
        assert comp["quick_test_mode"] is True
        assert comp["candidate_mode"] == "basic"
        assert comp["candidate_names_requested"] == ["logistic_regression", "random_forest"]
        assert comp["dataset_source"] == "MIMIC-IV-ED-Full-v2.2"
        assert comp["synthetic_data_used"] is False
        assert comp["demo_fixture_used"] is False
        assert comp["test_fixture_used"] is False
        assert comp["patient_level_split"] is True
        assert comp["patient_overlap_train_test"] == 0
        assert comp["test_set_used_for_model_selection"] is False
        assert comp["preprocessing_inside_pipeline"] is True
        assert comp["leakage_audit_passed"] is True
        assert comp["synthetic_audit_passed"] is True
        assert comp["selected_model"] in {c["model_name"] for c in comp["candidates"]}
        # the selected model must be the top of the safety ordering
        assert comp["candidates"][0]["model_name"] == comp["selected_model"]
        assert comp.get("selection_rationale")
        # logistic regression is a candidate
        names = {c["model_name"] for c in comp["candidates"]}
        assert "logistic_regression" in names
        assert "random_forest" in names
        assert names <= {"logistic_regression", "random_forest"}
        # every candidate has the full metric set
        for c in comp["candidates"]:
            assert "macro_f1" in c and "weighted_f1" in c
            assert "per_class" in c and c["per_class"]
            assert "confusion_matrix" in c and isinstance(c["confusion_matrix"], list)
            assert "train_seconds" in c and "infer_seconds" in c
        # CSV + model card written
        assert (out / "full_mimic_model_comparison.csv").exists()
        assert (out / "mimic_full_model_card.json").exists()
        assert (out / "mimic_full_dataset_card.json").exists()
        assert (out / "mimic_full_feature_schema.json").exists()
        assert (out / "mimic_full_training_provenance.json").exists()
        assert (out / "mimic_full_model_sha256.txt").exists()
        assert (out / "full_mimic_confusion_matrix.json").exists()
        assert (out / "full_mimic_calibration_report.json").exists()
        assert (out / "full_mimic_under_over_triage_report.json").exists()
        assert (out / "full_mimic_subgroup_metrics.json").exists()
        # corrected evaluation methodology is reflected in the report
        assert comp["split_kind"] in {"temporal", "patient_grouped"}
        assert "untouched_test_metrics" in comp
        utm = comp["untouched_test_metrics"]
        assert "auroc_pr_auc" in utm and "accuracy_95ci" in utm
        assert "over_triage_specificity" in utm and "subgroups_by_sex" in utm
        assert comp["n_train"] and comp["n_val"] and comp["n_test"]
        # selection considered the over-triage constraint
        assert "over_triage_constraint" in comp["selection_rationale"].lower() or \
               "over-triage" in comp["selection_rationale"].lower()
        card = json.loads((out / "mimic_full_model_card.json").read_text())
        assert card["selected_by"].startswith("triage_safety_metrics")
        assert "acuity" in card["excluded_leakage_features"]
        provenance = json.loads((out / "mimic_full_training_provenance.json").read_text())
        assert provenance["synthetic_data_used"] is False
        assert provenance["demo_fixture_used"] is False
        assert provenance["test_fixture_used"] is False
        assert provenance["patient_overlap_train_test"] == 0
        assert provenance["final_test_evaluation_once"] is True
        # artefact written, no raw rows anywhere
        assert (out / "mimic_full_acuity_selected.joblib").exists()
        assert "subject_id" not in json.dumps(comp)
        assert "stay_id" not in json.dumps(comp)

    def test_compare_models_refuses_without_safe_env(self, monkeypatch):
        from ml_training.full_mimic import compare_models
        monkeypatch.delenv("MIMIC_FULL_ED_DIR", raising=False)
        assert compare_models.main(["--quick-test"]) == 2

    def test_compare_models_rejects_unknown_candidate(self, monkeypatch, tmp_path):
        from ml_training.full_mimic import compare_models
        ed = tmp_path / "ed"; ed.mkdir()
        monkeypatch.setenv("MIMIC_FULL_ED_DIR", str(ed))
        monkeypatch.setenv("PATIENT_DATA_MODE", "true")
        assert compare_models.main(["--quick-test", "--candidates", "not_a_model"]) == 2
