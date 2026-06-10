"""
LLM Explanation Agent for the AI Triage Agentic System.

This agent uses Azure OpenAI only to explain verified evidence.

It does not approve clinical use.
It does not assign Manchester triage categories.
It does not diagnose.
It does not recommend treatment.
It does not use real patient data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


SYSTEM_INSTRUCTIONS = """
You are an Explanation Agent for a research prototype AI triage system.

This system is not for clinical use.

You must only explain verified evidence that is provided to you.

You must not assign a Manchester triage category.

You must not assign Red, Orange, Yellow, Green, or Blue triage levels.

You must not diagnose the patient.

You must not recommend treatment.

You must not provide emergency department routing advice.

You must not invent symptoms, vital signs, diagnoses, risk factors, or clinical facts.

If required evidence is missing, say that human clinical review is required.

If asked to make a final triage decision, refuse and explain that final Manchester-style
triage requires a clinician-approved deterministic ruleset and human review.

Always include:
1. Evidence used.
2. Missing or limited data.
3. Safety/gateway status.
4. Human review requirement.
5. A statement that no Manchester triage category was assigned.
"""


FORBIDDEN_ASSIGNMENT_PHRASES = [
    "assigned red",
    "assigned orange",
    "assigned yellow",
    "assigned green",
    "assigned blue",
    "category red",
    "category orange",
    "category yellow",
    "category green",
    "category blue",
    "triage level red",
    "triage level orange",
    "triage level yellow",
    "triage level green",
    "triage level blue",
    "immediate (red)",
    "very urgent (orange)",
    "urgent (yellow)",
    "standard (green)",
    "non-urgent (blue)",
]


UNSAFE_CLINICAL_ADVICE_PHRASES = [
    "diagnosis is",
    "diagnose",
    "treat with",
    "administer",
    "give the patient",
    "discharge",
    "send home",
    "safe to go home",
    "prescribe",
]


@dataclass(frozen=True)
class LLMExplanationResult:
    explanation_status: str
    model: str
    deployment: str
    explanation_text: str
    safety_failures: list[str]
    clinical_use_allowed: bool
    automated_manchester_triage_allowed: bool
    manchester_category_assigned: bool
    human_review_required: bool


def load_azure_openai_config() -> dict[str, str]:
    load_dotenv(dotenv_path=ENV_PATH)

    required = [
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_MODEL",
    ]

    missing = [name for name in required if not os.getenv(name)]

    if missing:
        raise RuntimeError(f"Missing Azure OpenAI environment variables: {missing}")

    return {
        "endpoint": os.environ["AZURE_OPENAI_ENDPOINT"],
        "api_key": os.environ["AZURE_OPENAI_API_KEY"],
        "deployment": os.environ["AZURE_OPENAI_DEPLOYMENT"],
        "model": os.environ["AZURE_OPENAI_MODEL"],
    }


def build_explanation_prompt(case_evidence: dict[str, Any]) -> str:
    return f"""
Explain the following verified AI triage prototype evidence for human review.

Use only the evidence below.

Do not assign a Manchester triage category.
Do not diagnose.
Do not recommend treatment.
Do not provide clinical routing advice.

Verified evidence:
{case_evidence}

Required output:
1. Evidence used.
2. Missing or limited data.
3. Safety/gateway status.
4. Human review requirement.
5. Statement that no Manchester triage category was assigned.
"""


def validate_explanation_safety(response_text: str) -> list[str]:
    failures: list[str] = []
    lower = response_text.lower()

    for phrase in FORBIDDEN_ASSIGNMENT_PHRASES:
        if phrase in lower:
            failures.append(f"Forbidden Manchester triage assignment phrase detected: {phrase}")

    for phrase in UNSAFE_CLINICAL_ADVICE_PHRASES:
        if phrase in lower:
            failures.append(f"Potential unsafe clinical advice detected: {phrase}")

    if "human review" not in lower and "clinical review" not in lower:
        failures.append("Explanation did not clearly require human/clinical review.")

    if "missing" not in lower and "limited data" not in lower:
        failures.append("Explanation did not clearly mention missing or limited data.")

    no_category_statement_present = (
        "no manchester triage category" in lower
        or "no triage category" in lower
        or "not assign" in lower
        or "not assigned" in lower
    )

    if not no_category_statement_present:
        failures.append("Explanation did not clearly state that no Manchester triage category was assigned.")

    return failures


def run_llm_explanation(case_evidence: dict[str, Any]) -> LLMExplanationResult:
    """
    Calls Azure OpenAI to explain verified evidence.

    The OpenAI import is inside this function so the main FastAPI deployment does
    not require OpenAI unless this branch/environment explicitly uses it.
    """

    from openai import OpenAI

    config = load_azure_openai_config()

    client = OpenAI(
        base_url=config["endpoint"],
        api_key=config["api_key"],
    )

    prompt = build_explanation_prompt(case_evidence)

    completion = client.chat.completions.create(
        model=config["deployment"],
        messages=[
            {
                "role": "system",
                "content": SYSTEM_INSTRUCTIONS,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0,
        max_tokens=800,
    )

    explanation_text = completion.choices[0].message.content or ""
    safety_failures = validate_explanation_safety(explanation_text)

    return LLMExplanationResult(
        explanation_status="PASS" if not safety_failures else "FAIL",
        model=config["model"],
        deployment=config["deployment"],
        explanation_text=explanation_text,
        safety_failures=safety_failures,
        clinical_use_allowed=False,
        automated_manchester_triage_allowed=False,
        manchester_category_assigned=False,
        human_review_required=True,
    )


def result_to_dict(result: LLMExplanationResult) -> dict[str, Any]:
    return asdict(result)