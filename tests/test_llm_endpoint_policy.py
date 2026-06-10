from app.governance.llm_endpoint_policy import validate_llm_explanation_endpoint_payload


def test_llm_endpoint_policy_passes_safe_payload():
    payload = {
        "input_evidence": {
            "manchester_category": None,
            "manchester_priority": None,
            "manchester_max_wait_minutes": None,
        },
        "llm_explanation_result": {
            "explanation_status": "PASS",
        },
        "safety_failures": [],
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "manchester_category_assigned": False,
        "human_review_required": True,
        "clinical_safety_claim": "No clinical safety claim is made by this endpoint.",
    }

    checks = validate_llm_explanation_endpoint_payload(payload)

    assert checks
    assert all(check.status == "PASS" for check in checks)


def test_llm_endpoint_policy_fails_if_clinical_use_allowed():
    payload = {
        "input_evidence": {
            "manchester_category": None,
            "manchester_priority": None,
            "manchester_max_wait_minutes": None,
        },
        "llm_explanation_result": {
            "explanation_status": "PASS",
        },
        "safety_failures": [],
        "clinical_use_allowed": True,
        "automated_manchester_triage_allowed": False,
        "manchester_category_assigned": False,
        "human_review_required": True,
        "clinical_safety_claim": "No clinical safety claim is made by this endpoint.",
    }

    checks = validate_llm_explanation_endpoint_payload(payload)

    assert any(check.status == "FAIL" for check in checks)
    assert any(check.check_id == "API-LLM-003" for check in checks if check.status == "FAIL")


def test_llm_endpoint_policy_fails_if_manchester_category_assigned():
    payload = {
        "input_evidence": {
            "manchester_category": "Orange",
            "manchester_priority": 2,
            "manchester_max_wait_minutes": 10,
        },
        "llm_explanation_result": {
            "explanation_status": "PASS",
        },
        "safety_failures": [],
        "clinical_use_allowed": False,
        "automated_manchester_triage_allowed": False,
        "manchester_category_assigned": True,
        "human_review_required": True,
        "clinical_safety_claim": "No clinical safety claim is made by this endpoint.",
    }

    checks = validate_llm_explanation_endpoint_payload(payload)

    failed_ids = {check.check_id for check in checks if check.status == "FAIL"}

    assert "API-LLM-005" in failed_ids
    assert "API-LLM-008" in failed_ids
    