"""
Policy checks for the deployed LLM explanation endpoint.

These checks validate the deterministic safety wrapper around the LLM output.

They do not evaluate clinical correctness.
They do not approve clinical use.
They only confirm that the deployed endpoint preserves required governance gates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import requests


DEFAULT_LLM_ENDPOINT_PATH = "/explain/llm/30115213"


@dataclass(frozen=True)
class LLMEndpointPolicyCheck:
    check_id: str
    name: str
    status: str
    details: str


def _pass(check_id: str, name: str, details: str) -> LLMEndpointPolicyCheck:
    return LLMEndpointPolicyCheck(check_id, name, "PASS", details)


def _fail(check_id: str, name: str, details: str) -> LLMEndpointPolicyCheck:
    return LLMEndpointPolicyCheck(check_id, name, "FAIL", details)


def validate_llm_explanation_endpoint_payload(payload: dict[str, Any]) -> list[LLMEndpointPolicyCheck]:
    """
    Validates the safety wrapper returned by /explain/llm/{stay_id}.

    This function checks deterministic fields only. The LLM text is not trusted as
    the source of clinical safety status.
    """

    checks: list[LLMEndpointPolicyCheck] = []

    checks.append(
        _pass(
            "API-LLM-001",
            "LLM explanation endpoint returned JSON payload",
            "Payload was parsed successfully.",
        )
    )

    safety_failures = payload.get("safety_failures")

    checks.append(
        _pass(
            "API-LLM-002",
            "LLM endpoint safety validator returned no failures",
            "safety_failures=[]",
        )
        if safety_failures == []
        else _fail(
            "API-LLM-002",
            "LLM endpoint safety validator returned no failures",
            f"Expected safety_failures=[], got {safety_failures!r}",
        )
    )

    checks.append(
        _pass(
            "API-LLM-003",
            "LLM endpoint blocks clinical use",
            "clinical_use_allowed=false",
        )
        if payload.get("clinical_use_allowed") is False
        else _fail(
            "API-LLM-003",
            "LLM endpoint blocks clinical use",
            f"Expected clinical_use_allowed=false, got {payload.get('clinical_use_allowed')!r}",
        )
    )

    checks.append(
        _pass(
            "API-LLM-004",
            "LLM endpoint blocks automated Manchester triage",
            "automated_manchester_triage_allowed=false",
        )
        if payload.get("automated_manchester_triage_allowed") is False
        else _fail(
            "API-LLM-004",
            "LLM endpoint blocks automated Manchester triage",
            (
                "Expected automated_manchester_triage_allowed=false, "
                f"got {payload.get('automated_manchester_triage_allowed')!r}"
            ),
        )
    )

    checks.append(
        _pass(
            "API-LLM-005",
            "LLM endpoint does not assign Manchester category",
            "manchester_category_assigned=false",
        )
        if payload.get("manchester_category_assigned") is False
        else _fail(
            "API-LLM-005",
            "LLM endpoint does not assign Manchester category",
            f"Expected manchester_category_assigned=false, got {payload.get('manchester_category_assigned')!r}",
        )
    )

    checks.append(
        _pass(
            "API-LLM-006",
            "LLM endpoint requires human review",
            "human_review_required=true",
        )
        if payload.get("human_review_required") is True
        else _fail(
            "API-LLM-006",
            "LLM endpoint requires human review",
            f"Expected human_review_required=true, got {payload.get('human_review_required')!r}",
        )
    )

    clinical_safety_claim = payload.get("clinical_safety_claim")

    checks.append(
        _pass(
            "API-LLM-007",
            "LLM endpoint makes no clinical safety claim",
            str(clinical_safety_claim),
        )
        if isinstance(clinical_safety_claim, str)
        and "No clinical safety claim" in clinical_safety_claim
        else _fail(
            "API-LLM-007",
            "LLM endpoint makes no clinical safety claim",
            f"Expected explicit no-clinical-safety-claim text, got {clinical_safety_claim!r}",
        )
    )

    input_evidence = payload.get("input_evidence", {})

    checks.append(
        _pass(
            "API-LLM-008",
            "LLM endpoint preserves Manchester null outputs",
            "manchester_category=null, manchester_priority=null, manchester_max_wait_minutes=null",
        )
        if input_evidence.get("manchester_category") is None
        and input_evidence.get("manchester_priority") is None
        and input_evidence.get("manchester_max_wait_minutes") is None
        else _fail(
            "API-LLM-008",
            "LLM endpoint preserves Manchester null outputs",
            (
                "Expected category/priority/max wait to remain null, got "
                f"category={input_evidence.get('manchester_category')!r}, "
                f"priority={input_evidence.get('manchester_priority')!r}, "
                f"max_wait={input_evidence.get('manchester_max_wait_minutes')!r}"
            ),
        )
    )

    llm_result = payload.get("llm_explanation_result", {})

    checks.append(
        _pass(
            "API-LLM-009",
            "LLM explanation status passed safety wrapper",
            "llm_explanation_result.explanation_status=PASS",
        )
        if llm_result.get("explanation_status") == "PASS"
        else _fail(
            "API-LLM-009",
            "LLM explanation status passed safety wrapper",
            f"Expected PASS, got {llm_result.get('explanation_status')!r}",
        )
    )

    return checks


def check_deployed_llm_explanation_endpoint(
    base_url: str,
    path: str = DEFAULT_LLM_ENDPOINT_PATH,
    timeout_seconds: int = 60,
) -> list[LLMEndpointPolicyCheck]:
    """
    Calls the deployed LLM explanation endpoint and validates its deterministic safety wrapper.
    """

    url = f"{base_url.rstrip('/')}{path}"

    try:
        response = requests.get(url, timeout=timeout_seconds)
    except requests.RequestException as exc:
        return [
            _fail(
                "API-LLM-000",
                "LLM explanation endpoint reachable",
                f"Request failed: {exc}",
            )
        ]

    if response.status_code != 200:
        return [
            _fail(
                "API-LLM-000",
                "LLM explanation endpoint reachable",
                f"Expected status_code=200, got status_code={response.status_code}, body={response.text[:500]}",
            )
        ]

    try:
        payload = response.json()
    except ValueError as exc:
        return [
            _fail(
                "API-LLM-001",
                "LLM explanation endpoint returned JSON payload",
                f"JSON parse failed: {exc}",
            )
        ]

    checks = [
        _pass(
            "API-LLM-000",
            "LLM explanation endpoint reachable",
            f"status_code=200 path={path}",
        )
    ]

    checks.extend(validate_llm_explanation_endpoint_payload(payload))

    return checks


def checks_to_dicts(checks: list[LLMEndpointPolicyCheck]) -> list[dict[str, Any]]:
    return [asdict(check) for check in checks]