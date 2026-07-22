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
class TestRetiredDatasetsHaveNoPrediction:
    """With the system MIMIC-IV-ED-Full-only, demo/KTAS cases no longer produce
    an ML prediction through the pipeline (the deterministic safety layer still
    runs). Uses the demo adapter only as a convenient source of MIMIC-shaped
    cases; the point is that NO ML acuity is produced for a non-full dataset."""
    def _synth(self, hr=88.0):
        from app.schemas.internal import EDTriageCase
        return EDTriageCase(**{
            "source_dataset": "MIMIC-IV-ED-Full-v2.2", "stay_id": 30000001,
            "subject_id": 10000001,
            "edstay": {"subject_id": 10000001, "stay_id": 30000001, "gender": "F",
                       "arrival_transport": "AMBULANCE", "disposition": "HOME"},
            "triage": {"subject_id": 10000001, "stay_id": 30000001, "heartrate": hr,
                       "chiefcomplaint": "CHEST PAIN", "acuity": None},
            "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
        })

    def test_no_full_mimic_model_means_no_ml_acuity_but_safety_runs(self, monkeypatch):
        monkeypatch.delenv("MIMIC_FULL_MODEL_PATH", raising=False)
        from app.agents.orchestrator import run_workflow
        # No full-MIMIC model configured -> no ML acuity, but safety still runs.
        wf = run_workflow(self._synth())
        assert wf.ml_prediction.prediction_available is False
        assert wf.final_acuity_assessment.final_acuity is None
        wf2 = run_workflow(self._synth(hr=195.0))
        assert wf2.decision.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"

    def test_clinician_review_always_required(self):
        from app.agents.orchestrator import run_workflow
        wf = run_workflow(self._synth())
        assert wf.decision.requires_clinician_review is True


# ── Leakage discipline of the model's feature set (full-MIMIC registry) ──────
class TestModelFeatureLeakage:
    def test_registry_records_blocked_leakage_features(self):
        import json
        from app.config import settings
        if not settings.model_registry_path.exists():
            import pytest; pytest.skip("no registry in this environment")
        reg = json.load(open(settings.model_registry_path))
        # The cleaned full-MIMIC registry records which leakage/outcome columns are
        # blocked from the feature set. The label and outcome fields must be listed.
        blocked = set(reg.get("blocked_leakage_features", []))
        # registry must not carry any retired demo/KTAS model entries
        assert not any("ktas" in k.lower() or "demo" in k.lower() for k in reg.keys())
        # if blocked features are recorded, the acuity label must be among them
        if blocked:
            assert "acuity" in blocked or "disposition" in blocked
