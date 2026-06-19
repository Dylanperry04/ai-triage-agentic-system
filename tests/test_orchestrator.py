"""
Tests for the workflow orchestrator.

Verifies end-to-end workflow correctness and clinical safety invariants.

Run with: pytest tests/test_orchestrator.py -v
"""
import pytest
from app.schemas.internal import EDTriageCase, EDStaySource, TriageSource
from app.agents.orchestrator import run_workflow


def _make_case(chiefcomplaint="chest pain", **triage_kwargs) -> EDTriageCase:
    triage_defaults = {
        "subject_id": 1, "stay_id": 1,
        "temperature": 98.6, "heartrate": 80.0,
        "resprate": 16.0, "o2sat": 98.0,
        "sbp": 120.0, "dbp": 80.0, "pain": "5",
        "chiefcomplaint": chiefcomplaint,
        "acuity": 2.0,  # retrospective — must NOT appear in triage input
    }
    triage_defaults.update(triage_kwargs)
    return EDTriageCase(
        stay_id=1,
        subject_id=1,
        source_dataset="MIMIC-IV-ED-Demo-v2.2",
        edstay=EDStaySource(
            subject_id=1, stay_id=1,
            disposition="ADMITTED",  # retrospective — must NOT appear in triage input
            outtime="2024-01-01 12:00:00",
        ),
        triage=TriageSource(**triage_defaults),
    )


class TestOrchestrator:
    def test_workflow_completes_without_error(self):
        case = _make_case()
        result = run_workflow(case)
        assert result is not None
        assert result.stay_id == 1

    def test_workflow_output_contains_all_required_fields(self):
        case = _make_case()
        result = run_workflow(case)
        assert result.triage_input is not None
        assert result.data_validation is not None
        assert result.case_summary is not None
        assert result.decision is not None
        assert result.safety_review is not None
        assert result.retrospective_labels is not None
        assert result.audit is not None

    def test_retrospective_data_not_in_triage_input(self):
        case = _make_case()
        result = run_workflow(case)
        triage_dict = result.triage_input.model_dump()
        # acuity and disposition must NOT appear in triage_input
        assert "acuity" not in triage_dict
        assert "disposition" not in triage_dict
        assert "outtime" not in triage_dict

    def test_retrospective_labels_preserved_separately(self):
        case = _make_case()
        result = run_workflow(case)
        # Retrospective labels should be preserved for audit
        assert result.retrospective_labels.original_acuity == 2.0
        assert result.retrospective_labels.disposition == "ADMITTED"

    def test_clinician_review_always_required(self):
        case = _make_case()
        result = run_workflow(case)
        assert result.decision.requires_clinician_review is True

    def test_no_mts_category_without_approved_ruleset(self):
        """Without approved ruleset, no MTS category should ever be assigned."""
        case = _make_case()
        result = run_workflow(case)
        assert result.decision.category is None
        assert result.decision.priority is None
        assert result.decision.classification_status in (
            "AWAITING_APPROVED_CLINICAL_RULESET",
            "REQUIRES_CLINICIAN_REVIEW",
            "CRITICAL_PHYSIOLOGY_FLAGGED",
            "PHYSIOLOGY_CONCERN_FLAGGED",
        )

    def test_audit_record_populated(self):
        case = _make_case()
        result = run_workflow(case)
        assert "workflow_version" in result.audit
        assert "run_start_utc" in result.audit
        assert "clinical_decision_policy" in result.audit

    def test_workflow_without_llm_does_not_raise(self):
        """Workflow must complete safely without Azure OpenAI configured."""
        case = _make_case()
        result = run_workflow(case, include_llm_explanation=False)
        assert result.explanation.explanation_status == "NOT_REQUESTED"

    def test_missing_vitals_case_handled_safely(self):
        """Workflow must not crash when vital signs are absent."""
        case = _make_case(
            o2sat=None, heartrate=None, temperature=None
        )
        result = run_workflow(case)
        assert result is not None
        assert result.safety_review.is_safe_to_present is False
        assert len(result.safety_review.critical_missing_vitals) > 0


class TestLlmEvidencePackageTemperatureLabelling:
    """
    Regression test for a real bug found during third-party code review:
    the LLM evidence package previously used the key "temperature_F" for
    triage_input.temperature unconditionally, even for KTAS cases where
    temperature_unit is "C". This meant a Celsius fever value was sent to
    the LLM explanation agent mislabelled as Fahrenheit, which could cause
    the LLM to reason about fever severity using the wrong unit entirely.
    Fixed in app/agents/orchestrator.py to send the raw value, its real
    unit, and a pre-computed Celsius value via the shared
    app.rules.vitals.temperature_c() utility.
    """

    def test_celsius_temperature_correctly_labelled_in_evidence_package(self, monkeypatch):
        captured_evidence = {}

        def fake_run_llm_explanation(case_evidence):
            captured_evidence.update(case_evidence)
            from app.schemas.workflow import ExplanationResult
            return ExplanationResult(explanation_status="TEST_STUB")

        monkeypatch.setattr(
            "app.agents.llm_explanation_agent.run_llm_explanation",
            fake_run_llm_explanation,
        )

        case = _make_case(temperature=38.9, temperature_unit="C")
        run_workflow(case, include_llm_explanation=True)

        vitals = captured_evidence["triage_vitals"]
        assert "temperature_F" not in vitals, (
            "The old buggy key must not be present at all."
        )
        assert vitals["temperature"] == 38.9
        assert vitals["temperature_unit"] == "C"
        assert vitals["temperature_c"] == 38.9  # already Celsius, no-op conversion

    def test_fahrenheit_temperature_correctly_converted_in_evidence_package(self, monkeypatch):
        captured_evidence = {}

        def fake_run_llm_explanation(case_evidence):
            captured_evidence.update(case_evidence)
            from app.schemas.workflow import ExplanationResult
            return ExplanationResult(explanation_status="TEST_STUB")

        monkeypatch.setattr(
            "app.agents.llm_explanation_agent.run_llm_explanation",
            fake_run_llm_explanation,
        )

        case = _make_case(temperature=102.0, temperature_unit="F")
        run_workflow(case, include_llm_explanation=True)

        vitals = captured_evidence["triage_vitals"]
        assert vitals["temperature"] == 102.0
        assert vitals["temperature_unit"] == "F"
        assert abs(vitals["temperature_c"] - 38.888888) < 0.01


class TestWorkflowResultWorkflowAction:
    """
    Tests for WorkflowResult.workflow_action, the single-run equivalent
    of FollowUpComparisonResult.workflow_action, added to support the
    Trial Matcher-style assessment card's explicit workflow_action field.
    """

    def test_critical_vitals_give_escalation_required(self):
        case = _make_case(chiefcomplaint="unrecognised xyz", o2sat=85.0)
        result = run_workflow(case, include_llm_explanation=False)
        assert result.decision.classification_status == "CRITICAL_PHYSIOLOGY_FLAGGED"
        assert result.workflow_action == "ESCALATION_REQUIRED"

    def test_concern_vitals_with_matched_pathway_give_escalation_required(self):
        case = _make_case(chiefcomplaint="chest pain", o2sat=92.0)
        result = run_workflow(case, include_llm_explanation=False)
        assert result.decision.classification_status == "PHYSIOLOGY_CONCERN_FLAGGED"
        assert result.workflow_action == "ESCALATION_REQUIRED"

    def test_missing_chief_complaint_gives_clinician_intervention_required(self):
        case = _make_case(chiefcomplaint=None)
        result = run_workflow(case, include_llm_explanation=False)
        assert result.decision.classification_status == "REQUIRES_CLINICIAN_REVIEW"
        assert result.workflow_action == "CLINICIAN_INTERVENTION_REQUIRED"

    def test_clean_unrecognised_complaint_gives_no_critical_physiology_flagged(self):
        case = _make_case(chiefcomplaint="unrecognised xyz")
        result = run_workflow(case, include_llm_explanation=False)
        assert result.decision.classification_status == "AWAITING_APPROVED_CLINICAL_RULESET"
        assert result.workflow_action == "NO_CRITICAL_PHYSIOLOGY_FLAGGED"

    def test_workflow_action_never_overrides_requires_clinician_review(self):
        case = _make_case(chiefcomplaint="unrecognised xyz")
        result = run_workflow(case, include_llm_explanation=False)
        assert result.decision.requires_clinician_review is True

    def test_workflow_action_is_always_one_of_three_enumerated_values(self):
        for cc, kw in [
            ("unrecognised xyz", {}), ("unrecognised xyz", {"o2sat": 85.0}),
            (None, {}), ("chest pain", {"o2sat": 92.0}),
        ]:
            case = _make_case(chiefcomplaint=cc, **kw)
            result = run_workflow(case, include_llm_explanation=False)
            assert result.workflow_action in {
                "ESCALATION_REQUIRED", "CLINICIAN_INTERVENTION_REQUIRED", "NO_CRITICAL_PHYSIOLOGY_FLAGGED",
            }
