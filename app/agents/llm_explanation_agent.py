"""
LLM Explanation Agent.

Calls Azure OpenAI to generate a clinician-facing explanation of
verified triage evidence.

WHAT THE LLM MAY DO
===================
  - Summarise the verified triage-time evidence in plain English
  - Explain which vital signs or complaint features triggered the rules engine
  - Explain the rules engine's already-computed result, INCLUDING a provisional
    Manchester-style category if the deterministic engine already produced one
    (the LLM restates it; it does not create or change it), always labelling it
    provisional, unvalidated, not the official MTS, and not clinically approved
  - Describe what data is missing and what that means for completeness
  - Explain what human review action is required and why

WHAT THE LLM MUST NEVER DO
===========================
  - CREATE, CHANGE, INFER, or OVERRIDE a triage category. The LLM must not
    invent a category, and must not alter the category the deterministic engine
    produced. It may only restate and explain an already-computed one.
  - Diagnose the patient
  - Recommend treatment or medication
  - Recommend disposition (admit, discharge, send home)
  - Invent symptoms, vital signs, or clinical facts not in the verified evidence
  - Provide clinical routing advice
  - Present any category as official Manchester Triage System or as clinically
    approved

SAFETY VALIDATION
=================
All LLM responses are post-processed through `validate_explanation_safety()`.
If any forbidden phrase is detected, the explanation is flagged as FAIL
and the clinician is informed. The flags themselves are logged for audit.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.schemas.workflow import ExplanationResult
from app.rules.llm_safety_filter import check_forbidden_phrases


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


SYSTEM_INSTRUCTIONS = """\
You are the Explanation Agent for a research prototype AI clinical triage system.

This system is NOT approved for clinical use. It is a research prototype only.

Your job is to explain the verified triage evidence provided to you, clearly and
concisely, so that a clinician can review it quickly.

STRICT RULES — you must follow these without exception:

1. You must ONLY use the verified evidence provided in this message.
2. You must NOT create, change, infer, or override a triage category yourself.
   If the deterministic rules engine has ALREADY produced a provisional
   Manchester-style category (e.g. it appears in the evidence as a
   classification status like PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW
   with a category such as "Very Urgent (Orange)"), you MAY restate and explain
   that already-computed category — but you must NOT invent one if none was
   produced, and you must NOT change the one that was.
3. Whenever you mention a category, you MUST state that it is provisional,
   unvalidated, not the official Manchester Triage System, and not clinically
   approved, and that a clinician must confirm or override it.
4. You must NOT diagnose the patient.
5. You must NOT recommend treatment, medication, or procedures.
6. You must NOT recommend admission, discharge, or any other disposition.
7. You must NOT invent any symptoms, vital signs, risk factors, or clinical facts.
8. You MUST clearly state that human clinical review is required before any action.
9. You MUST mention what data is missing or limited.
10. You MUST make clear that you did not assign or decide the category yourself;
    any category shown was produced by the deterministic rules engine and is
    provisional.

Your response must have exactly these five sections:
1. Evidence Used
2. Missing or Limited Data
3. Safety and Rules Engine Status
4. ML Risk Estimate (if available)
5. Human Review Required — Action Needed
"""


def _validate_explanation_structure(text: str) -> list[str]:
    """Structured validation of the five-section response format. Unlike a pure
    substring check, this verifies each numbered section heading is actually
    present as a section, so a response that merely mentions a keyword in passing
    does not pass as well-formed. Returns a list of structural failures."""
    failures: list[str] = []
    import re
    required_sections = [
        (1, r"(?im)^\s*1[\.\)]\s*evidence used"),
        (2, r"(?im)^\s*2[\.\)]\s*(missing|limited)"),
        (3, r"(?im)^\s*3[\.\)]\s*(safety|rules)"),
        (4, r"(?im)^\s*4[\.\)]\s*(ml|risk)"),
        (5, r"(?im)^\s*5[\.\)]\s*(human review|action)"),
    ]
    for num, pat in required_sections:
        if not re.search(pat, text):
            failures.append(f"MISSING_SECTION_{num}")
    return failures


def _validate_explanation_safety(text: str) -> list[str]:
    """
    Returns list of safety failure descriptions.
    Empty list = response passed safety checks.

    Combines the shared forbidden-phrase check (app.rules.llm_safety_filter)
    with this agent's own format-completeness checks, which are specific to
    the five-section response structure mandated by SYSTEM_INSTRUCTIONS
    above. These completeness checks are deliberately NOT in the shared
    module because they would misfire on free-form conversational replies
    from other LLM-facing agents (see app/rules/llm_safety_filter.py
    docstring for why).
    """
    failures = check_forbidden_phrases(text)
    lower = text.lower()

    if "human review" not in lower and "clinical review" not in lower:
        failures.append("MISSING_HUMAN_REVIEW_REQUIREMENT")

    if "missing" not in lower and "not available" not in lower and "not provided" not in lower:
        failures.append("MISSING_DATA_LIMITATIONS_STATEMENT")

    no_category_or_provisional_statement = (
        "no manchester triage category" in lower
        or "not assigned" in lower
        or "not assign" in lower
        or "no triage category" in lower
        or "provisional" in lower
        or "rules engine" in lower
        or "rules-engine" in lower
    )
    if not no_category_or_provisional_statement:
        failures.append("MISSING_PROVISIONAL_OR_NO_CATEGORY_STATEMENT")

    # Structured validation of the required five-section format (not just keyword
    # presence) — replaces phrase-only checking with structural checking.
    failures.extend(_validate_explanation_structure(text))

    return failures


def _redact_evidence_for_prompt(case_evidence: dict[str, Any]) -> dict[str, Any]:
    """Produce an LLM-prompt-safe copy of the evidence package:

      - replace any raw top-level case_id/stay_id with a PSEUDONYMOUS case_ref;
      - drop raw identifier keys at every depth;
      - run every string through redact_text so names/dates/long identifiers are
        scrubbed before they leave the box;
      - fail closed on unsupported object types instead of stringifying arbitrary
        Python objects into a prompt.

    Restricted MIMIC evidence must never be sent to a cloud LLM with raw
    identifiers. This is applied to EVERY prompt build.
    """
    from app.security.redaction import IDENTIFIER_KEYS, pseudonymous_case_uid, redact_text

    label = case_evidence.get("source_dataset")
    raw_id = case_evidence.get("case_id") or case_evidence.get("stay_id")

    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for key, value in obj.items():
                key_s = str(key)
                if key_s.lower() in IDENTIFIER_KEYS or key_s in {"case_id"}:
                    continue
                out[key_s] = _clean(value)
            return out
        if isinstance(obj, list):
            return [_clean(item) for item in obj]
        if isinstance(obj, tuple):
            return [_clean(item) for item in obj]
        if isinstance(obj, str):
            return redact_text(obj)
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        raise ValueError(f"Unsupported evidence object for LLM prompt: {type(obj).__name__}")

    ev = _clean(case_evidence)
    if raw_id is not None:
        ev["case_ref"] = pseudonymous_case_uid(label, raw_id)
    return ev


def _build_prompt(case_evidence: dict[str, Any],
                  clinician_question: str | None = None) -> str:
    """Build the user-turn prompt from the verified evidence package. The evidence
    is redacted (pseudonymous id, scrubbed free text, no raw identifiers) before
    it is embedded. An optional, already-SCREENED clinician question is included
    so the explanation can address what was asked, while the strict rules below
    still bound the answer."""
    safe_evidence = _redact_evidence_for_prompt(case_evidence)
    question_block = ""
    if clinician_question:
        question_block = f"""
=== CLINICIAN QUESTION (already screened; answer ONLY from the verified evidence) ===
{clinician_question}
"""
    return f"""
Please explain the following verified triage-time evidence for clinician review.

Use ONLY the verified evidence below. Do not invent any clinical information.
Do not create, change, or override a triage category. If the evidence already
contains a deterministic rules-engine category, you may restate and explain it,
clearly labelled as provisional, unvalidated, not official MTS, and not
clinically approved.

=== VERIFIED EVIDENCE ===
{safe_evidence}
{question_block}
=== REQUIRED RESPONSE FORMAT ===
1. Evidence Used
2. Missing or Limited Data
3. Safety and Rules Engine Status
4. ML Risk Estimate (if available — mark as research-grade estimate only)
5. Human Review Required — Action Needed

Remember: you must NOT assign or decide a category yourself. Any category shown
was produced by the deterministic rules engine and is provisional and
unvalidated; you must say so. You must also state that human clinical review is
required and what data is missing. If a clinician question is shown, answer it
ONLY from the verified evidence; if the evidence does not contain the answer, say
so rather than speculating.
"""


def _load_azure_config() -> dict:
    """Load Azure OpenAI config from environment. Raises if missing OR if cloud
    egress is disabled for the active profile (e.g. LOCAL_CREDENTIALED_RESEARCH,
    where credentialed evidence must not be sent to a cloud LLM by default)."""
    from app.security.identity import cloud_egress_allowed
    if not cloud_egress_allowed():
        raise RuntimeError(
            "Cloud LLM egress is disabled for the active profile "
            "(LOCAL_CREDENTIALED_RESEARCH). Credentialed evidence is not sent to a "
            "cloud LLM by default. Set both ALLOW_CLOUD_LLM_IN_LOCAL_RESEARCH=true "
            "and APPROVED_CLOUD_LLM_DATA_PROCESSING=true only after documented "
            "zero-retention/no-training/no-human-review approval."
        )
    load_dotenv(dotenv_path=ENV_PATH)
    required = [
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Azure OpenAI config missing: {missing}")
    return {
        "endpoint":   os.environ["AZURE_OPENAI_ENDPOINT"],
        "api_key":    os.environ["AZURE_OPENAI_API_KEY"],
        "deployment": os.environ["AZURE_OPENAI_DEPLOYMENT"],
        "api_version":os.environ["AZURE_OPENAI_API_VERSION"],
        "model":      os.environ.get("AZURE_OPENAI_MODEL", os.environ["AZURE_OPENAI_DEPLOYMENT"]),
    }


def result_to_dict(result: ExplanationResult) -> dict:
    """Serialise an ExplanationResult to a plain dict for API responses."""
    return result.model_dump(mode="json")


def run_llm_explanation(case_evidence: dict[str, Any],
                        clinician_question: str | None = None) -> ExplanationResult:
    """
    Call Azure OpenAI to explain verified triage evidence.

    Returns ExplanationResult with safety_failures populated if any issues found.
    On configuration failure, returns a safe default (NOT_CONFIGURED) rather
    than raising — the workflow continues without LLM explanation.
    """
    try:
        config = _load_azure_config()
    except RuntimeError as exc:
        reason = str(exc)
        return ExplanationResult(
            explanation_status="NOT_CONFIGURED",
            explanation_text=(
                f"LLM explanation is unavailable: {reason}. "
                "If credentials are missing, set AZURE_OPENAI_ENDPOINT, "
                "AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT, and "
                "AZURE_OPENAI_API_VERSION in the local .env or Azure App Settings. "
                "Human clinical review of the verified evidence is still required."
            ),
            model="not_configured",
            deployment="not_configured",
        )

    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=config["endpoint"],
            api_key=config["api_key"],
            api_version=config["api_version"],
        )

        prompt = _build_prompt(case_evidence, clinician_question)

        completion = client.chat.completions.create(
            model=config["deployment"],
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {"role": "user",   "content": prompt},
            ],
            temperature=0,      # Deterministic — no creative freedom on clinical content
            max_tokens=900,
        )

        explanation_text = completion.choices[0].message.content or ""
        safety_failures = _validate_explanation_safety(explanation_text)

        return ExplanationResult(
            explanation_status="PASS" if not safety_failures else "SAFETY_FAIL",
            explanation_text=explanation_text,
            safety_failures=safety_failures,
            clinical_use_allowed=False,
            automated_manchester_triage_allowed=False,
            manchester_category_assigned=False,
            human_review_required=True,
            model=config["model"],
            deployment=config["deployment"],
        )

    except Exception as exc:
        return ExplanationResult(
            explanation_status="ERROR",
            explanation_text=(
                f"LLM explanation failed: {type(exc).__name__}. "
                "Human clinical review of the verified evidence is still required."
            ),
            safety_failures=[f"LLM_CALL_FAILED: {type(exc).__name__}"],
            model=config.get("model", "unknown"),
            deployment=config.get("deployment", "unknown"),
        )
