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

import re as _re


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

# ── Clinical-advice detection ───────────────────────────────────────────────
# Context-blind substrings do not work here, in BOTH directions.
#
# Too permissive: the list blocked "give the patient" but not "Give aspirin
# immediately"; "discharge the patient" but not "the patient should be
# discharged"; and nothing at all for "Start aspirin", "Stop warfarin", "Call
# cardiology", "requires admission" or "Consider giving aspirin".
#
# Too aggressive: the same list blocked "administer" inside "fluids were
# administered before arrival", "prescribe" inside "aspirin was prescribed by
# the GP yesterday", and "diagnose" inside the system's own disclaimer "this
# system does not diagnose or recommend treatment" — retrospective evidence and
# an explicit refusal are exactly what a good explanation contains.
#
# What actually distinguishes advice from description is grammar: an imperative
# or a modal recommendation aimed at future action, in the active voice, not
# negated. So detection is done per sentence with a passive/past guard and a
# negation guard.

DIRECTIVE_VERBS = (
    "give", "administer", "prescribe", "commence", "initiate", "start", "stop",
    "withhold", "call", "arrange", "refer", "admit", "discharge", "order",
    "consider", "diagnose", "treat", "transfer", "escalate to",
)

# Noun forms that follow "requires"/"needs" and denote a clinical action.
DIRECTIVE_NOUNS = (
    "admission", "discharge", "transfer", "referral", "treatment", "surgery",
    "antibiotics", "thrombolysis", "intubation",
)

# Narrow by design. Descriptive text is already excluded structurally: the
# imperative patterns match only BASE forms ("administer", "give", "start"), so
# participles ("administered", "given", "started") never match in the first
# place. This guard only catches an auxiliary immediately before a base form.
# Widening it made "the patient is stable but ..." read as passive and
# suppressed a real directive in the following clause.
_PASSIVE_OR_PAST = _re.compile(
    r"\b(was|were|is|are|been|being|had|has|have)\s+(?:\w+\s+)?$"
)
_NEGATION = _re.compile(
    r"\b(?:do(?:es)?\s+not|did\s+not|cannot|can\s?not|must\s+not|will\s+not|"
    r"never|no\s+need|without|refus\w*|unable\s+to|not\s+(?:a\s+)?|"
    r"no\s+(?:diagnosis|treatment|recommendation|advice|disposition))\b"
)

# "Please give aspirin." and "Kindly administer fluids." are imperatives with a
# politeness prefix; requiring the verb at the literal clause start let them
# through. So did "so give aspirin" after a subordinating conjunction.
_IMPERATIVE = _re.compile(
    r"(?:^|[.!?;:]\s*|,\s*(?:and|then|so|but)\s+|\bso\s+|\bthen\s+)"
    r"(?:please\s+|kindly\s+|now\s+|immediately\s+|urgently\s+)*"
    r"(" + "|".join(DIRECTIVE_VERBS) + r")\b\s+\S",
    _re.IGNORECASE,
)
# "The patient should start antibiotics" is as directive as "should be started",
# but only the past participle was listed. Cover both forms of every verb.
_MODAL = _re.compile(
    r"\b(?:should|must|needs?\s+to|ought\s+to|is\s+to|are\s+to)\s+(?:be\s+)?("
    r"given|give|administered|administer|prescribed|prescribe|discharged|discharge|"
    r"admitted|admit|treated|treat|started|start|stopped|stop|referred|refer|"
    r"transferred|transfer|commenced|commence|receive|get|have|undergo)\b",
    _re.IGNORECASE,
)
_REQUIRES_NOUN = _re.compile(
    r"\b(?:requires?|needs?)\s+(?:urgent\s+|immediate\s+|an?\s+)?("
    + "|".join(DIRECTIVE_NOUNS) + r")\b",
    _re.IGNORECASE,
)
_RECOMMEND = _re.compile(
    r"\brecommend(?:s|ed|ing)?\s+(?:that\s+)?(?:the\s+)?(?:patient\s+)?(?:be\s+)?"
    r"(discharg|admit|treat|prescrib|administer|giv|start|stop|refer)\w*",
    _re.IGNORECASE,
)
# A stated diagnosis is neither imperative nor modal -- "The diagnosis is
# appendicitis." is a flat assertion -- so structural directive detection alone
# misses it. It is still the model making a clinical determination it must not
# make, so it needs its own pattern.
_DIAGNOSIS_ASSERTION = _re.compile(
    r"\b(?:the\s+|a\s+|likely\s+|probable\s+)?diagnos(?:is|es)\s+(?:is|are|was|were)\b"
    r"|\bdiagnosis\s*:",
    _re.IGNORECASE,
)
_DISPOSITION = _re.compile(
    r"\b(safe|fit|ready|okay|ok)\s+(?:for|to)\s+(discharge|go\s+home|leave|"
    r"be\s+discharged)\b",
    _re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _clauses(sentence: str) -> list[str]:
    """Split a sentence on coordinating/subordinating boundaries.

    Detection and negation are evaluated per clause so a negation in one clause
    cannot excuse a directive in another.
    """
    parts = _re.split(
        r",\s*(?:so|but|and\s+then|then|and)\s+|;\s*|\s+(?:so|but|then)\s+"
        r"|\s+and\s+then\s+",
        sentence,
    )
    return [p.strip() for p in parts if p and p.strip()]


def _clinical_advice_failures(text: str) -> list[str]:
    """Structural, voice-aware detection of directive clinical advice."""
    failures: list[str] = []
    for sentence in _sentences(text):
        # Negation is scoped to its own CLAUSE, not the whole sentence. A
        # sentence-wide guard meant an unrelated negation anywhere disabled all
        # detection in it, so "There is no fever, so give aspirin immediately."
        # passed cleanly -- the negation belonged to the fever, not the order.
        sentence_lower = sentence.lower()
        for clause in _clauses(sentence):
            lowered = clause.lower()
            negated = bool(_NEGATION.search(lowered))
            clause_offset = sentence_lower.find(lowered)

            match = _IMPERATIVE.search(clause)
            if match:
                verb_start = match.start(1)
                # Descriptive vs directive is decided by what precedes the verb
                # in the FULL sentence ("fluids were given and started ..."),
                # because clause splitting discards exactly that context.
                absolute = (
                    clause_offset + verb_start if clause_offset >= 0 else verb_start
                )
                passive = bool(_PASSIVE_OR_PAST.search(sentence_lower[:absolute]))
                if not passive and not negated:
                    failures.append(
                        f"IMPERATIVE_CLINICAL_DIRECTIVE: '{match.group(1).lower()}'"
                    )

            for pattern, label in (
                (_MODAL, "MODAL_CLINICAL_DIRECTIVE"),
                (_REQUIRES_NOUN, "REQUIRED_CLINICAL_ACTION"),
                (_RECOMMEND, "RECOMMENDED_DISPOSITION_OR_TREATMENT"),
                (_DISPOSITION, "DISPOSITION_CLAIM"),
                (_DIAGNOSIS_ASSERTION, "STATED_DIAGNOSIS"),
            ):
                hit = pattern.search(clause)
                if hit and not negated:
                    failures.append(f"{label}: '{hit.group(0).strip().lower()}'")
    return failures


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

    failures.extend(_clinical_advice_failures(text))

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


    return failures
