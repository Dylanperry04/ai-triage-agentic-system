"""
Tests for the AutoGen clinician-chat integration (app/agents/autogen_team.py).

These tests use AutoGen's own ReplayChatCompletionClient, which is real
AutoGen test infrastructure (not something written for this project) that
lets a scripted sequence of model responses drive a real AssistantAgent.
This means the tool-calling path tested here is genuinely exercised --
the agent really does call the real Python evidence-lookup function and get
a real return value back -- it is only the *model's* responses that are
scripted, not the tool execution.

What these tests CANNOT verify, and are not claimed to verify: how a real
Azure OpenAI deployment actually responds to the system prompt in practice
(tone, whether it reliably calls the tool unprompted, whether it reliably
avoids forbidden phrasing on its own). That requires a live credential and
should be checked manually against a real deployment before relying on this
in any demo, exactly as already noted for the existing single-shot LLM
Explanation Agent, which has the same limitation and was never claimed to
be tested against a live model in this project either.
"""
import asyncio
from pathlib import Path

import pytest

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import CreateResult, ModelInfo, RequestUsage
from autogen_ext.models.replay import ReplayChatCompletionClient

from app.agents.autogen_team import (
    _build_evidence_dict,
    _make_evidence_tool,
    _validate_chat_reply_safety,
    build_chat_agent,
    load_azure_config,
    run_chat_turn,
    run_single_question,
)
from app.schemas.internal import EDTriageCase
from app.storage.jsonl_repository import read_jsonl


FIXTURES = Path(__file__).parent / "fixtures" / "sample_ktas_cases.jsonl"

_MODEL_INFO = ModelInfo(
    vision=False, function_calling=True, json_output=False,
    family="unknown", structured_output=False,
)


def _tool_call_response(name: str, arguments: str, call_id: str = "1") -> CreateResult:
    return CreateResult(
        finish_reason="function_calls",
        content=[{"id": call_id, "name": name, "arguments": arguments}],
        usage=RequestUsage(prompt_tokens=0, completion_tokens=0),
        cached=False,
    )


class TestEvidenceTool:
    def test_tool_finds_existing_case(self):
        tool = _make_evidence_tool(FIXTURES)
        result = tool(1)
        assert result["stay_id"] == 1
        assert result["chief_complaint"] == "right ocular pain"
        assert "error" not in result

    def test_tool_reports_missing_case_without_inventing_one(self):
        tool = _make_evidence_tool(FIXTURES)
        result = tool(99999)
        assert "error" in result
        assert "99999" in result["error"]
        assert "available_stay_ids_sample" in result

    def test_critical_case_evidence_includes_real_safety_flags(self):
        """
        Confirms the evidence dict for a critical case is built entirely
        from the deterministic pipeline's real output, not approximated.
        """
        tool = _make_evidence_tool(FIXTURES)
        result = tool(2)
        assert result["rules_engine_status"] == "CRITICAL_PHYSIOLOGY_FLAGGED"
        assert result["is_safe_to_present"] is False
        assert "CRITICAL_HYPOXIA_SPO2_BELOW_90" in result["rules_engine_reason_codes"]

    def test_evidence_dict_never_contains_retrospective_fields(self):
        """
        The evidence handed to the LLM must never contain KTAS_expert,
        mistriage, or other retrospective fields -- same leakage boundary
        that applies everywhere else in this project.

        This checks dict KEYS, not a flattened string of the whole object.
        A naive substring search across all serialised text would produce a
        false positive here: MLPredictionResult.model_note is a fixed,
        dataset-level disclaimer sentence ("...predicts KTAS_expert from
        public Kaggle data...") that legitimately names the prediction
        target in prose. That is not a leaked per-patient value -- it is the
        same sentence for every case, present or absent. What must actually
        never happen is one of these names appearing as a DICT KEY (which
        would mean an actual per-case retrospective value was attached).
        """
        records = read_jsonl(FIXTURES)
        case = EDTriageCase(**records[0])
        evidence = _build_evidence_dict(case)

        def _all_keys(d) -> set:
            keys = set()
            if isinstance(d, dict):
                for k, v in d.items():
                    keys.add(k)
                    keys |= _all_keys(v)
            elif isinstance(d, list):
                for item in d:
                    keys |= _all_keys(item)
            return keys

        forbidden_keys = {
            "ktas_expert", "ktas_rn", "mistriage", "mistriage_label",
            "error_group", "disposition_code", "disposition_label",
            "diagnosis_in_ed", "length_of_stay_min", "ktas_duration_min",
        }
        leaked = forbidden_keys.intersection(_all_keys(evidence))
        assert leaked == set(), f"Retrospective field(s) leaked as dict keys: {leaked}"

        # The model_note disclaimer mentioning "KTAS_expert" in prose is
        # expected and fine -- confirm that's genuinely all it is, by
        # checking no NUMBER resembling an actual KTAS_expert value (1-5)
        # is attached under any per-case prediction key other than the
        # already-reviewed predicted_ktas_class (which is the ML model's
        # OWN estimate, not the ground-truth retrospective label -- a
        # different and legitimate thing).
        assert "predicted_ktas_class" in evidence["ml_research_estimate"]


class TestChatReplySafetyFilter:
    def test_safe_reply_passes(self):
        text = "The heart rate is 84 bpm. A clinician should review this case."
        assert _validate_chat_reply_safety(text) == []

    def test_forbidden_phrase_caught(self):
        text = "Based on the evidence, the patient is assigned red. A clinician should review."
        failures = _validate_chat_reply_safety(text)
        assert any("FORBIDDEN_TRIAGE_ASSIGNMENT_PHRASE" in f for f in failures)

    def test_missing_human_review_reference_caught(self):
        text = "The heart rate is 84 bpm and the temperature is normal."
        failures = _validate_chat_reply_safety(text)
        assert "MISSING_HUMAN_REVIEW_REQUIREMENT" in failures

    def test_short_factual_answer_does_not_need_missing_data_statement(self):
        """
        Confirms the chat filter does NOT impose the explanation agent's
        five-section completeness requirements -- a short factual answer
        that mentions a clinician is exactly the kind of reply this filter
        should accept.
        """
        text = "Heart rate is 84 bpm. Please have a clinician confirm this."
        assert _validate_chat_reply_safety(text) == []


class TestScriptedAgentConversation:
    """
    Uses ReplayChatCompletionClient to drive a real AssistantAgent through a
    full conversation: the model 'decides' to call the evidence tool, the
    real tool runs and returns real data, then the model's scripted final
    reply is returned. This proves the tool-registration and execution
    machinery actually works, end to end, without a live API key.
    """

    def test_agent_calls_real_tool_and_returns_scripted_reply(self):
        client = ReplayChatCompletionClient(
            chat_completions=[
                _tool_call_response(
                    "get_verified_evidence_for_stay", '{"stay_id": 1}'
                ),
                (
                    "The chief complaint is right ocular pain. Heart rate is 84 bpm. "
                    "No Manchester category has been assigned. A clinician must review this case."
                ),
            ],
            model_info=_MODEL_INFO,
        )
        agent = build_chat_agent(client, FIXTURES)
        result = asyncio.run(run_chat_turn(agent, "Tell me about stay 1"))

        assert result["status"] == "PASS"
        assert "right ocular pain" in result["reply_text"]
        assert result["safety_failures"] == []

    def test_unsafe_scripted_reply_is_caught_by_filter(self):
        """
        Proves the post-hoc safety filter actually runs on the agent's
        output and is not bypassable just because the model produced text.
        """
        client = ReplayChatCompletionClient(
            chat_completions=[
                _tool_call_response(
                    "get_verified_evidence_for_stay", '{"stay_id": 2}'
                ),
                "Based on the symptoms, the patient is assigned red and the diagnosis is a heart attack.",
            ],
            model_info=_MODEL_INFO,
        )
        agent = build_chat_agent(client, FIXTURES)
        result = asyncio.run(run_chat_turn(agent, "Tell me about stay 2"))

        assert result["status"] == "SAFETY_FAIL"
        assert len(result["safety_failures"]) >= 2  # both the colour assignment and the diagnosis phrase

    def test_agent_cannot_get_data_for_a_case_that_does_not_exist(self):
        """
        If the model asks the tool for a nonexistent stay_id, the tool
        returns an explicit error rather than fabricated data, and that
        error is what the model sees -- it cannot be silently papered over.
        """
        client = ReplayChatCompletionClient(
            chat_completions=[
                _tool_call_response(
                    "get_verified_evidence_for_stay", '{"stay_id": 555}'
                ),
                "I could not find a case with that stay ID. Please check with a clinician for the correct ID.",
            ],
            model_info=_MODEL_INFO,
        )
        agent = build_chat_agent(client, FIXTURES)
        result = asyncio.run(run_chat_turn(agent, "Tell me about stay 555"))
        assert "could not find" in result["reply_text"].lower()


class TestGracefulDegradation:
    def test_not_configured_when_azure_env_missing(self, monkeypatch):
        for key in (
            "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION",
        ):
            monkeypatch.delenv(key, raising=False)
        # Also prevent a real .env file (if present in this checkout) from
        # supplying the values and masking the test.
        monkeypatch.setattr(
            "app.agents.autogen_team.ENV_PATH", Path("/nonexistent/.env")
        )
        assert load_azure_config() is None
        result = asyncio.run(run_single_question("Tell me about stay 1", cases_path=FIXTURES))
        assert result["status"] == "NOT_CONFIGURED"
        assert "human clinical review" in result["reply_text"].lower()

    def test_agent_exception_degrades_safely(self):
        """
        If agent.run() itself raises (e.g. a network error against a real
        Azure endpoint), run_chat_turn must return a usable dict rather than
        propagate the exception, matching the existing degrade-gracefully
        pattern used by llm_explanation_agent.py.
        """

        class _ExplodingAgent:
            async def run(self, task: str):
                raise ConnectionError("simulated network failure")

        result = asyncio.run(run_chat_turn(_ExplodingAgent(), "Tell me about stay 1"))
        assert result["status"] == "ERROR"
        assert "human clinical review" in result["reply_text"].lower()
        assert any("ConnectionError" in f for f in result["safety_failures"])
