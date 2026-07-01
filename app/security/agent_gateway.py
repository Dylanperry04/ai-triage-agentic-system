"""
Agent Security Gateway — the single boundary chokepoint for the agent system.

DESIGN RATIONALE (why a boundary gateway, not an inter-agent layer):
The four agents (DataValidation, ClinicalContext, SafetyReview, Explanation) are
read-only EXPLAINERS. They share one tool, get_verified_evidence_for_stay, which
cannot write a triage result, call out, or persist anything. The ML model decides
the acuity BEFORE any agent runs; nothing an agent says can change it. So the real
attack surface is the BOUNDARY into and out of the agent system, not the messages
between agents. This gateway sits at that boundary and enforces:

  1. AUTHORISATION  — the caller's role must permit asking the chatbot / agents.
  2. INPUT SCREENING — screen the user's question for prompt-injection patterns.
  3. EVIDENCE MINIMISATION — only the fields needed to explain are passed in
     (data minimisation; nothing identifiable beyond the pseudonymous case_uid).
  4. NO-ACTION INVARIANT — assert the agents expose only the read-only evidence
     tool (fails closed if anyone ever adds an action-taking tool).
  5. OUTPUT SAFETY — run the deterministic forbidden-phrase filter on output
     (reused from llm_safety_filter); block anything that assigns/changes a result.
  6. AUDIT — every agent call (allowed, denied, or blocked) is logged.

If the architecture ever gives an agent an action-taking tool, the no-action
invariant here fails and forces a security review — which is the proportionate
version of "a security layer between agents".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.security import authz
from app.security.access_audit import record_access


# ── Prompt-injection screening (defence-in-depth on inputs) ─────────────────
# These patterns are heuristics, not a guarantee. They catch common injection
# attempts in a clinician's free-text question. The deterministic OUTPUT filter
# remains the primary safety control; this reduces the chance of a manipulated
# prompt reaching the model at all.
_INJECTION_PATTERNS = [
    r"ignore (all |the |your )?(previous|prior|above) (instructions|prompt)",
    r"disregard (your |the )?(instructions|system prompt|rules)",
    r"you are now ",
    r"new (system )?(instructions|prompt)\s*:",
    r"act as (an? )?(?!triage)",          # "act as <something other than triage>"
    r"pretend (to be|you are)",
    r"reveal (your |the )?(system )?(prompt|instructions)",
    r"override (the |your )?(safety|rules|filter)",
    r"assign (the )?(category|acuity|triage)",   # trying to make the agent ASSIGN
    r"change (the )?(category|acuity|result|prediction)",
    r"\bjailbreak\b",
    r"developer mode",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def screen_user_input(text: str) -> Dict[str, Any]:
    """Return {'flagged': bool, 'patterns': [...]} for a user question."""
    hits = [rx.pattern for rx in _INJECTION_RE if rx.search(text or "")]
    return {"flagged": bool(hits), "patterns": hits}


# ── Evidence minimisation ───────────────────────────────────────────────────
# The fields an explainer agent legitimately needs. Anything not on this list is
# dropped before the evidence reaches the agents (data minimisation). The
# pseudonymous case_uid is allowed. The legacy fixture-only AutoGen helper still
# uses stay_id internally for tests/offline experiments, but the public app path
# passes case_uid-only evidence.
_ALLOWED_EVIDENCE_FIELDS = {
    "case_uid", "stay_id", "source_dataset", "chief_complaint", "age", "gender",
    "arrival_transport", "triage_vitals", "data_validation_status",
    "missing_fields", "rules_engine_status", "rules_engine_reason_codes",
    "rules_engine_note", "safety_flags", "is_safe_to_present",
    "ml_research_estimate", "final_acuity_assessment", "followup_comparison",
    "policy",
}


def minimise_evidence(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the allowed explainer fields."""
    return {k: v for k, v in (evidence or {}).items() if k in _ALLOWED_EVIDENCE_FIELDS}


# ── No-action invariant ─────────────────────────────────────────────────────
# The single read-only tool the agents are permitted to have. The invariant is:
# every agent's tool set is a subset of these read-only evidence tools. The
# case_uid variant is the live app path; the stay_id variant is retained only for
# fixture/offline AutoGen tests.
ALLOWED_AGENT_TOOL_NAMES = {
    "get_verified_evidence_for_case",
    "get_verified_evidence_for_stay",
}


def assert_agents_have_no_action_tools(tool_names: List[str]) -> None:
    """Raise if any agent tool is not the allowed read-only evidence tool."""
    extra = set(tool_names) - ALLOWED_AGENT_TOOL_NAMES
    if extra:
        raise AgentSecurityError(
            f"Agent security invariant VIOLATED: agents expose non-read-only "
            f"tool(s) {sorted(extra)}. Agents must remain read-only explainers; "
            "they must never be able to assign or change a triage result."
        )


class AgentSecurityError(PermissionError):
    """Raised when an agent-boundary security check fails."""


# ── The gateway ─────────────────────────────────────────────────────────────
@dataclass
class GatewayDecision:
    allowed: bool
    reason: str = ""
    input_flagged: bool = False
    input_patterns: List[str] = field(default_factory=list)
    minimised_evidence: Optional[Dict[str, Any]] = None
    rate_limited: bool = False
    retry_after_seconds: int = 0
    too_long: bool = False


def authorise_agent_call(
    ctx, *, action: str, case_uid: Optional[str], user_text: str = "",
    evidence: Optional[Dict[str, Any]] = None, page: Optional[str] = None,
) -> GatewayDecision:
    """
    The pre-call boundary check. Returns a GatewayDecision. Callers MUST refuse
    to run the agents if `allowed` is False. Every outcome is audit-logged.

    - Authorisation: requires can_ask_chatbot.
    - Input screening: flags prompt-injection patterns (blocks on flag).
    - Evidence minimisation: returns the minimised evidence to pass to agents.
    """
    # 1. Authorisation.
    if not authz.has_permission(ctx, authz.PERM_ASK_CHATBOT):
        record_access(action=action, decision="DENIED", ctx=ctx, page=page,
                      case_uid=case_uid, permission=authz.PERM_ASK_CHATBOT,
                      detail="role lacks can_ask_chatbot")
        return GatewayDecision(allowed=False, reason="not_authorised")

    # 2. Input-size limit (cheap; before anything expensive).
    from app.security import rate_limit
    ok_len, len_reason = rate_limit.check_prompt_length(user_text)
    if not ok_len:
        rate_limit.record_block(getattr(ctx, "user_id", None))
        record_access(action=action, decision="BLOCKED", ctx=ctx, page=page,
                      case_uid=case_uid, permission=authz.PERM_ASK_CHATBOT,
                      detail=len_reason)
        return GatewayDecision(allowed=False, reason="too_long", too_long=True)

    # 3. Per-user rate limit (abuse / cost / DoS protection).
    rl = rate_limit.check_rate(getattr(ctx, "user_id", None))
    if not rl.allowed:
        record_access(action=action, decision="BLOCKED", ctx=ctx, page=page,
                      case_uid=case_uid, permission=authz.PERM_ASK_CHATBOT,
                      detail=rl.reason)
        return GatewayDecision(allowed=False, reason="rate_limited",
                               rate_limited=True, retry_after_seconds=rl.retry_after_seconds)

    # 4. Input screening (prompt injection).
    screen = screen_user_input(user_text)
    if screen["flagged"]:
        rate_limit.record_block(getattr(ctx, "user_id", None))
        record_access(action=action, decision="BLOCKED", ctx=ctx, page=page,
                      case_uid=case_uid, permission=authz.PERM_ASK_CHATBOT,
                      detail=f"prompt_injection_patterns={screen['patterns']}")
        return GatewayDecision(allowed=False, reason="input_flagged",
                               input_flagged=True, input_patterns=screen["patterns"])

    # 5. Evidence minimisation.
    minimised = minimise_evidence(evidence) if evidence is not None else None

    record_access(action=action, decision="ALLOWED", ctx=ctx, page=page,
                  case_uid=case_uid, permission=authz.PERM_ASK_CHATBOT)
    return GatewayDecision(allowed=True, reason="ok", minimised_evidence=minimised)


def screen_agent_output(text: str) -> Dict[str, Any]:
    """Post-call boundary check: run the deterministic forbidden-phrase filter
    (the existing primary safety control). Returns {'safe': bool, 'failures': [...]}."""
    from app.rules.llm_safety_filter import check_forbidden_phrases
    failures = check_forbidden_phrases(text or "")
    return {"safe": not failures, "failures": failures}
