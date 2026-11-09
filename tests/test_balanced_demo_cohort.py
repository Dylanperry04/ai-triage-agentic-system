from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.constants import BALANCED_DEMO_COHORT_SHA256, DATASET_SOURCE
from app.data_pipeline.uhl_repository import UhlDatasetContractError, get_case_repository
from app.rules.mts_catalog import propose_flowchart


ROOT = Path(__file__).resolve().parents[1]


def test_balanced_demo_is_pinned_and_serves_reproducible_supported_and_abstaining_cases(monkeypatch):
    monkeypatch.setattr(settings, "uhl_case_cohort", "demo_balanced_100")
    repository = get_case_repository(settings)
    manifest = json.loads(settings.serving_data_path.read_bytes())
    legacy = json.loads(settings.uhl_demo_data_path.read_bytes())
    cases = manifest["cases"]
    assert hashlib.sha256(settings.serving_data_path.read_bytes()).hexdigest() == BALANCED_DEMO_COHORT_SHA256
    assert len({row["row_number"] for row in cases}) == 100
    assert {row["row_number"] for row in cases}.isdisjoint(row["row_number"] for row in legacy["cases"])
    assert sum(row["age"] >= 75 for row in cases) > 0
    assert manifest["selection"]["split"] == "TRAIN"
    for row in cases:
        assert "selection_evidence" not in row
        assert row["proposed_flowchart"] == propose_flowchart(row["presenting_complaint"])["flowchart_id"]
    assert Counter(row["selection_group"] for row in cases) == {
        "chest_pain": 20, "abdominal_pain": 20, "respiratory_symptoms": 20, "limb_or_wound": 20,
        "ambiguous_injury": 10, "unwell_adult": 5, "other_unsupported": 5,
    }
    assert sum(row["proposed_flowchart"] is None for row in cases) == 20
    assert repository.status()["source_rows"] == 100

    from app.api.health_routes import _uhl_status_payload
    status = _uhl_status_payload()
    assert status["active_case_source_mode"] == "balanced_demo_100"
    assert status["curated_demonstration"]
    assert status["expected_acuity_counts"] is status["expected_matched_cases"] is None

    from app.api.safe_dto import safe_clinical_case_view
    case = repository.list_cases(limit=1)["cases"][0]
    view = safe_clinical_case_view(case["case_uid"], DATASET_SOURCE, case)
    assert view["is_curated_demo"] and "no independent clinical reference" in view["demo_data_notice"]
    assert "source_estimated_acuity" not in str(view)


def test_balanced_mode_does_not_silently_load_legacy_or_a_modified_manifest(tmp_path):
    path = tmp_path / "wrong_cohort.json"
    path.write_bytes((ROOT / "data/uhl_demo_100.json").read_bytes())
    repository = get_case_repository(Settings(uhl_case_cohort="demo_balanced_100", uhl_balanced_demo_data_path=path))
    with pytest.raises(UhlDatasetContractError, match="hash mismatch"):
        repository.status()
