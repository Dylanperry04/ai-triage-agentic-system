from fastapi.testclient import TestClient

from app.api import explanation_routes
from app.agents.llm_explanation_agent import LLMExplanationResult
from app.main import app


client = TestClient(app)


def test_llm_explanation_endpoint_returns_safety_wrapper(monkeypatch):
    def fake_run_llm_explanation(case_evidence):
        return LLMExplanationResult(
            explanation_status="PASS",
            model="test-model",
            deployment="test-deployment",
            explanation_text=(
                "Evidence used: public-demo evidence. "
                "Missing or limited data: vital signs are missing. "
                "Safety/gateway status: NOT_READY_FOR_CLINICAL_USE. "
                "Human review requirement: human review is required. "
                "No Manchester triage category was assigned."
            ),
            safety_failures=[],
            clinical_use_allowed=False,
            automated_manchester_triage_allowed=False,
            manchester_category_assigned=False,
            human_review_required=True,
        )

    monkeypatch.setattr(explanation_routes, "run_llm_explanation", fake_run_llm_explanation)

    response = client.get("/explain/llm/30115213")

    assert response.status_code == 200

    payload = response.json()

    assert payload["clinical_use_allowed"] is False
    assert payload["automated_manchester_triage_allowed"] is False
    assert payload["manchester_category_assigned"] is False
    assert payload["human_review_required"] is True
    assert payload["safety_failures"] == []
    assert "No Manchester triage category was assigned" in payload["llm_explanation"]


def test_llm_explanation_endpoint_blocks_failed_safety_validation(monkeypatch):
    def fake_run_llm_explanation(case_evidence):
        return LLMExplanationResult(
            explanation_status="FAIL",
            model="test-model",
            deployment="test-deployment",
            explanation_text="This patient should be assigned Orange.",
            safety_failures=[
                "Forbidden Manchester triage assignment phrase detected: assigned orange"
            ],
            clinical_use_allowed=False,
            automated_manchester_triage_allowed=False,
            manchester_category_assigned=False,
            human_review_required=True,
        )

    monkeypatch.setattr(explanation_routes, "run_llm_explanation", fake_run_llm_explanation)

    response = client.get("/explain/llm/30115213")

    assert response.status_code == 502

    detail = response.json()["detail"]

    assert detail["clinical_use_allowed"] is False
    assert detail["automated_manchester_triage_allowed"] is False
    assert detail["manchester_category_assigned"] is False
    assert detail["human_review_required"] is True
    assert detail["llm_explanation"] is None
    assert detail["safety_failures"]


def test_llm_explanation_endpoint_returns_404_for_unknown_stay_id(monkeypatch):
    def fake_run_llm_explanation(case_evidence):
        raise AssertionError("LLM should not be called for unknown stay_id")

    monkeypatch.setattr(explanation_routes, "run_llm_explanation", fake_run_llm_explanation)

    response = client.get("/explain/llm/999999999")

    assert response.status_code == 404