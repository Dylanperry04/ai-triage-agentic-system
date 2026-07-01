"""
Priority-5 LLM-handling proofs:
  - raw stay_id / identifiers are removed from the prompt (pseudonymous ref only)
  - evidence free-text is redacted before entering the prompt
  - cloud explanation is disabled by default in LOCAL_CREDENTIALED_RESEARCH
  - a screened clinician question IS passed into the prompt
  - output safety uses STRUCTURED section validation, not phrase-only
  - stronger free-text redaction (phones/dates/names/SSN), clinical text preserved
"""
import pytest

from app.agents.llm_explanation_agent import (
    _build_prompt, _redact_evidence_for_prompt, _validate_explanation_structure,
    _validate_explanation_safety, run_llm_explanation,
)
from app.security.redaction import redact_text


class TestPromptRedaction:
    def test_raw_identifiers_removed_from_prompt(self):
        ev = {"case_id": 30000001, "stay_id": 30000001, "subject_id": 10000001,
              "source_dataset": "MIMIC-IV-ED-Full-v2.2", "chief_complaint": "chest pain"}
        prompt = _build_prompt(ev)
        assert "30000001" not in prompt
        assert "10000001" not in prompt

    def test_pseudonymous_ref_replaces_raw_id(self):
        red = _redact_evidence_for_prompt(
            {"case_id": 30000001, "source_dataset": "MIMIC-IV-ED-Full-v2.2"})
        assert "stay_id" not in red and "case_id" not in red
        assert "case_ref" in red and "~" in red["case_ref"]
        assert "30000001" not in red["case_ref"]

    def test_free_text_redacted_in_prompt(self):
        ev = {"case_id": 1, "source_dataset": "MIMIC-IV-ED-Full-v2.2",
              "chief_complaint": "chest pain, call 555-123-4567 ask for John Smith"}
        prompt = _build_prompt(ev)
        assert "555-123-4567" not in prompt
        assert "John Smith" not in prompt


class TestCloudEgressGate:
    def test_explanation_blocked_in_local_research_by_default(self, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.setenv("LOCAL_CREDENTIALED_RESEARCH", "true")
        monkeypatch.delenv("ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH", raising=False)
        for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
                  "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION"):
            monkeypatch.setenv(k, "x")
        result = run_llm_explanation({"case_id": 1, "source_dataset": "MIMIC-IV-ED-Full-v2.2"})
        # cloud egress blocked -> NOT_CONFIGURED safe default, no call made
        assert result.explanation_status == "NOT_CONFIGURED"
        assert "Cloud LLM egress is disabled" in result.explanation_text

    def test_missing_credentials_message_is_distinct(self, monkeypatch):
        monkeypatch.delenv("PATIENT_DATA_MODE", raising=False)
        monkeypatch.delenv("LOCAL_CREDENTIALED_RESEARCH", raising=False)
        for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
                  "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION"):
            monkeypatch.delenv(k, raising=False)
        result = run_llm_explanation({"case_id": 1, "source_dataset": "MIMIC-IV-ED-Full-v2.2"})
        assert result.explanation_status == "NOT_CONFIGURED"
        assert "Azure OpenAI config missing" in result.explanation_text


class TestQuestionPassedToPrompt:
    def test_screened_question_in_prompt(self):
        prompt = _build_prompt({"case_id": 1, "source_dataset": "X"},
                               clinician_question="Why is the o2sat concerning?")
        assert "Why is the o2sat concerning?" in prompt
        assert "CLINICIAN QUESTION" in prompt

    def test_no_question_no_block(self):
        prompt = _build_prompt({"case_id": 1, "source_dataset": "X"})
        assert "CLINICIAN QUESTION" not in prompt


class TestStructuredOutputValidation:
    def test_well_formed_passes_structure(self):
        text = ("1. Evidence Used\na\n2. Missing or Limited Data\nb\n"
                "3. Safety and Rules Engine Status\nc\n4. ML Risk Estimate\nd\n"
                "5. Human Review Required\ne")
        assert _validate_explanation_structure(text) == []

    def test_missing_sections_flagged(self):
        text = "Just a paragraph mentioning evidence and risk and review casually."
        fails = _validate_explanation_structure(text)
        assert len(fails) == 5  # all five sections missing

    def test_safety_check_includes_structure(self):
        # a response that hits keywords but lacks the section structure must fail
        text = ("The rules engine status is provisional. Human review required. "
                "Some data is missing. No category assigned.")
        fails = _validate_explanation_safety(text)
        assert any(f.startswith("MISSING_SECTION_") for f in fails)


class TestStrongerRedaction:
    def test_phone_redacted(self):
        assert "555-123-4567" not in redact_text("call 555-123-4567")

    def test_name_pair_redacted(self):
        assert "John Smith" not in redact_text("patient John Smith arrived")

    def test_date_and_ssn_redacted(self):
        assert "12/03/1980" not in redact_text("DOB 12/03/1980")
        assert "123-45-6789" not in redact_text("SSN 123-45-6789")

    def test_clinical_text_preserved(self):
        out = redact_text("chest pain radiating to left arm, BP 120/80, HR 88")
        assert "chest pain" in out and "radiating" in out and "left arm" in out
