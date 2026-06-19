"""
Tests for the shared LLM-output phrase-blocking safety filter
(app/rules/llm_safety_filter.py) and its use in llm_explanation_agent.py.

This logic had no test coverage at all before the AutoGen integration work,
despite being the deterministic safety net for every LLM-generated clinical
explanation in this project. These tests close that gap.
"""
from app.rules.llm_safety_filter import check_forbidden_phrases
from app.agents.llm_explanation_agent import _validate_explanation_safety


class TestSharedForbiddenPhraseFilter:
    def test_clean_text_passes(self):
        text = (
            "The heart rate is 84 bpm and blood pressure is 160/100. "
            "Human clinical review is required before any action."
        )
        assert check_forbidden_phrases(text) == []

    def test_catches_manchester_colour_assignment(self):
        text = "Based on the evidence, the patient is assigned red."
        failures = check_forbidden_phrases(text)
        assert any("FORBIDDEN_TRIAGE_ASSIGNMENT_PHRASE" in f for f in failures)

    def test_catches_diagnosis_language(self):
        text = "The diagnosis is appendicitis."
        failures = check_forbidden_phrases(text)
        assert any("FORBIDDEN_CLINICAL_ADVICE_PHRASE" in f for f in failures)

    def test_catches_disposition_advice(self):
        text = "It is safe to go home now."
        failures = check_forbidden_phrases(text)
        assert any("FORBIDDEN_CLINICAL_ADVICE_PHRASE" in f for f in failures)

    def test_catches_treatment_advice(self):
        text = "Administer 500mg paracetamol."
        failures = check_forbidden_phrases(text)
        assert any("FORBIDDEN_CLINICAL_ADVICE_PHRASE" in f for f in failures)

    def test_case_insensitive(self):
        text = "ASSIGNED RED"
        failures = check_forbidden_phrases(text)
        assert any("FORBIDDEN_TRIAGE_ASSIGNMENT_PHRASE" in f for f in failures)

    def test_does_not_require_missing_data_statement(self):
        """
        This is the key behavioural difference from the explanation agent's
        stricter check: the shared filter alone must NOT fail a short,
        benign factual answer just because it doesn't mention missing data
        or category assignment -- those are format requirements specific to
        the explanation agent's five-section structure, not safety
        requirements that apply to every possible LLM output.
        """
        text = "The heart rate is 84 bpm."
        assert check_forbidden_phrases(text) == []


class TestExplanationAgentCompletenessChecks:
    """
    These checks are specific to llm_explanation_agent.py's mandated
    five-section response format, layered on top of the shared filter.
    """

    def _valid_five_section_response(self) -> str:
        return (
            "1. Evidence Used: heart rate 84 bpm, temperature 36.6C.\n"
            "2. Missing or Limited Data: pain score not provided.\n"
            "3. Safety and Rules Engine Status: no critical flags.\n"
            "4. ML Risk Estimate: not available.\n"
            "5. Human Review Required: a clinician must review this case; "
            "no Manchester triage category has been assigned."
        )

    def test_well_formed_response_passes(self):
        assert _validate_explanation_safety(self._valid_five_section_response()) == []

    def test_missing_human_review_statement_fails(self):
        text = "Evidence used: heart rate 84. Missing data: pain score. No category assigned."
        failures = _validate_explanation_safety(text)
        assert "MISSING_HUMAN_REVIEW_REQUIREMENT" in failures

    def test_missing_data_limitations_statement_fails(self):
        text = "Evidence used: heart rate 84. Human review is required. No category assigned."
        failures = _validate_explanation_safety(text)
        assert "MISSING_DATA_LIMITATIONS_STATEMENT" in failures

    def test_missing_provisional_or_no_category_statement_fails(self):
        text = "Evidence used: heart rate 84. Human review is required. Data is missing: pain score."
        failures = _validate_explanation_safety(text)
        assert "MISSING_PROVISIONAL_OR_NO_CATEGORY_STATEMENT" in failures

    def test_provisional_restatement_satisfies_category_check(self):
        # Restating the engine's provisional category (with provisional framing)
        # should NOT trigger the missing-statement failure, and should NOT be
        # blocked by the shared forbidden-phrase filter.
        text = (
            "Evidence used: heart rate 84. Data is missing: pain score. "
            "The deterministic rules engine produced a provisional category of "
            "Very Urgent (Orange); this is provisional, unvalidated, not the "
            "official Manchester Triage System, and not clinically approved. "
            "Human review is required."
        )
        failures = _validate_explanation_safety(text)
        assert "MISSING_PROVISIONAL_OR_NO_CATEGORY_STATEMENT" not in failures
        assert not any("CATEGORY_NAME_WITHOUT_PROVISIONAL_FRAMING" in f for f in failures)

    def test_category_name_without_provisional_framing_is_blocked(self):
        # A bare category name with NO provisional framing must still be caught.
        from app.rules.llm_safety_filter import check_forbidden_phrases
        failures = check_forbidden_phrases("The patient is Very Urgent (Orange).")
        assert any("CATEGORY_NAME_WITHOUT_PROVISIONAL_FRAMING" in f for f in failures)

    def test_forbidden_phrase_still_caught_via_shared_filter(self):
        """Confirms the explanation agent's check still includes the shared filter, not just its own additions."""
        text = self._valid_five_section_response() + " The diagnosis is sepsis."
        failures = _validate_explanation_safety(text)
        assert any("FORBIDDEN_CLINICAL_ADVICE_PHRASE" in f for f in failures)

    def test_short_answer_that_would_pass_shared_filter_alone_fails_here(self):
        """
        Demonstrates exactly why the two checks are kept separate: this text
        passes the shared filter (no forbidden phrases) but correctly fails
        the explanation agent's stricter format check, since the explanation
        agent's system prompt mandates all three statements on every reply.
        """
        text = "The heart rate is 84 bpm."
        assert check_forbidden_phrases(text) == []
        assert _validate_explanation_safety(text) != []
