"""Runtime and inference regression checks for the disclosed curated demo."""
from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.constants import DATASET_SHA256, DATASET_SOURCE, DEMO_COHORT_SHA256, DEMO_COHORT_CATEGORY_COUNTS, DEMO_COHORT_MATCHED_ROWS, MODEL_SHA256
from app.data_pipeline.uhl_demo_repository import UhlDemoCaseRepository
from app.data_pipeline.uhl_repository import UhlCaseRepository, UhlDatasetContractError, get_case_repository
from app.schemas.internal import EDTriageCase


def test_all_100_preserve_source_identity_and_reproduce_real_model_agreement():
    from app.agents.ml_prediction_agent import run_ml_prediction
    from app.agents.orchestrator import run_workflow
    from app.rules.provisional_mts_ruleset import register_provisional_ruleset
    from app.security.redaction import pseudonymous_case_uid

    register_provisional_ruleset()
    manifest = json.loads(settings.serving_data_path.read_text())
    assert hashlib.sha256(settings.serving_data_path.read_bytes()).hexdigest() == DEMO_COHORT_SHA256
    assert manifest["training_dataset_sha256"] == DATASET_SHA256
    assert manifest["model_sha256"] == MODEL_SHA256
    assert manifest["source_rows_scanned"] == 777176
    assert manifest["selection"]["official_mts"] is False
    repo = get_case_repository(settings)
    counts, matched_count = Counter(), 0
    for source in manifest["cases"]:
        uid = pseudonymous_case_uid(DATASET_SOURCE, source["row_number"])
        payload = repo.get_case(uid)
        assert payload is not None
        case = EDTriageCase(**payload)
        ti = case.to_triage_time_input()
        assert 16 <= ti.age < 75
        ml = run_ml_prediction(ti)
        expected = source["selection_evidence"]
        assert ml.prediction_available
        assert ml.predicted_mimic_acuity == expected["ml_acuity_before_override"]
        assert "source_estimated_acuity" not in ti.model_dump()
        assert ml.argmax_acuity == ml.predicted_mimic_acuity
        assert ml.decision_rule_changed_prediction is False
        assert expected["assigned_class_probability"] >= .5
        assert ml.mimic_acuity_probabilities[str(ml.predicted_mimic_acuity)] == pytest.approx(
            expected["assigned_class_probability"], abs=1e-10)
        result = run_workflow(case)
        final = result.final_acuity_assessment.final_acuity
        matched = bool(result.discriminator_evidence["matched"])
        assert final == expected["final_acuity"] == expected["source_estimated_acuity"]
        assert final <= ml.predicted_mimic_acuity
        assert matched is expected["has_matched_discriminator"]
        assert result.final_acuity_assessment.override_applied is expected["override_applied"]
        assert result.final_acuity_assessment.override_applied is (final < ml.predicted_mimic_acuity)
        if not matched:
            assert final == ml.predicted_mimic_acuity
        matched_count += int(matched)
        counts[final] += 1
    assert counts == Counter(DEMO_COHORT_CATEGORY_COUNTS)
    assert matched_count == DEMO_COHORT_MATCHED_ROWS == 60
    assert counts[5] == 5


def test_runtime_never_reads_full_csv_or_builds_sqlite(tmp_path, monkeypatch):
    config = Settings(uhl_data_path=tmp_path / "absent.csv.gz", uhl_case_cache_path=tmp_path / "cache.sqlite3")
    monkeypatch.setattr(UhlCaseRepository, "ensure_cache", lambda _: pytest.fail("Full cache was accessed"))
    repository = get_case_repository(config)
    assert isinstance(repository, UhlDemoCaseRepository)
    assert repository.status()["source_rows"] == 100
    assert len(repository.list_cases()["cases"]) == 100
    repository.clear_derived_cache()
    assert len(repository.list_cases()["cases"]) == 100
    assert not config.case_cache_path.exists()
    assert not config.uhl_data_path.exists()


def test_pagination_search_and_returned_data_cannot_mutate_cohort():
    repository = get_case_repository(settings)
    all_rows = repository.list_cases()["cases"]
    seen = []
    offset = 0
    while True:
        page = repository.list_cases(offset=offset, limit=17)
        seen.extend(row["case_uid"] for row in page["cases"])
        offset = page["pagination"]["next_offset"]
        if offset is None:
            break
    assert len(seen) == len(set(seen)) == 100
    assert set(seen) == {row["case_uid"] for row in all_rows}
    last = all_rows[-1]
    original_heart_rate = last["triage"]["heartrate"]
    assert repository.list_cases(query=last["case_uid"])["cases"] == [last]
    repository.get_case(last["case_uid"])["triage"]["heartrate"] = -1
    all_rows[-1]["triage"]["heartrate"] = -2
    assert repository.get_case(last["case_uid"])["triage"]["heartrate"] == original_heart_rate


@pytest.mark.parametrize("fault", ["digest", "root", "duplicate", "age", "probability", "distribution", "label", "matched_count", "downgrade", "missing"])
def test_invalid_cohort_fails_closed(tmp_path, monkeypatch, fault):
    import app.constants as constants
    content = json.loads(settings.serving_data_path.read_text())
    if fault == "root":
        content = []
    elif fault == "duplicate":
        content["cases"][1] = content["cases"][0]
    elif fault == "age":
        content["cases"][0]["age"] = 15
    elif fault == "probability":
        content["cases"][0]["selection_evidence"]["assigned_class_probability"] = float("nan")
    elif fault == "distribution":
        row = next(r for r in content["cases"] if not r["selection_evidence"]["has_matched_discriminator"])
        item = row["selection_evidence"]
        acuity = 4 if item["final_acuity"] == 3 else 3
        item["ml_acuity_before_override"] = item["final_acuity"] = item["source_estimated_acuity"] = acuity
    elif fault == "label":
        item = content["cases"][0]["selection_evidence"]
        item["source_estimated_acuity"] = item["ml_acuity_before_override"] % 5 + 1
    elif fault == "matched_count":
        row = next(r for r in content["cases"] if r["selection_evidence"]["final_acuity"] == 2)
        row.update(temperature=98.6, heartrate=80, resprate=16, o2sat=98, sbp=120, dbp=80, pain=2)
        row["selection_evidence"].update(ml_acuity_before_override=2, has_matched_discriminator=False,
                                          safety_floor_acuity=None, override_applied=False)
    elif fault == "downgrade":
        row = next(r for r in content["cases"] if r["selection_evidence"]["has_matched_discriminator"])
        row["selection_evidence"].update(ml_acuity_before_override=2, final_acuity=5, source_estimated_acuity=5)
    path = tmp_path / "demo.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    if fault != "digest":
        monkeypatch.setattr(constants, "DEMO_COHORT_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    if fault == "missing":
        path.unlink()
    with pytest.raises(UhlDatasetContractError):
        get_case_repository(Settings(uhl_demo_data_path=path)).status()


def test_readiness_and_reports_do_not_require_full_training_data(tmp_path, monkeypatch):
    from app.api.health_routes import _readiness_payload, _uhl_status_payload
    from app.api.status_routes import _uhl_model_performance_payload
    monkeypatch.setattr(settings, "uhl_data_path", tmp_path / "not-shipped.csv.gz")
    monkeypatch.setattr(settings, "processed_dir", tmp_path / "processed")
    monkeypatch.setattr(settings, "uhl_case_cache_path", tmp_path / "unused.sqlite3")
    readiness = _readiness_payload()
    assert readiness["ready"] is True
    assert readiness["source_rows"] == readiness["expected_source_rows"] == 100
    status = _uhl_status_payload()
    assert status["serving_data_sha256"] == DEMO_COHORT_SHA256
    assert status["expected_matched_cases"] == 60
    assert status["expected_acuity_counts"][5] == 5
    performance = _uhl_model_performance_payload()
    assert performance["status"] == "available"
    assert performance["curated_demo_is_evaluation"] is False
    assert not settings.uhl_case_cache_path.exists()


def test_safe_case_view_discloses_selection():
    from app.api.safe_dto import safe_clinical_case_view
    row = get_case_repository(settings).list_cases(limit=1)["cases"][0]
    payload = safe_clinical_case_view(row["case_uid"], DATASET_SOURCE, row)
    assert payload["is_curated_demo"] is True
    assert "not an evaluation cohort" in payload["demo_data_notice"]
    assert "synthetic vitals" in payload["demo_data_notice"]


def test_v3_cohort_has_source_values_and_complaint_diversity():
    import math
    from scripts.build_demo_cohort import VITAL_FIELDS, vital_distances
    manifest = json.loads(settings.serving_data_path.read_text())
    assert manifest["cohort_id"] == "uhl_curated_demo_100_v3"
    assert manifest["selection"]["source_inputs_unchanged"] is True
    profile = manifest["selection"]["vital_profile"]
    complaints = {i: Counter() for i in range(1, 6)}
    for row in manifest["cases"]:
        for field in VITAL_FIELDS:
            assert profile[field]["lower"] <= row[field] <= profile[field]["upper"]
        evidence = row["selection_evidence"]
        peak, mean = vital_distances(row, profile)
        assert evidence["peak_vital_distance"] == pytest.approx(peak)
        assert evidence["mean_vital_distance"] == pytest.approx(mean)
        assert row["presenting_complaint"] not in {"UNWELL CHILD", "LIMPING CHILD"}
        assert row["dbp"] <= row["sbp"]
        assert 0 <= row["pain"] <= manifest["selection"]["maximum_pain"] == 8
        assert row["presenting_complaint"] != "QUERY COVID-19" or row["arrival_time"] >= "2020-01-01"
        complaints[evidence["final_acuity"]][row["presenting_complaint"]] += 1
    for acuity, quota in DEMO_COHORT_CATEGORY_COUNTS.items():
        assert max(complaints[acuity].values()) <= math.ceil(quota / 3)


def test_deployment_uses_small_asset_and_example_has_no_credentials():
    root = Path(__file__).resolve().parents[1]
    docker = (root / "Dockerfile").read_text()
    workflow = (root / ".github/workflows/deploy-azure.yml").read_text()
    startup = (root / "startup-backend.sh").read_text()
    assert "COPY data/uhl_demo_100.json" in docker
    assert "COPY data/uhl_dataset_final.csv.gz" not in docker
    assert "data/uhl_dataset_final.csv.gz" in (root / ".dockerignore").read_text().splitlines()
    assert "--exclude 'data/uhl_dataset_final.csv.gz'" in workflow
    assert '[ "${UHL_CASE_COHORT}" = "full" ]' in startup
    for line in (root / ".env.example").read_text().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if any(token in key for token in ("API_KEY", "SECRET", "PASSWORD", "CONNECTION_STRING")) or key.endswith("_TOKEN"):
            assert value.strip() in ("", "your-key-here", "replace-me")


def test_runtime_zip_manifest_rejects_missing_assets_and_full_dataset(tmp_path):
    import zipfile
    from scripts.verify_runtime_assets import REQUIRED, verify_runtime_assets

    def archive(names):
        path = tmp_path / "runtime.zip"
        with zipfile.ZipFile(path, "w") as output:
            for name in names:
                output.writestr(name, b"manifest test")
        return path

    verify_runtime_assets(archive(REQUIRED))
    with pytest.raises(ValueError, match="Missing runtime assets"):
        verify_runtime_assets(archive(REQUIRED - {"data/uhl_demo_100.json"}))
    with pytest.raises(ValueError, match="Full training cohort"):
        verify_runtime_assets(archive(REQUIRED | {"data/uhl_dataset_final.csv.gz"}))


def test_concurrent_reload_and_reads_remain_consistent():
    from concurrent.futures import ThreadPoolExecutor
    repository = get_case_repository(settings)

    def access(number):
        if number % 3 == 0:
            repository.clear_derived_cache()
        elif number % 3 == 1:
            assert repository.status()["source_rows"] == 100
        else:
            assert len(repository.list_cases()["cases"]) == 100

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(access, range(60)))
