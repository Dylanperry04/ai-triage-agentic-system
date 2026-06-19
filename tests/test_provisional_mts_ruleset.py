"""
Tests for the provisional MTS research ruleset and its default-on wiring.

These lock in the safety-critical properties of provisional mode:
  - registering it makes the engine assign categories where it was gated
  - NOT registering it leaves the engine fully gated (the safe default path
    is still reachable and still works)
  - every category-bearing decision is labelled provisional and unvalidated
  - requires_clinician_review is True on every output, always
  - the provenance table stays consistent with the engine's live thresholds
    for the thresholds that can be checked directly
  - the env-var toggle disables provisional mode
"""
import importlib

import pytest

import app.rules.manchester_engine as me
from app.rules.provisional_mts_ruleset import (
    PROVISIONAL_RULESET_ID,
    THRESHOLD_PROVENANCE,
    register_provisional_ruleset,
    provisional_ruleset_summary,
)
from app.schemas.internal import TriageTimeInput


def _input(**kw) -> TriageTimeInput:
    base = dict(subject_id=1, stay_id=1, source_dataset="MIMIC-IV-ED-Demo-v2.2",
                temperature_unit="F")
    base.update(kw)
    return TriageTimeInput(**base)


# The autouse conftest fixture clears the ruleset before each test, so every
# test here starts gated regardless of import order.

class TestGatedByDefaultUntilRegistered:
    def test_engine_is_gated_before_registration(self):
        assert me.get_approved_ruleset() is None
        assert me.mts_pathway_enabled() is False
        d = me.run_manchester_engine(_input(chiefcomplaint="chest pain", o2sat=98.0))
        assert d.category is None
        assert d.classification_status == "AWAITING_APPROVED_CLINICAL_RULESET"

    def test_registration_enables_categories(self):
        register_provisional_ruleset()
        assert me.mts_pathway_enabled() is True
        d = me.run_manchester_engine(_input(chiefcomplaint="chest pain", o2sat=98.0))
        assert d.category is not None


class TestProvisionalLabellingIsUnmissable:
    def test_status_says_provisional(self):
        register_provisional_ruleset()
        d = me.run_manchester_engine(_input(chiefcomplaint="chest pain", o2sat=98.0))
        assert d.classification_status == "PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW"
        assert "PROVISIONAL" in d.classification_status

    def test_reason_codes_flag_unvalidated_ruleset(self):
        register_provisional_ruleset()
        d = me.run_manchester_engine(_input(chiefcomplaint="chest pain", o2sat=98.0))
        assert "PROVISIONAL_UNVALIDATED_RULESET" in d.reason_codes

    def test_ruleset_id_is_the_provisional_id(self):
        register_provisional_ruleset()
        d = me.run_manchester_engine(_input(chiefcomplaint="chest pain", o2sat=98.0))
        assert d.ruleset_id == PROVISIONAL_RULESET_ID

    def test_critical_case_provisional_status_too(self):
        # A critical case maps to Immediate (Red) under provisional mode, and
        # MUST still carry the provisional label -- not the approved status.
        register_provisional_ruleset()
        d = me.run_manchester_engine(_input(chiefcomplaint="chest pain", sbp=80.0))
        assert d.category == "Immediate (Red)"
        assert d.classification_status == "PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW"
        assert "PROVISIONAL_UNVALIDATED_RULESET" in d.reason_codes

    def test_concern_upgrade_case_provisional_status_too(self):
        # Matched pathway + concern vital triggers the inline upgrade branch;
        # that branch must also emit the provisional status, not the approved one.
        register_provisional_ruleset()
        d = me.run_manchester_engine(
            _input(chiefcomplaint="chest pain", o2sat=92.0)  # concern SpO2 90-94
        )
        assert d.classification_status == "PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW"
        assert "PROVISIONAL_UNVALIDATED_RULESET" in d.reason_codes


class TestClinicianReviewAlwaysRequired:
    @pytest.mark.parametrize("kw", [
        dict(chiefcomplaint="chest pain", o2sat=98.0),
        dict(chiefcomplaint="chest pain", sbp=80.0),           # critical
        dict(chiefcomplaint="fever", o2sat=92.0),              # concern upgrade
        dict(chiefcomplaint="something weird", nrs_pain=2),    # unrecognised
        dict(chiefcomplaint="cardiac arrest"),                 # immediate pathway
    ])
    def test_requires_clinician_review_is_always_true(self, kw):
        register_provisional_ruleset()
        d = me.run_manchester_engine(_input(**kw))
        assert d.requires_clinician_review is True


class TestApprovedVsProvisionalDistinction:
    def test_a_genuinely_approved_ruleset_gets_the_approved_status(self):
        # If a real approved ruleset is registered (validation_status set to
        # CLINICALLY_APPROVED), the engine must use the NON-provisional status,
        # proving the distinction is real and keyed off the ruleset itself.
        me.register_approved_ruleset(
            ruleset_id="real-approved-v1",
            approved_by="Dr Example",
            approved_date="2026-06-18",
            source="governance record 123",
            acknowledge_heuristic_pathways=True,
        )
        # Promote it to truly-approved (the provisional path leaves the
        # REQUIRES_CLINICAL_VALIDATION marker; an approved one would not).
        me._APPROVED_RULESET["validation_status"] = "CLINICALLY_APPROVED"
        d = me.run_manchester_engine(_input(chiefcomplaint="chest pain", o2sat=98.0))
        assert d.classification_status == "MTS_CATEGORY_ASSIGNED_PENDING_CLINICIAN_REVIEW"
        assert "PROVISIONAL_UNVALIDATED_RULESET" not in d.reason_codes


class TestProvenanceConsistency:
    def test_summary_reports_not_official_not_approved(self):
        s = provisional_ruleset_summary()
        assert s["is_official_mts"] is False
        assert s["is_clinically_approved"] is False
        assert s["ruleset_id"] == PROVISIONAL_RULESET_ID

    def test_published_spo2_thresholds_match_engine(self):
        # The two PUBLISHED_MTS_DISCRIMINATOR rows claim specific SpO2 cuts.
        # Confirm the live engine actually flags those exact boundaries, so the
        # provenance table cannot silently drift from the code it documents.
        register_provisional_ruleset()
        # <90 on air -> critical hypoxia
        crit = me._critical_vital_flags(_input(o2sat=89.0))
        assert "CRITICAL_HYPOXIA_SPO2_BELOW_90" in crit
        # 90-94 -> concern
        concern = me._concern_vital_flags(_input(o2sat=92.0))
        assert "CONCERN_SPO2_90_TO_94" in concern
        # 95 -> neither
        assert me._critical_vital_flags(_input(o2sat=95.0)) == []
        assert me._concern_vital_flags(_input(o2sat=95.0)) == []

    def test_every_provenance_row_has_a_source_tag(self):
        valid = {
            "PUBLISHED_MTS_DISCRIMINATOR",
            "PROVISIONAL_STANDARD_PHYSIOLOGY",
            "PROVISIONAL_APPROXIMATION_OF_MTS_STRUCTURE",
        }
        for key, row in THRESHOLD_PROVENANCE.items():
            assert row["source"] in valid, key
            assert row["citation"]


class TestEnvVarToggle:
    def test_provisional_mode_off_via_env(self, monkeypatch):
        monkeypatch.setenv("PROVISIONAL_MTS_MODE", "off")
        import app.config as cfg
        importlib.reload(cfg)
        try:
            assert cfg.settings.provisional_mts_mode is False
        finally:
            monkeypatch.delenv("PROVISIONAL_MTS_MODE", raising=False)
            importlib.reload(cfg)

    def test_provisional_mode_on_by_default(self, monkeypatch):
        monkeypatch.delenv("PROVISIONAL_MTS_MODE", raising=False)
        import app.config as cfg
        importlib.reload(cfg)
        assert cfg.settings.provisional_mts_mode is True
