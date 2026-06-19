"""
Tests for the AutoGen clinician chat API route (app/api/chat_routes.py).

These are the first API-route-level tests in this codebase; everything else
exercises agent functions directly. Scoped to just the new chat route, since
backfilling route-level tests for every existing endpoint is a separate,
larger task than the one requested.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


class TestChatRoute:
    def test_returns_503_when_azure_not_configured(self, monkeypatch):
        for key in (
            "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(
            "app.agents.autogen_team.ENV_PATH", Path("/nonexistent/.env")
        )

        response = client.post("/chat/ask", json={"question": "Tell me about stay 1"})

        assert response.status_code == 503
        body = response.json()
        assert body["detail"]["human_review_required"] is True
        assert "not configured" in body["detail"]["reply_text"].lower()

    def test_requires_question_field(self):
        response = client.post("/chat/ask", json={})
        assert response.status_code == 422  # FastAPI/Pydantic validation error


class TestTeamExplanationRoute:
    """
    Route-level tests for /chat/team-explanation, the new four-agent
    AutoGen team endpoint, mirroring TestChatRoute's pattern exactly.
    """

    def test_returns_503_when_azure_not_configured(self, monkeypatch):
        for key in (
            "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION",
        ):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(
            "app.agents.autogen_team.ENV_PATH", Path("/nonexistent/.env")
        )

        response = client.post("/chat/team-explanation", json={"stay_id": 1})

        assert response.status_code == 503
        body = response.json()
        assert body["detail"]["human_review_required"] is True
        assert "not configured" in body["detail"]["final_explanation"].lower()

    def test_requires_stay_id_field(self):
        response = client.post("/chat/team-explanation", json={})
        assert response.status_code == 422

    def test_unsafe_team_output_returns_502(self, monkeypatch):
        """
        Confirms the route correctly surfaces a SAFETY_FAIL from the
        underlying run_team_explanation() as a 502 with the safety
        failures explicitly listed, not silently swallowed into a 200.
        """
        async def _fake_run_team_explanation(stay_id, cases_path=None):
            return {
                "status": "SAFETY_FAIL",
                "agent_turns": [{"agent": "ExplanationAgent", "text": "the patient is assigned red"}],
                "final_explanation": "the patient is assigned red",
                "safety_failures": ["FORBIDDEN_TRIAGE_ASSIGNMENT_PHRASE: 'assigned red'"],
            }

        monkeypatch.setattr(
            "app.api.chat_routes.run_team_explanation", _fake_run_team_explanation
        )
        response = client.post("/chat/team-explanation", json={"stay_id": 1})
        assert response.status_code == 502
        body = response.json()
        assert body["detail"]["human_review_required"] is True
        assert len(body["detail"]["safety_failures"]) >= 1

    def test_successful_team_run_returns_200_with_agent_turns(self, monkeypatch):
        async def _fake_run_team_explanation(stay_id, cases_path=None):
            return {
                "status": "PASS",
                "agent_turns": [
                    {"agent": "IntakeAgent", "text": "Chief complaint restated."},
                    {"agent": "ValidationAgent", "text": "No missing fields."},
                    {"agent": "SafetyReviewAgent", "text": "No category assigned. Human review required."},
                    {"agent": "ExplanationAgent", "text": "Summary. Clinician review required."},
                ],
                "final_explanation": "Summary. Clinician review required.",
                "safety_failures": [],
            }

        monkeypatch.setattr(
            "app.api.chat_routes.run_team_explanation", _fake_run_team_explanation
        )
        response = client.post("/chat/team-explanation", json={"stay_id": 1})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "PASS"
        assert len(body["agent_turns"]) == 4
        assert body["clinical_use_allowed"] is False
        assert body["human_review_required"] is True
