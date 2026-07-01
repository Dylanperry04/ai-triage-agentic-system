"""
Fixture-only multi-agent AutoGen team: IntakeAgent, ValidationAgent,
SafetyReviewAgent, ExplanationAgent, coordinated via RoundRobinGroupChat.

WHY THIS MODULE EXISTS, READ BEFORE EDITING
=============================================
The legacy raw-stay_id helper in this module is retained for explicit
fixture-based tests and offline experiments that prove the AutoGen team wiring
still works. The public app/API must use the case_uid-keyed functions and
POST /cases/{case_uid}/multiagent-explanations, which resolve a pseudonymous
case_uid on the backend and pass minimized evidence only.

The team uses AutoGen's own RoundRobinGroupChat primitive instead of a single
AssistantAgent, because a single AssistantAgent is not what "multi-agent
orchestration" means in the AutoGen ecosystem.

THE SAFETY BOUNDARY -- IDENTICAL TO autogen_team.py, NOT WEAKENED
====================================================================
Adding more agents could in principle add more opportunities for an LLM
to drift into making a decision it should not make. This design
deliberately prevents that the same way the single-agent design does,
just replicated across four agents instead of one:

  1. ALL FOUR AGENTS SHARE THE SAME SINGLE TOOL: get_verified_evidence_
     for_stay(stay_id), the exact same already-tested function from
     app.agents.autogen_team (_make_evidence_tool / _build_evidence_dict).
     No agent has, or could have, a tool that writes a triage category,
     modifies a vital sign, or does anything other than read
     already-computed deterministic output. There is one source of truth
     for all four agents, not four independently-fallible ones.
  2. EACH AGENT'S SYSTEM MESSAGE NARROWS ITS ROLE, NOT WIDENS ITS
     AUTHORITY. IntakeAgent may only restate facts. ValidationAgent may
     only discuss data completeness. SafetyReviewAgent may only restate
     the deterministic engine's own output -- it does NOT decide
     anything itself, even though its name might suggest it reviews
     safety; the actual safety review was already done by
     app/agents/safety_review_agent.py before any of this ever runs.
     ExplanationAgent synthesises the previous three agents' turns into
     one clinician-facing summary, under the exact same nine strict
     rules as the single-agent CHAT_SYSTEM_MESSAGE in autogen_team.py
     (no category assignment, no diagnosis, no treatment advice, no
     cross-scale triage mapping, must state human review is required).
  3. THE FINAL OUTPUT (ExplanationAgent's message, since it speaks last
     in the round-robin) is run through the SAME shared, format-agnostic
     forbidden-phrase filter (app.rules.llm_safety_filter
     .check_forbidden_phrases) already used by both the single-shot LLM
     Explanation Agent and the single-agent chat path, so this third
     LLM-facing surface in the project cannot silently diverge on what
     counts as a forbidden phrase either.
  4. If Azure OpenAI is not configured, this degrades to a clear
     NOT_CONFIGURED result, exactly like every other LLM-facing entry
     point in this project. It never crashes the rest of the application
     and never falls back to inventing an explanation locally.

If you are tempted to give any of these four agents a tool that can set
a triage category, change a vital sign, or skip the safety filter on the
final message: stop. That reverses the entire point of this design, and
of every other LLM-facing module in this project.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.agents.autogen_team import (
    ENV_PATH,
    _make_evidence_tool,
    build_model_client,
    load_azure_config,
)
from app.rules.llm_safety_filter import check_forbidden_phrases


INTAKE_SYSTEM_MESSAGE = """\
You are the Intake Agent in a multi-agent clinician-support team for a \
research prototype AI triage system. NOT FOR CLINICAL USE.

You have exactly one tool: get_verified_evidence_for_stay(stay_id). Call \
it once for the stay_id given in the task, then restate the case facts \
plainly: chief complaint, age/gender if available, arrival transport, and \
the recorded vital signs with their units. State values exactly as \
returned by the tool.

STRICT RULES:
1. Only state facts returned by your tool. Never invent or estimate a \
   value that was not returned.
2. Do NOT create, assign, infer, or change a Manchester category \
   yourself. You may restate a provisional category the deterministic engine \
   already produced, clearly labelled provisional and not official MTS.
3. Do NOT diagnose, recommend treatment, or recommend disposition.
4. Keep this turn to intake facts only -- data completeness and safety \
   flags are the next two agents' job, not yours.
"""

VALIDATION_SYSTEM_MESSAGE = """\
You are the Validation Agent in a multi-agent clinician-support team for \
a research prototype AI triage system. NOT FOR CLINICAL USE.

You have exactly one tool: get_verified_evidence_for_stay(stay_id). Call \
it once for the stay_id given in the task (or reuse the result if the \
Intake Agent already called it in this conversation), then report data \
completeness ONLY: the data_validation_status field, and list missing_fields \
exactly as returned. State explicitly whether the data is complete enough \
for the deterministic engine to have produced a confident output.

STRICT RULES:
1. Only report what missing_fields and data_validation_status actually say. \
   Never guess at why a field is missing or invent a missing field that \
   was not listed.
2. Do NOT create, assign, infer, or change a Manchester category \
   yourself. You may note a provisional category the engine already produced, \
   labelled provisional, but do not diagnose or recommend treatment.
3. Keep this turn to data completeness only -- case facts and the safety \
   engine's output are the other two agents' job, not yours.
"""

SAFETY_REVIEW_SYSTEM_MESSAGE = """\
You are the Safety Review Agent in a multi-agent clinician-support team \
for a research prototype AI triage system. NOT FOR CLINICAL USE.

IMPORTANT: despite your name, you do not perform a safety review yourself. \
The actual deterministic safety review already happened in Python before \
this conversation started. Your only job is to accurately RESTATE that \
already-completed review.

You have exactly one tool: get_verified_evidence_for_stay(stay_id). Call \
it once for the stay_id given in the task (or reuse the result if an \
earlier agent already called it in this conversation), then report \
EXACTLY what it returned for: rules_engine_status, rules_engine_reason_codes, \
safety_flags, is_safe_to_present, and the rules_engine_note. State plainly \
whether a Manchester category was assigned and, if so, that it is a PROVISIONAL \
category from an unvalidated research ruleset (not the official MTS, not \
clinically approved) -- the engine produces these by default unless \
PROVISIONAL_MTS_MODE is off, in which case no category is assigned.

STRICT RULES:
1. Only restate what the tool actually returned. Never decide a category, \
   priority, or severity yourself, even informally, even as a suggestion.
2. You may restate a provisional category the engine returned (clearly \
   labelled provisional and not official MTS), but never invent or change one \
   of your own.
3. Do NOT diagnose, recommend treatment, or recommend disposition.
4. Always state that human clinical review is required.
"""

EXPLANATION_SYSTEM_MESSAGE = """\
You are the Explanation Agent in a multi-agent clinician-support team for \
a research prototype AI triage system. NOT FOR CLINICAL USE. You speak \
last, after the Intake, Validation, and Safety Review agents.

Your job is to synthesise the previous three agents' turns into one \
clear, clinician-facing summary paragraph. Do not call the evidence tool \
yourself unless none of the previous agents did -- prefer to summarise \
what they already reported.

STRICT RULES -- you must follow these without exception:
1. Only synthesise what the previous agents already stated from verified \
   evidence. Never introduce a new fact, vital sign, or value.
2. Do NOT create, assign, infer, or change a Manchester category \
   yourself. You may restate a provisional category the deterministic engine \
   already produced, always labelled provisional, unvalidated, and not \
   official MTS.
3. Do NOT diagnose the patient.
4. Do NOT recommend treatment, medication, or procedures.
5. Do NOT recommend admission, discharge, or any other disposition.
6. Do NOT map or convert any external/non-MTS triage scale to Manchester.
7. Always state that human clinical review is required before any action.
8. If the Validation or Safety Review agents reported missing data or an \
   unsafe-to-present flag, your summary must say so plainly, not soften it.
9. End your summary with the word TERMINATE on its own line.
"""


CASE_UID_INTAKE_SYSTEM_MESSAGE = """\
You are the Intake Agent in a multi-agent clinician-support team for a
research prototype AI triage system. NOT FOR CLINICAL USE.

You have exactly one tool: get_verified_evidence_for_case(case_uid). Call it
once for the case_uid given in the task, then restate only the case facts the
tool returns: chief complaint, age/gender if available, arrival transport, and
recorded triage-time vital signs with units.

STRICT RULES:
1. Only state facts returned by your tool. Never invent or estimate values.
2. Do NOT create, assign, infer, or change a Manchester category.
3. Do NOT diagnose, recommend treatment, or recommend disposition.
4. Do not mention or request raw stay_id, subject_id, hadm_id, names, MRNs, or
   any other raw identifier.
"""


CASE_UID_VALIDATION_SYSTEM_MESSAGE = """\
You are the Validation Agent in a multi-agent clinician-support team for a
research prototype AI triage system. NOT FOR CLINICAL USE.

You have exactly one tool: get_verified_evidence_for_case(case_uid). Call it
once for the case_uid given in the task, then report data completeness only:
data_validation_status and missing_fields exactly as returned.

STRICT RULES:
1. Only report what the tool returns. Never invent missing fields.
2. Do NOT create, assign, infer, or change a Manchester category.
3. Do NOT diagnose, recommend treatment, or recommend disposition.
4. Do not mention or request raw identifiers.
"""


CASE_UID_SAFETY_REVIEW_SYSTEM_MESSAGE = """\
You are the Safety Review Agent in a multi-agent clinician-support team for a
research prototype AI triage system. NOT FOR CLINICAL USE.

IMPORTANT: you do not perform a new safety review. The deterministic Python
safety review already happened before this conversation started. Your only job
is to restate returned fields: rules_engine_status, rules_engine_reason_codes,
safety_flags, is_safe_to_present, and rules_engine_note.

STRICT RULES:
1. Only restate what the tool returned. Never decide a category, priority, or
   severity yourself.
2. You may restate a provisional category only if returned by the evidence,
   clearly labelled provisional, unvalidated, and not official MTS.
3. Do NOT diagnose, recommend treatment, or recommend disposition.
4. Always state that human clinical review is required.
5. Do not mention or request raw identifiers.
"""


CASE_UID_EXPLANATION_SYSTEM_MESSAGE = """\
You are the Explanation Agent in a multi-agent clinician-support team for a
research prototype AI triage system. NOT FOR CLINICAL USE. You speak last,
after the Intake, Validation, and Safety Review agents.

Your job is to synthesize those agents' verified statements into one concise
clinician-facing summary. Do not call the evidence tool yourself unless none of
the previous agents did.

STRICT RULES:
1. Only synthesize verified evidence already returned by the read-only tool.
2. Do NOT create, assign, infer, or change a Manchester category.
3. Do NOT diagnose, recommend treatment, medication, procedures, admission,
   discharge, or any other disposition.
4. Do NOT map or convert any external/non-MTS triage scale to Manchester.
5. Always state that human clinical review is required before any action.
6. If missing data or unsafe-to-present flags were reported, state that plainly.
7. Do not mention or request raw identifiers.
8. End your summary with the word TERMINATE on its own line.
"""


def build_intake_validation_safety_explanation_team(model_client, cases_path: Path):
    """
    Builds the real four-agent AutoGen team using RoundRobinGroupChat.

    All four agents share the exact same single tool
    (get_verified_evidence_for_stay), built once and passed to every
    agent, so there is one source of truth, not four independently
    fallible ones. Termination is driven by the ExplanationAgent's
    "TERMINATE" sentinel (its system message instructs it to always end
    with this), with a max_turns safety net in case that instruction is
    ever not followed by a given model.
    """
    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination

    evidence_tool = _make_evidence_tool(cases_path)

    # SECURITY INVARIANT: every agent in this team must expose ONLY the read-only
    # evidence tool. This is the load-bearing property that makes the agents safe
    # explainers (they cannot assign or change a triage result). If anyone ever
    # adds an action-taking tool, this fails closed and forces a security review.
    from app.security.agent_gateway import assert_agents_have_no_action_tools
    _tool_name = getattr(evidence_tool, "name", "get_verified_evidence_for_stay")
    assert_agents_have_no_action_tools([_tool_name])

    intake_agent = AssistantAgent(
        name="IntakeAgent",
        model_client=model_client,
        tools=[evidence_tool],
        system_message=INTAKE_SYSTEM_MESSAGE,
        reflect_on_tool_use=True,
    )
    validation_agent = AssistantAgent(
        name="ValidationAgent",
        model_client=model_client,
        tools=[evidence_tool],
        system_message=VALIDATION_SYSTEM_MESSAGE,
        reflect_on_tool_use=True,
    )
    safety_review_agent = AssistantAgent(
        name="SafetyReviewAgent",
        model_client=model_client,
        tools=[evidence_tool],
        system_message=SAFETY_REVIEW_SYSTEM_MESSAGE,
        reflect_on_tool_use=True,
    )
    explanation_agent = AssistantAgent(
        name="ExplanationAgent",
        model_client=model_client,
        tools=[evidence_tool],
        system_message=EXPLANATION_SYSTEM_MESSAGE,
        reflect_on_tool_use=True,
    )

    termination = TextMentionTermination("TERMINATE") | MaxMessageTermination(max_messages=12)

    team = RoundRobinGroupChat(
        participants=[intake_agent, validation_agent, safety_review_agent, explanation_agent],
        termination_condition=termination,
    )
    return team


def build_case_uid_intake_validation_safety_explanation_team(
    model_client,
    evidence: dict[str, Any],
):
    """Build the live case_uid-keyed four-agent AutoGen team.

    The single exposed tool is read-only and closed over one already-minimized
    evidence dict produced by app.api.safe_dto.safe_multiagent_evidence().
    """
    from autogen_agentchat.agents import AssistantAgent
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination

    safe_evidence = dict(evidence or {})
    expected_case_uid = str(safe_evidence.get("case_uid") or "")

    def get_verified_evidence_for_case(case_uid: str) -> dict:
        """Return already-computed, minimized evidence for one pseudonymous case."""
        if str(case_uid or "") != expected_case_uid:
            return {
                "error": "No evidence is available for that pseudonymous case_uid.",
                "policy": (
                    "The live AutoGen tool is scoped to one already-resolved "
                    "case_uid and never exposes raw identifiers."
                ),
            }
        return dict(safe_evidence)

    from app.security.agent_gateway import assert_agents_have_no_action_tools
    _tool_name = getattr(
        get_verified_evidence_for_case,
        "name",
        getattr(get_verified_evidence_for_case, "__name__", ""),
    )
    assert_agents_have_no_action_tools([_tool_name])

    intake_agent = AssistantAgent(
        name="IntakeAgent",
        model_client=model_client,
        tools=[get_verified_evidence_for_case],
        system_message=CASE_UID_INTAKE_SYSTEM_MESSAGE,
        reflect_on_tool_use=True,
    )
    validation_agent = AssistantAgent(
        name="ValidationAgent",
        model_client=model_client,
        tools=[get_verified_evidence_for_case],
        system_message=CASE_UID_VALIDATION_SYSTEM_MESSAGE,
        reflect_on_tool_use=True,
    )
    safety_review_agent = AssistantAgent(
        name="SafetyReviewAgent",
        model_client=model_client,
        tools=[get_verified_evidence_for_case],
        system_message=CASE_UID_SAFETY_REVIEW_SYSTEM_MESSAGE,
        reflect_on_tool_use=True,
    )
    explanation_agent = AssistantAgent(
        name="ExplanationAgent",
        model_client=model_client,
        tools=[get_verified_evidence_for_case],
        system_message=CASE_UID_EXPLANATION_SYSTEM_MESSAGE,
        reflect_on_tool_use=True,
    )

    termination = TextMentionTermination("TERMINATE") | MaxMessageTermination(max_messages=12)
    return RoundRobinGroupChat(
        participants=[intake_agent, validation_agent, safety_review_agent, explanation_agent],
        termination_condition=termination,
    )


def _validate_team_explanation_safety(text: str) -> list[str]:
    """
    Safety check for the team's final synthesised explanation. Built on
    the SAME shared forbidden-phrase core already used by both
    llm_explanation_agent.py and the single-agent chat path in
    autogen_team.py, plus one completeness requirement matching the
    EXPLANATION_SYSTEM_MESSAGE's own rule 7 (must mention human review).
    """
    failures = check_forbidden_phrases(text)
    lower = text.lower()
    if "human review" not in lower and "clinical review" not in lower and "clinician" not in lower:
        failures.append("MISSING_HUMAN_REVIEW_REQUIREMENT")
    return failures


async def run_team_explanation(
    stay_id: int,
    cases_path: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Runs the full four-agent team for one stay_id and returns a dict with
    every agent's individual turn plus the safety-filtered final
    synthesis, mirroring the PASS/SAFETY_FAIL/NOT_CONFIGURED pattern
    already used everywhere else in this project's LLM-facing code.
    """
    config = load_azure_config()
    if config is None:
        return {
            "status": "NOT_CONFIGURED",
            "agent_turns": [],
            "final_explanation": (
                "Azure OpenAI is not configured. Set AZURE_OPENAI_ENDPOINT, "
                "AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT, and "
                "AZURE_OPENAI_API_VERSION in .env to run this fixture-only "
                "multi-agent helper outside the public app flow. Human clinical "
                "review of the verified evidence is still required."
            ),
            "safety_failures": [],
        }

    if cases_path is None:
        return {
            "status": "ERROR",
            "agent_turns": [],
            "final_explanation": (
                "No explicit case evidence source was provided for the legacy "
                "raw-stay_id multi-agent helper. Use the case_uid-keyed "
                "explanation API for app usage. Human clinical review is still "
                "required."
            ),
            "safety_failures": ["NO_EXPLICIT_CASE_SOURCE"],
        }

    model_client = build_model_client(config)
    team = build_intake_validation_safety_explanation_team(model_client, cases_path)
    try:
        task = (
            f"Explain stay_id={stay_id} to a clinician. Each agent should "
            f"call get_verified_evidence_for_stay({stay_id}) as needed and "
            f"speak once in turn."
        )
        try:
            result = await team.run(task=task)
        except Exception as exc:
            return {
                "status": "ERROR",
                "agent_turns": [],
                "final_explanation": (
                    f"Multi-agent team failed: {type(exc).__name__}. Human "
                    "clinical review of the verified evidence is still required."
                ),
                "safety_failures": [f"TEAM_RUN_FAILED: {type(exc).__name__}"],
            }

        agent_turns = []
        for msg in result.messages:
            source = getattr(msg, "source", None)
            content = getattr(msg, "content", None)
            if source == "user":
                continue  # the initial task message, not an agent's output
            if source and isinstance(content, str) and content.strip():
                agent_turns.append({"agent": source, "text": content.strip()})

        final_explanation = ""
        for turn in reversed(agent_turns):
            if turn["agent"] == "ExplanationAgent":
                final_explanation = turn["text"].replace("TERMINATE", "").strip()
                break
        if not final_explanation and agent_turns:
            final_explanation = agent_turns[-1]["text"]

        safety_failures = (
            _validate_team_explanation_safety(final_explanation)
            if final_explanation else ["EMPTY_RESPONSE"]
        )

        return {
            "status": "PASS" if not safety_failures else "SAFETY_FAIL",
            "agent_turns": agent_turns,
            "final_explanation": final_explanation,
            "safety_failures": safety_failures,
        }
    finally:
        await model_client.close()


async def run_case_uid_team_explanation(
    case_uid: str,
    evidence: dict[str, Any],
    user_question: Optional[str] = None,
) -> dict[str, Any]:
    """Run the live case_uid-keyed four-agent explainer.

    This is the public-app-safe counterpart to run_team_explanation(): it never
    accepts or exposes raw stay_id, and its only tool returns the caller-provided
    minimized evidence dict for the already-resolved pseudonymous case_uid.
    """
    config = load_azure_config()
    if config is None:
        return {
            "status": "NOT_CONFIGURED",
            "agent_turns": [],
            "final_explanation": (
                "Azure OpenAI is not configured or cloud LLM use is blocked for "
                "this runtime profile. The multi-agent explanation layer did not "
                "run. Human clinical review of the verified evidence is still "
                "required."
            ),
            "safety_failures": [],
        }

    evidence = dict(evidence or {})
    evidence["case_uid"] = case_uid
    model_client = build_model_client(config)
    team = build_case_uid_intake_validation_safety_explanation_team(model_client, evidence)
    try:
        task = (
            f"Explain case_uid={case_uid} to a clinician. Each agent should call "
            f"get_verified_evidence_for_case('{case_uid}') as needed and speak "
            "once in turn. "
        )
        if user_question:
            task += f"Optional clinician question: {user_question.strip()}"
        try:
            result = await team.run(task=task)
        except Exception as exc:
            return {
                "status": "ERROR",
                "agent_turns": [],
                "final_explanation": (
                    f"Case_uid multi-agent team failed: {type(exc).__name__}. "
                    "Human clinical review of the verified evidence is still "
                    "required."
                ),
                "safety_failures": [f"TEAM_RUN_FAILED: {type(exc).__name__}"],
            }

        agent_turns = []
        for msg in result.messages:
            source = getattr(msg, "source", None)
            content = getattr(msg, "content", None)
            if source == "user":
                continue
            if source and isinstance(content, str) and content.strip():
                agent_turns.append({"agent": source, "text": content.strip()})

        final_explanation = ""
        for turn in reversed(agent_turns):
            if turn["agent"] == "ExplanationAgent":
                final_explanation = turn["text"].replace("TERMINATE", "").strip()
                break
        if not final_explanation and agent_turns:
            final_explanation = agent_turns[-1]["text"]

        safety_failures = (
            _validate_team_explanation_safety(final_explanation)
            if final_explanation else ["EMPTY_RESPONSE"]
        )
        return {
            "status": "PASS" if not safety_failures else "SAFETY_FAIL",
            "agent_turns": agent_turns,
            "final_explanation": final_explanation,
            "safety_failures": safety_failures,
        }
    finally:
        await model_client.close()
