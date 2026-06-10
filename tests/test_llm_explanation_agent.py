from app.agents.llm_explanation_agent import (
    build_explanation_prompt,
    validate_explanation_safety,
)


def test_explanation_prompt_contains_no_manchester_assignment_instruction():
    evidence = {
        "chief_complaint": "shortness of breath",
        "missing_required_fields": ["o2sat", "sbp"],
        "governance_verdict": "NOT_READY_FOR_CLINICAL_USE",
    }

    prompt = build_explanation_prompt(evidence)

    assert "Do not assign a Manchester triage category" in prompt
    assert "Do not diagnose" in prompt
    assert "Do not recommend treatment" in prompt


def test_validate_explanation_safety_passes_safe_response():
    response = """
    Evidence used: chief complaint and governance status.
    Missing or limited data: oxygen saturation and blood pressure are missing.
    Safety/gateway status: NOT_READY_FOR_CLINICAL_USE.
    Human review requirement: human clinical review is required.
    No Manchester triage category was assigned.
    """

    failures = validate_explanation_safety(response)

    assert failures == []


def test_validate_explanation_safety_blocks_manchester_assignment():
    response = """
    This patient should be assigned Orange.
    Human review is required.
    Missing data is present.
    """

    failures = validate_explanation_safety(response)

    assert failures
    assert any("Forbidden Manchester triage assignment" in failure for failure in failures)


def test_validate_explanation_safety_blocks_treatment_advice():
    response = """
    Human review is required.
    Missing data is present.
    No Manchester triage category was assigned.
    Treat with oxygen immediately.
    """

    failures = validate_explanation_safety(response)

    assert failures
    assert any("Potential unsafe clinical advice" in failure for failure in failures)