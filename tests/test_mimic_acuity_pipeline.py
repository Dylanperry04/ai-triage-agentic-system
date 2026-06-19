"""
Tests for the MIMIC acuity ML pipeline, the acuity->MTS-display mapping, the
deterministic escalate-only vital override, and the KTAS/MIMIC separation.

These lock in the safety-critical behaviour:
  - the mapping is exactly Dylan's law (1->Red ... 5->Blue)
  - the override is ESCALATE-ONLY (never moves a case to less urgent)
  - the two tiers (EXTREME->Red, CRITICAL->Orange) fire on the right thresholds
  - KTAS cases never get an MTS category / MIMIC fields; MIMIC cases never get
    KTAS fields -- the two triage systems are never mixed
"""
import pytest

from app.schemas.internal import TriageTimeInput
from app.rules.acuity_mts_mapping import map_acuity_to_mts, MAPPING_RULE_VERSION
from app.rules.acuity_override import apply_acuity_override


def _mimic(**kw) -> TriageTimeInput:
    base = dict(subject_id=1, stay_id=1, source_dataset="MIMIC-IV-ED-Demo-v2.2",
                temperature_unit="F")
    base.update(kw)
    return TriageTimeInput(**base)


def _ktas(**kw) -> TriageTimeInput:
    base = dict(subject_id=1, stay_id=1, source_dataset="Kaggle-KTAS",
                temperature_unit="C")
    base.update(kw)
    return TriageTimeInput(**base)


# ── The mapping is exactly the law ──────────────────────────────────────────
class TestAcuityToMtsMapping:
    @pytest.mark.parametrize("acuity,category,priority,wait,colour", [
        (1, "Immediate (Red)", 1, 0, "red"),
        (2, "Very Urgent (Orange)", 2, 10, "orange"),
        (3, "Urgent (Yellow)", 3, 60, "yellow"),
        (4, "Standard (Green)", 4, 120, "green"),
        (5, "Non-Urgent (Blue)", 5, 240, "blue"),
    ])
    def test_each_level_maps_exactly(self, acuity, category, priority, wait, colour):
        m = map_acuity_to_mts(acuity)
        assert m["category"] == category
        assert m["priority"] == priority
        assert m["max_wait_minutes"] == wait
        assert m["colour"] == colour
        assert m["mapping_rule_version"] == MAPPING_RULE_VERSION
        assert m["is_official_mts"] is False
        assert m["is_clinically_approved"] is False

    @pytest.mark.parametrize("bad", [None, 0, 6, 7, "x", -1])
    def test_invalid_acuity_returns_none(self, bad):
        assert map_acuity_to_mts(bad) is None


# ── Override: escalate-only, two tiers ──────────────────────────────────────
class TestAcuityOverride:
    def test_extreme_hr_escalates_green_to_red(self):
        r = apply_acuity_override(4, _mimic(heartrate=190))
        assert r["final_acuity"] == 1
        assert r["override_applied"] is True
        assert r["override_tier"] == "EXTREME"

    def test_critical_spo2_escalates_green_to_orange(self):
        r = apply_acuity_override(4, _mimic(o2sat=87))
        assert r["final_acuity"] == 2
        assert r["override_applied"] is True
        assert r["override_tier"] == "CRITICAL"

    def test_override_never_de_escalates(self):
        # ML already Orange (2); a CRITICAL vital must NOT push it to Yellow.
        r = apply_acuity_override(2, _mimic(heartrate=135))
        assert r["final_acuity"] == 2
        assert r["override_applied"] is False

    def test_ml_more_urgent_than_floor_stands(self):
        # ML Red (1) + EXTREME vital -> stays Red, not "applied" (already there).
        r = apply_acuity_override(1, _mimic(o2sat=80))
        assert r["final_acuity"] == 1
        assert r["override_applied"] is False

    def test_no_abnormal_vitals_keeps_ml_prediction(self):
        r = apply_acuity_override(3, _mimic(heartrate=80, o2sat=98))
        assert r["final_acuity"] == 3
        assert r["override_applied"] is False
        assert r["override_tier"] is None

    def test_floor_catches_when_ml_missing(self):
        r = apply_acuity_override(None, _mimic(o2sat=82))
        assert r["final_acuity"] == 1
        assert r["override_applied"] is True

    @pytest.mark.parametrize("kw,expected_tier", [
        (dict(o2sat=84), "EXTREME"),
        (dict(o2sat=88), "CRITICAL"),
        (dict(heartrate=151), "EXTREME"),
        (dict(heartrate=131), "CRITICAL"),
        (dict(resprate=36), "EXTREME"),
        (dict(resprate=30), "CRITICAL"),
        (dict(sbp=79), "EXTREME"),
        (dict(sbp=89), "CRITICAL"),
    ])
    def test_tier_thresholds(self, kw, expected_tier):
        # ML at lowest urgency (5) so any floor is an escalation we can read.
        r = apply_acuity_override(5, _mimic(**kw))
        assert r["override_tier"] == expected_tier
        assert r["override_applied"] is True


# ── KTAS/MIMIC separation through the real pipeline ─────────────────────────
class TestDatasetSeparationThroughPipeline:
    def test_mimic_case_gets_acuity_and_mapped_mts_not_ktas(self):
        from app.config import settings
        from app.data_pipeline.mimic_adapter import load_mimic_demo_cases
        from app.agents.orchestrator import run_workflow
        mimic, _ = load_mimic_demo_cases(settings.raw_demo_dir, n=3)
        wf = run_workflow(mimic[0])
        ml = wf.ml_prediction
        assert ml.prediction_scale == "MIMIC_ACUITY_MAPPED_TO_MTS"
        assert ml.predicted_mimic_acuity is not None
        assert ml.mapped_mts_category is not None
        assert ml.predicted_ktas_class is None         # never KTAS fields
        fa = wf.final_acuity_assessment
        assert fa.applicable is True
        assert fa.category is not None
        assert fa.requires_clinician_review is True

    def test_ktas_case_gets_ktas_not_mts_category_or_mimic_fields(self):
        from app.config import settings
        from app.data_pipeline.ktas_adapter import load_ktas_cases
        from app.agents.orchestrator import run_workflow
        ktas, _ = load_ktas_cases(settings.raw_ktas_csv, n=3)
        wf = run_workflow(ktas[0])
        ml = wf.ml_prediction
        assert ml.prediction_scale == "KTAS"
        assert ml.predicted_ktas_class is not None
        assert ml.mapped_mts_category is None          # never MTS mapping
        assert ml.predicted_mimic_acuity is None       # never MIMIC fields
        # rules engine assigns NO MTS category to KTAS
        assert wf.decision.category is None
        # final acuity assessment not applicable to KTAS
        assert wf.final_acuity_assessment.applicable is False
        assert wf.decision.requires_clinician_review is True


# ── Leakage discipline of the trained model's feature set ───────────────────
class TestModelFeatureLeakage:
    def test_model_registry_excludes_leakage_features(self):
        import json
        from app.config import settings
        reg = json.load(open(settings.model_registry_path))
        info = reg["best_mimic_acuity_model"]
        all_feats = set(info["feature_names_numeric"]) | set(info["feature_names_categorical"])
        forbidden = {"acuity", "disposition", "outtime", "hadm_id",
                     "diagnosis_icd", "vitalsign_timeseries", "medrecon", "pyxis"}
        assert all_feats & forbidden == set(), (
            f"Leakage feature(s) in MIMIC model: {all_feats & forbidden}"
        )
        # the label must not be a feature
        assert "acuity" not in all_feats
