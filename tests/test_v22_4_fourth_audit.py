"""Regression guards for the fourth audit's reproduced counterexamples.

Three of these pin holes created by the PREVIOUS round's fixes: the clause-wide
negation guard, the unbounded protected review sentence, and the review-sentence
matcher that accepted its own negation.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from app.agents.autogen_multi_agent_team import (
    MAX_EXPLANATION_CHARS,
    _is_affirmative_review_sentence,
    condense_explanation,
)
from app.rules.llm_safety_filter import check_forbidden_phrases


class TestFilterBypassesClosed:
    @pytest.mark.parametrize("text", [
        "Please give aspirin.",
        "Kindly administer fluids.",
        "The patient should start antibiotics.",
        "You should start warfarin.",
        "There is no fever, so give aspirin immediately.",
        "The patient is stable but start antibiotics now.",
        "Obs are normal; give paracetamol.",
    ])
    def test_directive_is_blocked(self, text):
        assert check_forbidden_phrases(text), f"bypass: {text!r}"

    @pytest.mark.parametrize("text", [
        "Fluids were administered before arrival.",
        "Aspirin was prescribed by the GP yesterday.",
        "This system does not diagnose or recommend treatment.",
        "No diagnosis is assigned by this system.",
        "The patient was discharged at 14:00 according to the record.",
        "The patient had been started on warfarin previously.",
        "Heart rate is 115 bpm, and blood pressure is 145/94 mmHg.",
        "No treatment was given in the department.",
        "Clinician review is required before any action.",
    ])
    def test_descriptive_text_is_not_blocked(self, text):
        assert check_forbidden_phrases(text) == [], f"false positive: {text!r}"

    def test_negation_does_not_excuse_a_directive_in_another_clause(self):
        """A sentence-wide negation guard let an unrelated 'no' clear the sentence."""
        assert check_forbidden_phrases("There is no fever, so give aspirin immediately.")
        assert check_forbidden_phrases("No rash was seen, but administer fluids.")


class TestReviewRequirementMustBeAffirmative:
    @pytest.mark.parametrize("text", [
        "Clinician review is required.",
        "Clinician review is still required.",
        "A clinician must review this before any action.",
        "This must be reviewed by a clinician.",
    ])
    def test_affirmative_accepted(self, text):
        assert _is_affirmative_review_sentence(text)

    @pytest.mark.parametrize("text", [
        "No clinician review is required.",
        "The clinician reviewed this yesterday.",
        "The clinician did not review the case.",
        "Clinician review is not required.",
    ])
    def test_negated_or_past_rejected(self, text):
        """These say the opposite of the mandated statement."""
        assert not _is_affirmative_review_sentence(text)


class TestLengthCapHasNoProtectedHole:
    def test_enormous_review_sentence_is_still_capped(self):
        huge = "Clinician review is required " + "and must be documented carefully " * 80 + "."
        out = condense_explanation(huge)
        assert len(out) <= MAX_EXPLANATION_CHARS + 1
        assert _is_affirmative_review_sentence(out), "requirement must survive in some form"

    def test_normal_review_sentence_is_preserved_verbatim(self):
        body = "The observations show an elevated heart rate and raised blood pressure. " * 8
        out = condense_explanation(body + "Clinician review is required before any action.")
        assert "clinician review is required before any action" in out.lower()
        assert len(out) <= MAX_EXPLANATION_CHARS + 1

    def test_negated_review_sentence_is_not_protected_from_truncation(self):
        """A fake review sentence must not gain the protected slot."""
        huge = "No clinician review is required " + "at all whatsoever " * 100 + "."
        assert len(condense_explanation(huge)) <= MAX_EXPLANATION_CHARS + 1


class TestModelRootIndependentOfDataRoot:
    def test_writable_data_root_does_not_move_the_model_registry(self):
        """Setting ALTER_DATA_ROOT previously broke the registry/SHA-256 check."""
        import importlib
        with tempfile.TemporaryDirectory() as tmp:
            old_data = os.environ.get("ALTER_DATA_ROOT")
            old_model = os.environ.get("ALTER_MODEL_ROOT")
            os.environ["ALTER_DATA_ROOT"] = tmp
            os.environ.pop("ALTER_MODEL_ROOT", None)
            try:
                import app.config as cfg
                importlib.reload(cfg)
                assert str(cfg.settings.processed_dir).startswith(tmp), "state must be writable"
                assert not str(cfg.settings.models_dir).startswith(tmp), (
                    "immutable model assets must not follow the writable root"
                )
                assert cfg.settings.model_registry_path.exists()
            finally:
                if old_data is None:
                    os.environ.pop("ALTER_DATA_ROOT", None)
                else:
                    os.environ["ALTER_DATA_ROOT"] = old_data
                if old_model is not None:
                    os.environ["ALTER_MODEL_ROOT"] = old_model
                importlib.reload(cfg)
