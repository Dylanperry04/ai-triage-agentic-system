"""
Tests for app/api/explanation_routes.py.

The most important test class is TestExtractMissingFields, which guards
against a real bug found during third-party code review and reproduced
live before fixing: _extract_missing_fields() previously searched for a
"MISSING_TRIAGE_FIELD:" flag prefix that the safety review agent never
actually produces (it produces "MISSING_CRITICAL_VITAL:{field}" and
"MISSING_CHIEF_COMPLAINT" instead), so missing_required_fields was
silently always empty, hiding genuinely missing data from the LLM
evidence package. Fixed by reading data_validation.missing_required_fields
directly rather than re-inferring missingness from a flag string at all.
"""
from __future__ import annotations

import pytest

from app.api.explanation_routes import _extract_missing_fields, build_verified_endpoint_evidence
from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource
from app.agents.orchestrator import run_workflow


def _make_case(stay_id: int = 1, **triage_kwargs) -> EDTriageCase:
    defaults = dict(
        subject_id=stay_id, stay_id=stay_id, chiefcomplaint="test complaint",
        heartrate=80.0, resprate=16.0, o2sat=98.0, sbp=120.0, dbp=78.0,
        temperature=98.6, temperature_unit="F", pain="2",
    )
    defaults.update(triage_kwargs)
    return EDTriageCase(
        stay_id=stay_id, subject_id=stay_id, source_dataset="Kaggle-KTAS",
        edstay=EDStaySource(subject_id=stay_id, stay_id=stay_id,
                             gender="F", arrival_transport="Walk-in"),
        triage=TriageSource(**defaults),
    )


class TestExtractMissingFields:
    def test_complete_case_returns_empty_list(self):
        case = _make_case()
        workflow = run_workflow(case, include_llm_explanation=False).model_dump(mode="json")
        assert _extract_missing_fields(workflow) == []

    def test_missing_single_vital_is_correctly_extracted(self):
        """
        Direct regression test: a case genuinely missing o2sat must
        produce ['o2sat'], not an empty list -- this is the exact case
        that was reproduced live as broken before the fix.
        """
        case = _make_case(o2sat=None)
        workflow = run_workflow(case, include_llm_explanation=False).model_dump(mode="json")
        missing = _extract_missing_fields(workflow)
        assert "o2sat" in missing

    def test_missing_multiple_vitals_all_extracted(self):
        case = _make_case(o2sat=None, heartrate=None, sbp=None)
        workflow = run_workflow(case, include_llm_explanation=False).model_dump(mode="json")
        missing = _extract_missing_fields(workflow)
        assert "o2sat" in missing
        assert "heartrate" in missing
        assert "sbp" in missing

    def test_missing_chief_complaint_extracted(self):
        case = _make_case(chiefcomplaint=None)
        workflow = run_workflow(case, include_llm_explanation=False).model_dump(mode="json")
        missing = _extract_missing_fields(workflow)
        assert "chiefcomplaint" in missing

    def test_old_buggy_flag_prefix_is_irrelevant_to_this_function_now(self):
        """
        Confirms the fix no longer depends on safety_review.data_quality_flags
        at all -- a fabricated workflow dict with the old (nonexistent)
        flag prefix but a correct data_validation section should still
        extract correctly from data_validation, proving the function reads
        the right source now.
        """
        fake_workflow = {
            "safety_review": {"data_quality_flags": ["SOME_OTHER_FLAG"]},
            "data_validation": {"missing_required_fields": ["o2sat", "sbp"]},
        }
        assert _extract_missing_fields(fake_workflow) == ["o2sat", "sbp"]


class TestBuildVerifiedEndpointEvidence:
    def test_legacy_raw_stay_id_evidence_builder_is_retired(self):
        """
        The raw-stay_id evidence builder used to load a local sample file. That
        route is retired; canonical explanations now go through
        /cases/{case_uid}/explanations.
        """
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            build_verified_endpoint_evidence(42)
        assert exc.value.status_code == 410
