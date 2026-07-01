"""
Tests for the AutoGen clinician chat API route (app/api/chat_routes.py).

These are the first API-route-level tests in this codebase; everything else
exercises agent functions directly. Scoped to just the new chat route, since
backfilling route-level tests for every existing endpoint is a separate,
larger task than the one requested.
"""
from fastapi.testclient import TestClient
import pytest

import os as _os
_os.environ["ALLOW_LEGACY_RAW_ID_ROUTES"] = "true"  # these test the legacy compat layer
_os.environ.pop("PATIENT_DATA_MODE", None)
import importlib
import app.main as _appmain
importlib.reload(_appmain)
app = _appmain.app


client = TestClient(app)


@pytest.fixture(autouse=True)
def _clinical_demo_role(monkeypatch):
    monkeypatch.setenv("DEMO_ROLE", "triage_nurse")


class TestChatRoute:
    def test_legacy_chat_route_is_gone(self):
        response = client.post("/chat/ask", json={"question": "Tell me about stay 1"})

        assert response.status_code == 410
        assert "retired" in response.json()["detail"].lower()

    def test_requires_question_field(self):
        response = client.post("/chat/ask", json={})
        assert response.status_code == 422  # FastAPI/Pydantic validation error


class TestTeamExplanationRoute:
    """
    Route-level tests for /chat/team-explanation, the new four-agent
    AutoGen team endpoint, mirroring TestChatRoute's pattern exactly.
    """

    def test_legacy_team_route_is_gone(self):
        response = client.post("/chat/team-explanation", json={"stay_id": 1})

        assert response.status_code == 410
        assert "retired" in response.json()["detail"].lower()

    def test_requires_stay_id_field(self):
        response = client.post("/chat/team-explanation", json={})
        assert response.status_code == 422

    def test_unsafe_team_output_still_returns_410(self, monkeypatch):
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
        assert response.status_code == 410

    def test_successful_team_run_still_returns_410(self, monkeypatch):
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
        assert response.status_code == 410


def teardown_module(module):
    """Restore the default (legacy-disabled) app state for subsequent test modules."""
    import os, importlib
    os.environ.pop("ALLOW_LEGACY_RAW_ID_ROUTES", None)
    import app.main as _am
    importlib.reload(_am)
