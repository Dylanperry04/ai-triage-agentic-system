"""
Shared LLM-output phrase-blocking safety checks.

This module holds the part of the safety filter that is genuinely about
output safety regardless of response format: never let an LLM-generated
response assign a Manchester triage category, or give clinical advice
(diagnosis, treatment, disposition) in those exact terms.

It deliberately does NOT include format-completeness checks like "must
mention missing data" or "must state no category assigned" -- those are
specific to the single-shot LLM Explanation Agent's mandated five-section
response structure (see llm_explanation_agent.py), and applying them to a
free-form conversational reply (e.g. the AutoGen-based clinician chat agent)
would produce constant false-positive failures on completely benign short
answers such as "the heart rate is 84 bpm", which has no missing-data
statement and no category-assignment statement to make, because it was
never asked to discuss either. A safety flag that fires constantly on benign
output trains people to ignore it, which is worse than not having one.

Each consumer (llm_explanation_agent.py, autogen_team.py) calls
`check_forbidden_phrases()` and may add its own additional, format-specific
checks on top of the result.
"""
from __future__ import annotations


# Phrases that always indicate the LLM is ASSIGNING/DECIDING a category itself
# (an action verb or a definitional "is/:"), which it must never do regardless
# of context. These remain forbidden even when provisional framing is present.
FORBIDDEN_ASSIGNMENT_PHRASES = [
    "assigned red", "assigned orange", "assigned yellow", "assigned green", "assigned blue",
    "i assign", "i am assigning", "i would assign", "i classify", "i am classifying",
    "category red", "category orange", "category yellow", "category green", "category blue",
    "triage level red", "triage level orange", "triage level yellow",
    "triage category is", "triage category:",
]

# Bare Manchester category NAMES. These appear in the deterministic engine's
# own output (e.g. ManchesterDecision.category == "Very Urgent (Orange)"), so an
# explanation that RESTATES the engine's already-computed provisional category
# will legitimately contain them. They are only a safety problem when the reply
# presents such a category WITHOUT making clear it is provisional/unvalidated.
# So they are blocked only when provisional framing is absent (see
# check_forbidden_phrases).
BARE_CATEGORY_NAME_PHRASES = [
    "immediate (red)", "very urgent (orange)", "urgent (yellow)",
    "standard (green)", "non-urgent (blue)",
]

# Words/phrases that establish the category is provisional/unvalidated rather
# than an official or LLM-made assignment. If any of these is present, a bare
# category name is treated as a permitted restatement of the engine's output.
PROVISIONAL_CONTEXT_MARKERS = [
    "provisional", "unvalidated", "not official", "not the official",
    "not clinically approved", "not approved", "rules engine", "rules-engine",
    "research ruleset", "requires clinician", "clinician must", "clinician confirm",
]

FORBIDDEN_CLINICAL_ADVICE = [
    "diagnose", "diagnosis is", "the diagnosis",
    "treat with", "administer", "give the patient",
    "discharge the patient", "send home", "safe to go home",
    "prescribe", "order a", "should receive",
]


def check_forbidden_phrases(text: str) -> list[str]:
    """
    Returns a list of safety failure descriptions for forbidden phrases found
    in `text`. Empty list = no forbidden phrases detected. This check alone
    does not mean a response is fully safe -- it only means it did not
    contain a known-dangerous phrase pattern. Callers may add further,
    format-specific checks on top.

    Bare Manchester category names (e.g. "Very Urgent (Orange)") are permitted
    ONLY when the text also contains provisional framing, because the
    deterministic engine produces those exact strings and the LLM is allowed to
    restate (never create or change) an already-computed provisional category.
    """
    failures: list[str] = []
    lower = text.lower()

    for phrase in FORBIDDEN_ASSIGNMENT_PHRASES:
        if phrase in lower:
            failures.append(f"FORBIDDEN_TRIAGE_ASSIGNMENT_PHRASE: '{phrase}'")

    provisional_context = any(m in lower for m in PROVISIONAL_CONTEXT_MARKERS)
    if not provisional_context:
        for phrase in BARE_CATEGORY_NAME_PHRASES:
            if phrase in lower:
                failures.append(
                    f"CATEGORY_NAME_WITHOUT_PROVISIONAL_FRAMING: '{phrase}'"
                )

    for phrase in FORBIDDEN_CLINICAL_ADVICE:
        if phrase in lower:
            failures.append(f"FORBIDDEN_CLINICAL_ADVICE_PHRASE: '{phrase}'")

    return failures
