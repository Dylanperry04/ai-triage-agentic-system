"""
Tests for the Clinical Safety Rules Engine (manchester_engine.py).

Two-layer design:
  Layer 1 — vital-sign safety detection (always active, never assigns MTS category
             unless an approved ruleset is registered)
  Layer 2 — MTS pathway engine (gated, only active with approved ruleset)

Run with: pytest tests/test_manchester_engine.py -v
"""
import pytest
from app.schemas.internal import TriageTimeInput
from app.rules.manchester_engine import (
    run_manchester_engine,
    register_approved_ruleset,
    mts_pathway_enabled,
)

TEST_RULESET = {
    "ruleset_id": "test-ruleset-v1",
    "approved_by": "Test Clinician",
    "approved_date": "2025-01-01",
    "source": "Test ruleset for unit testing only",
    "acknowledge_heuristic_pathways": True,
}


def _make_input(**kwargs) -> TriageTimeInput:
    # MTS engine now applies only to MIMIC cases; these tests exercise the MTS
    # pathway logic, so they use the MIMIC dataset. The KTAS-gate is tested
    # separately (a KTAS source_dataset yields MTS_NOT_APPLIED_TO_KTAS_DATASET).
    defaults = {
        "subject_id": 1, "stay_id": 1,
        "source_dataset": "MIMIC-IV-ED-Demo-v2.2",
        "chiefcomplaint": "chest pain",
        "temperature": 98.6, "heartrate": 80.0,
        "resprate": 16.0, "o2sat": 98.0,
        "sbp": 120.0, "dbp": 80.0, "pain": "5",
    }
    defaults.update(kwargs)
    return TriageTimeInput(**defaults)


@pytest.fixture(autouse=True)
def reset_ruleset(monkeypatch):
    """Reset approved ruleset to None before every test."""
    import app.rules.manchester_engine as m
    monkeypatch.setattr(m, "_APPROVED_RULESET", None)
    yield
    monkeypatch.setattr(m, "_APPROVED_RULESET", None)


# ── Layer 1: Critical vital flags — no approved ruleset ──────────────────────

class TestCriticalVitalsNoRuleset:
    """Without approved ruleset: critical physiology → CRITICAL_PHYSIOLOGY_FLAGGED.
    No MTS category, colour, priority, or max_wait assigned."""

    def test_spo2_below_90_is_critical_flag_not_category(self):
        result = run_manchester_engine(_make_input(o2sat=88.0))
        assert result.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"
        assert result.category is None
        assert result.priority is None
        assert result.max_wait_minutes is None
        assert "CRITICAL_HYPOXIA_SPO2_BELOW_90" in result.reason_codes

    def test_resprate_above_29_is_critical_flag(self):
        result = run_manchester_engine(_make_input(resprate=30.0))
        assert result.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"
        assert result.priority is None

    def test_resprate_below_8_is_critical_flag(self):
        result = run_manchester_engine(_make_input(resprate=7.0))
        assert result.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"
        assert "CRITICAL_RESPIRATORY_RATE_BELOW_8" in result.reason_codes

    def test_heartrate_above_130_is_critical_flag(self):
        result = run_manchester_engine(_make_input(heartrate=135.0))
        assert result.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"
        assert "CRITICAL_HEART_RATE_ABOVE_130" in result.reason_codes

    def test_heartrate_below_40_is_critical_flag(self):
        result = run_manchester_engine(_make_input(heartrate=35.0))
        assert result.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"

    def test_sbp_below_90_is_critical_flag(self):
        result = run_manchester_engine(_make_input(sbp=85.0))
        assert result.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"
        assert "CRITICAL_HYPOTENSION_SBP_BELOW_90" in result.reason_codes

    def test_high_temperature_is_critical_flag(self):
        result = run_manchester_engine(_make_input(temperature=106.0))
        assert result.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"

    def test_low_temperature_is_critical_flag(self):
        result = run_manchester_engine(_make_input(temperature=94.0))
        assert result.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"

    def test_normal_vitals_not_critical(self):
        result = run_manchester_engine(_make_input(
            o2sat=98.0, resprate=16.0, heartrate=80.0,
            sbp=120.0, temperature=98.6,
        ))
        assert result.classification_status != "CRITICAL_PHYSIOLOGY_FLAGGED"


# ── Layer 1: Critical vitals WITH approved ruleset ───────────────────────────

class TestCriticalVitalsWithRuleset:
    def test_critical_vitals_assign_immediate_with_ruleset(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(o2sat=88.0))
        assert result.priority == 1
        assert "Immediate" in result.category
        assert result.ruleset_id == "test-ruleset-v1"


# ── Layer 2: Pathway gating — no approved ruleset ────────────────────────────

class TestPathwayGatingNoRuleset:
    def test_chest_pain_awaiting_ruleset(self):
        result = run_manchester_engine(_make_input(chiefcomplaint="chest pain", pain="8"))
        assert result.classification_status == "AWAITING_APPROVED_CLINICAL_RULESET"
        assert result.priority is None
        assert result.category is None
        assert result.max_wait_minutes is None
        assert result.ruleset_id is None

    def test_stroke_awaiting_ruleset(self):
        result = run_manchester_engine(_make_input(chiefcomplaint="sudden weakness"))
        assert result.classification_status == "AWAITING_APPROVED_CLINICAL_RULESET"

    def test_overdose_awaiting_ruleset(self):
        result = run_manchester_engine(_make_input(chiefcomplaint="overdose"))
        assert result.classification_status == "AWAITING_APPROVED_CLINICAL_RULESET"

    def test_mts_pathway_disabled_by_default(self):
        assert not mts_pathway_enabled()

    def test_register_without_acknowledgement_raises(self):
        """
        register_approved_ruleset() must raise if acknowledge_heuristic_pathways
        is not explicitly set to True. This prevents accidental activation.
        """
        with pytest.raises(ValueError, match="acknowledge_heuristic_pathways"):
            register_approved_ruleset(
                ruleset_id="test",
                approved_by="test",
                approved_date="2025-01-01",
                source="test",
                acknowledge_heuristic_pathways=False,
            )


# ── Layer 2: Pathway engine WITH approved ruleset ─────────────────────────────

class TestPathwayWithRuleset:
    def test_crushing_chest_pain_is_orange(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="crushing chest pain", pain="8"))
        assert result.priority == 2
        assert "Orange" in result.category

    def test_severe_chest_pain_is_orange(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="chest pain", pain="8"))
        assert result.priority == 2

    def test_moderate_chest_pain_is_yellow(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="chest pain", pain="5"))
        assert result.priority == 3

    def test_mild_chest_pain_unknown_is_yellow(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="chest pain", pain=None))
        assert result.priority == 3

    def test_acute_stroke_is_orange(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="sudden weakness left arm"))
        assert result.priority == 2

    def test_slurred_speech_is_orange(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="slurred speech onset"))
        assert result.priority == 2

    def test_sepsis_fever_is_orange(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="fever sepsis"))
        assert result.priority == 2

    def test_mild_fever_is_green(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="fever low grade", pain="1"))
        assert result.priority >= 4

    def test_concern_vital_upgrades_result(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(o2sat=92.0, chiefcomplaint="fever", pain="3"))
        assert result.priority == 2
        assert "CONCERN_VITAL_UPGRADE" in result.reason_codes

    def test_ruleset_id_in_output(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="chest pain", pain="8"))
        assert result.ruleset_id == "test-ruleset-v1"

    def test_unrecognised_complaint_high_pain_is_yellow(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="ear discharge", pain="8"))
        assert result.priority == 3

    def test_unrecognised_complaint_mild_pain_is_blue(self):
        register_approved_ruleset(**TEST_RULESET)
        result = run_manchester_engine(_make_input(chiefcomplaint="ear discharge", pain="2"))
        assert result.priority == 5


# ── Missing data ──────────────────────────────────────────────────────────────

class TestMissingData:
    def test_missing_chief_complaint_requires_review(self):
        result = run_manchester_engine(_make_input(chiefcomplaint=None))
        assert result.classification_status == "REQUIRES_CLINICIAN_REVIEW"
        assert result.priority is None
        assert "MISSING_CHIEF_COMPLAINT" in result.reason_codes

    def test_missing_vitals_does_not_crash(self):
        result = run_manchester_engine(_make_input(
            o2sat=None, heartrate=None, resprate=None,
            sbp=None, temperature=None, pain=None,
        ))
        assert result is not None
        assert result.requires_clinician_review is True


# ── Safety invariants ─────────────────────────────────────────────────────────

class TestSafetyInvariants:
    SAMPLE_INPUTS = [
        _make_input(chiefcomplaint="chest pain", pain="8"),
        _make_input(chiefcomplaint="shortness of breath", o2sat=88.0),
        _make_input(chiefcomplaint="stroke"),
        _make_input(chiefcomplaint="fever", temperature=102.0),
        _make_input(chiefcomplaint=None),
        _make_input(chiefcomplaint="abdominal pain", sbp=85.0),
        _make_input(chiefcomplaint="toothache"),
    ]

    @pytest.mark.parametrize("triage_input", SAMPLE_INPUTS)
    def test_requires_clinician_review_always_true(self, triage_input):
        result = run_manchester_engine(triage_input)
        assert result.requires_clinician_review is True

    @pytest.mark.parametrize("triage_input", SAMPLE_INPUTS)
    def test_priority_in_valid_range_or_none(self, triage_input):
        result = run_manchester_engine(triage_input)
        if result.priority is not None:
            assert 1 <= result.priority <= 5

    @pytest.mark.parametrize("triage_input", SAMPLE_INPUTS)
    def test_reason_codes_never_empty(self, triage_input):
        result = run_manchester_engine(triage_input)
        assert len(result.reason_codes) >= 1

    @pytest.mark.parametrize("triage_input", SAMPLE_INPUTS)
    def test_no_mts_category_without_approved_ruleset(self, triage_input):
        """
        CRITICAL: without an approved ruleset, no MTS category or priority
        may ever be assigned — even for critical physiology.
        """
        result = run_manchester_engine(triage_input)
        assert result.category is None, (
            f"MTS category '{result.category}' was assigned without approved ruleset "
            f"for: {triage_input.chiefcomplaint}"
        )
        assert result.priority is None, (
            f"Priority {result.priority} assigned without approved ruleset "
            f"for: {triage_input.chiefcomplaint}"
        )

    @pytest.mark.parametrize("triage_input", SAMPLE_INPUTS)
    def test_no_llm_in_engine(self, triage_input):
        import time
        start = time.time()
        run_manchester_engine(triage_input)
        assert time.time() - start < 1.0
