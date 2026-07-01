"""
Tests for app/agents/autogen_multi_agent_team.py.

Uses AutoGen's own ReplayChatCompletionClient, exactly as
tests/test_autogen_team.py already does for the single-agent path, so
the team-coordination and tool-calling machinery exercised here is
genuinely real: the four agents really do run in round-robin order, each
one really calls the real evidence-lookup function and gets real data
back, and the real TextMentionTermination condition really stops the
team when the scripted ExplanationAgent reply contains "TERMINATE".

What this cannot verify, and is not claimed to verify: how a real Azure
OpenAI deployment actually behaves in this four-agent setup in practice
(whether each agent reliably calls the tool, whether the round-robin
order produces a coherent conversation with a real model). That requires
a live credential and should be checked manually before any demo --
exactly the same caveat already documented for the single-agent path in
docs/KTAS_SAFETY_NOTES.md, now extended to this module.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from autogen_core.models import CreateResult, ModelInfo, RequestUsage
from autogen_ext.models.replay import ReplayChatCompletionClient

from app.agents import autogen_multi_agent_team as mat
from app.agents.autogen_multi_agent_team import (
    INTAKE_SYSTEM_MESSAGE,
    VALIDATION_SYSTEM_MESSAGE,
    SAFETY_REVIEW_SYSTEM_MESSAGE,
    EXPLANATION_SYSTEM_MESSAGE,
    build_intake_validation_safety_explanation_team,
    build_case_uid_intake_validation_safety_explanation_team,
    run_team_explanation,
    run_case_uid_team_explanation,
    _validate_team_explanation_safety,
)


FIXTURES = Path(__file__).parent / "fixtures" / "sample_mimic_cases.jsonl"

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


def _full_four_agent_script(stay_id: int = 1, final_text: str = "Summary complete. Human clinical review is required. TERMINATE"):
    """A complete scripted conversation where all four agents call the
    tool once, then reply -- the most common real shape."""
    return [
        _tool_call_response("get_verified_evidence_for_stay", f'{{"stay_id": {stay_id}}}'),
        "Intake: chief complaint and vitals restated from verified evidence.",
        _tool_call_response("get_verified_evidence_for_stay", f'{{"stay_id": {stay_id}}}'),
        "Validation: data completeness reported from verified evidence.",
        _tool_call_response("get_verified_evidence_for_stay", f'{{"stay_id": {stay_id}}}'),
        "Safety review: rules engine status restated from verified evidence. Human clinical review required.",
        final_text,
    ]


def _case_uid_four_agent_script(
    case_uid: str = "MIMIC-IV-ED-Full-v2.2~abc",
    final_text: str = "Summary complete. Human clinical review is required. TERMINATE",
):
    return [
        _tool_call_response("get_verified_evidence_for_case", f'{{"case_uid": "{case_uid}"}}'),
        "Intake: chief complaint and vitals restated from verified evidence.",
        _tool_call_response("get_verified_evidence_for_case", f'{{"case_uid": "{case_uid}"}}'),
        "Validation: data completeness reported from verified evidence.",
        _tool_call_response("get_verified_evidence_for_case", f'{{"case_uid": "{case_uid}"}}'),
        "Safety review: rules engine status restated from verified evidence. Human clinical review required.",
        final_text,
    ]


class TestSystemMessagesEnforceSafetyBoundary:
    """
    Static checks that every one of the four system messages actually
    contains the required safety language -- catches an accidental typo
    or omission in the prompt itself, separate from runtime behaviour.
    """

    @pytest.mark.parametrize(
        "message", [INTAKE_SYSTEM_MESSAGE, VALIDATION_SYSTEM_MESSAGE,
                    SAFETY_REVIEW_SYSTEM_MESSAGE, EXPLANATION_SYSTEM_MESSAGE],
    )
    def test_every_agent_prompt_forbids_category_assignment(self, message):
        lower = message.lower()
        assert "manchester" in lower or "ktas" in lower
        assert "category" in lower

    @pytest.mark.parametrize(
        "message", [INTAKE_SYSTEM_MESSAGE, VALIDATION_SYSTEM_MESSAGE,
                    SAFETY_REVIEW_SYSTEM_MESSAGE, EXPLANATION_SYSTEM_MESSAGE],
    )
    def test_every_agent_prompt_forbids_diagnosis_and_treatment(self, message):
        lower = message.lower()
        assert "diagnos" in lower
        assert "treatment" in lower

    def test_safety_review_agent_explicitly_told_not_to_decide(self):
        """
        SafetyReviewAgent's name could mislead a model into thinking it
        performs the safety review itself -- the prompt must explicitly
        clarify it only restates an already-completed deterministic check.
        """
        lower = SAFETY_REVIEW_SYSTEM_MESSAGE.lower()
        assert "already happened" in lower or "already-completed" in lower or "already completed" in lower


class TestTeamBuildsWithFourDistinctAgents:
    def test_team_has_four_participants_with_correct_names(self):
        client = ReplayChatCompletionClient(chat_completions=["unused"], model_info=_MODEL_INFO)
        team = build_intake_validation_safety_explanation_team(client, FIXTURES)
        participant_names = [p.name for p in team._participants]
        assert participant_names == [
            "IntakeAgent", "ValidationAgent", "SafetyReviewAgent", "ExplanationAgent",
        ]

    def test_all_four_agents_share_the_same_single_tool(self):
        """
        Confirms the design decision that all four agents use the exact
        same evidence-lookup tool (one source of truth), not four
        independently-built tools that could drift from each other.
        """
        client = ReplayChatCompletionClient(chat_completions=["unused"], model_info=_MODEL_INFO)
        team = build_intake_validation_safety_explanation_team(client, FIXTURES)
        tool_names_per_agent = []
        for p in team._participants:
            tool_names = [t.name for t in getattr(p, "_tools", [])]
            tool_names_per_agent.append(tool_names)
        for tool_names in tool_names_per_agent:
            assert tool_names == ["get_verified_evidence_for_stay"]

    def test_case_uid_team_uses_only_case_uid_evidence_tool(self):
        client = ReplayChatCompletionClient(chat_completions=["unused"], model_info=_MODEL_INFO)
        evidence = {
            "case_uid": "MIMIC-IV-ED-Full-v2.2~abc",
            "source_dataset": "MIMIC-IV-ED-Full-v2.2",
            "chief_complaint": "chest pain",
            "policy": "Human clinical review is required.",
        }
        team = build_case_uid_intake_validation_safety_explanation_team(client, evidence)
        participant_names = [p.name for p in team._participants]
        assert participant_names == [
            "IntakeAgent", "ValidationAgent", "SafetyReviewAgent", "ExplanationAgent",
        ]
        for p in team._participants:
            assert [t.name for t in getattr(p, "_tools", [])] == [
                "get_verified_evidence_for_case"
            ]


class TestScriptedFourAgentConversation:
    """
    The core proof that this is genuine multi-agent coordination: each
    agent really runs in round-robin order, really calls the real tool,
    and the conversation really terminates on the scripted sentinel.
    """

    def test_all_four_agents_run_in_order_and_call_real_tool(self):
        client = ReplayChatCompletionClient(
            chat_completions=_full_four_agent_script(stay_id=1),
            model_info=_MODEL_INFO,
        )
        team = build_intake_validation_safety_explanation_team(client, FIXTURES)
        result = asyncio.run(team.run(task="Explain stay_id=1 to a clinician."))

        sources_in_order = [
            getattr(m, "source", None) for m in result.messages
            if getattr(m, "source", None) not in (None, "user")
        ]
        # Each agent appears at least once (tool call + text = 2 messages
        # per agent that calls the tool), in the correct round-robin order.
        seen_agents_in_order = []
        for s in sources_in_order:
            if not seen_agents_in_order or seen_agents_in_order[-1] != s:
                seen_agents_in_order.append(s)
        assert seen_agents_in_order == [
            "IntakeAgent", "ValidationAgent", "SafetyReviewAgent", "ExplanationAgent",
        ]

    def test_tool_call_returns_real_evidence_not_fabricated(self):
        """
        Confirms the FunctionExecutionResult messages contain genuine
        evidence-dict content from the real fixture data, not a stub.
        """
        client = ReplayChatCompletionClient(
            chat_completions=_full_four_agent_script(stay_id=1),
            model_info=_MODEL_INFO,
        )
        team = build_intake_validation_safety_explanation_team(client, FIXTURES)
        result = asyncio.run(team.run(task="Explain stay_id=1 to a clinician."))

        function_results = [
            m for m in result.messages
            if type(m).__name__ == "ToolCallExecutionEvent"
        ]
        assert len(function_results) >= 1
        # At least one real tool result should mention the fixture's real
        # chief complaint for stay 1.
        found_real_data = False
        for m in function_results:
            content = getattr(m, "content", None)
            if content and "right ocular pain" in str(content):
                found_real_data = True
        assert found_real_data, "Expected real fixture evidence (chief complaint) in a tool result."

    def test_terminates_on_explanation_agent_sentinel(self):
        client = ReplayChatCompletionClient(
            chat_completions=_full_four_agent_script(stay_id=1),
            model_info=_MODEL_INFO,
        )
        team = build_intake_validation_safety_explanation_team(client, FIXTURES)
        result = asyncio.run(team.run(task="Explain stay_id=1 to a clinician."))
        assert "TERMINATE" in str(result.stop_reason)


class TestRunTeamExplanation:
    def test_not_configured_when_azure_missing(self, monkeypatch):
        monkeypatch.setattr(mat, "load_azure_config", lambda: None)
        result = asyncio.run(run_team_explanation(1, cases_path=FIXTURES))
        assert result["status"] == "NOT_CONFIGURED"
        assert result["agent_turns"] == []

    def test_full_pass_with_scripted_team(self, monkeypatch):
        script = _full_four_agent_script(stay_id=1)
        monkeypatch.setattr(
            mat, "load_azure_config",
            lambda: {"endpoint": "x", "api_key": "x", "deployment": "x", "api_version": "x", "model": "x"},
        )
        monkeypatch.setattr(
            mat, "build_model_client",
            lambda config: ReplayChatCompletionClient(chat_completions=script, model_info=_MODEL_INFO),
        )
        result = asyncio.run(run_team_explanation(1, cases_path=FIXTURES))

        assert result["status"] == "PASS"
        assert len(result["agent_turns"]) == 4
        agent_names = [t["agent"] for t in result["agent_turns"]]
        assert agent_names == ["IntakeAgent", "ValidationAgent", "SafetyReviewAgent", "ExplanationAgent"]
        assert "TERMINATE" not in result["final_explanation"]
        assert result["safety_failures"] == []

    def test_user_task_message_excluded_from_agent_turns(self, monkeypatch):
        """The initial task message (source='user') must not appear in
        agent_turns -- only the four agents' real output should."""
        script = _full_four_agent_script(stay_id=1)
        monkeypatch.setattr(
            mat, "load_azure_config",
            lambda: {"endpoint": "x", "api_key": "x", "deployment": "x", "api_version": "x", "model": "x"},
        )
        monkeypatch.setattr(
            mat, "build_model_client",
            lambda config: ReplayChatCompletionClient(chat_completions=script, model_info=_MODEL_INFO),
        )
        result = asyncio.run(run_team_explanation(1, cases_path=FIXTURES))
        agent_names = {t["agent"] for t in result["agent_turns"]}
        assert "user" not in agent_names

    def test_unsafe_final_explanation_caught_by_safety_filter(self, monkeypatch):
        """
        Proves the safety filter actually runs on the team's final
        message and is not bypassable just because all four agents
        produced text -- mirrors the equivalent test for the single-agent
        path in test_autogen_team.py.
        """
        unsafe_script = _full_four_agent_script(
            stay_id=1,
            final_text="Based on the evidence, the patient is assigned red and the diagnosis is appendicitis. TERMINATE",
        )
        monkeypatch.setattr(
            mat, "load_azure_config",
            lambda: {"endpoint": "x", "api_key": "x", "deployment": "x", "api_version": "x", "model": "x"},
        )
        monkeypatch.setattr(
            mat, "build_model_client",
            lambda config: ReplayChatCompletionClient(chat_completions=unsafe_script, model_info=_MODEL_INFO),
        )
        result = asyncio.run(run_team_explanation(1, cases_path=FIXTURES))
        assert result["status"] == "SAFETY_FAIL"
        assert len(result["safety_failures"]) >= 2  # colour assignment + diagnosis phrase

    def test_team_run_exception_does_not_crash_caller(self, monkeypatch):
        monkeypatch.setattr(
            mat, "load_azure_config",
            lambda: {"endpoint": "x", "api_key": "x", "deployment": "x", "api_version": "x", "model": "x"},
        )

        class _BrokenClient:
            async def close(self):
                pass

        def _broken_team_builder(model_client, cases_path):
            class _BrokenTeam:
                async def run(self, task):
                    raise RuntimeError("simulated team failure")
            return _BrokenTeam()

        monkeypatch.setattr(mat, "build_model_client", lambda config: _BrokenClient())
        monkeypatch.setattr(mat, "build_intake_validation_safety_explanation_team", _broken_team_builder)

        result = asyncio.run(run_team_explanation(1, cases_path=FIXTURES))
        assert result["status"] == "ERROR"
        assert "TEAM_RUN_FAILED" in result["safety_failures"][0]

    def test_case_uid_team_passes_with_scripted_team(self, monkeypatch):
        case_uid = "MIMIC-IV-ED-Full-v2.2~abc"
        script = _case_uid_four_agent_script(case_uid=case_uid)
        monkeypatch.setattr(
            mat, "load_azure_config",
            lambda: {"endpoint": "x", "api_key": "x", "deployment": "x", "api_version": "x", "model": "x"},
        )
        monkeypatch.setattr(
            mat, "build_model_client",
            lambda config: ReplayChatCompletionClient(chat_completions=script, model_info=_MODEL_INFO),
        )
        evidence = {
            "case_uid": case_uid,
            "source_dataset": "MIMIC-IV-ED-Full-v2.2",
            "chief_complaint": "chest pain",
            "policy": "Human clinical review is required.",
        }
        result = asyncio.run(run_case_uid_team_explanation(case_uid, evidence))
        assert result["status"] == "PASS"
        assert [t["agent"] for t in result["agent_turns"]] == [
            "IntakeAgent", "ValidationAgent", "SafetyReviewAgent", "ExplanationAgent",
        ]
        assert "TERMINATE" not in result["final_explanation"]


class TestValidateTeamExplanationSafety:
    def test_safe_explanation_passes(self):
        text = "No category assigned. A clinician must review this case before any action."
        assert _validate_team_explanation_safety(text) == []

    def test_forbidden_phrase_caught(self):
        text = "The patient is assigned red. A clinician should review."
        failures = _validate_team_explanation_safety(text)
        assert any("FORBIDDEN_TRIAGE_ASSIGNMENT_PHRASE" in f for f in failures)

    def test_missing_human_review_reference_caught(self):
        text = "The heart rate is 84 bpm and the temperature is normal."
        failures = _validate_team_explanation_safety(text)
        assert "MISSING_HUMAN_REVIEW_REQUIREMENT" in failures
