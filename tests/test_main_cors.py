"""
Tests for app/main.py, specifically the CORS configuration.

Regression guard for a real issue found during a later review pass:
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, ...)
combined a wildcard origin with credentialed requests. FastAPI's
CORSMiddleware handles this combination by reflecting back whatever
Origin header the request actually sent (since the CORS spec forbids a
literal wildcard alongside credentials), which means in practice this
configuration accepted credentialed cross-origin requests from ANY
origin -- confirmed live before fixing, by sending a request with an
arbitrary made-up Origin header and observing it reflected back.

Fixed by introducing settings.cors_allowed_origins (app/config.py),
which defaults to local-development-only origins (localhost:8501) and
must be explicitly set via the CORS_ALLOWED_ORIGINS environment variable
for a real deployment.
"""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _fresh_app_client():
    """
    app.main reads settings.cors_allowed_origins at import time (the
    middleware is configured once, at module load), so a test that wants
    to see the effect of a different CORS_ALLOWED_ORIGINS environment
    variable must reload both app.config and app.main, not just import
    the already-loaded module.
    """
    import app.config
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.main)
    return TestClient(app.main.app)


class TestCorsConfiguration:
    def test_arbitrary_origin_is_not_reflected_back_by_default(self, monkeypatch):
        """
        The core regression guard: with no CORS_ALLOWED_ORIGINS set, an
        arbitrary, made-up Origin header must NOT receive a matching
        Access-Control-Allow-Origin header back. This is the exact
        behaviour that was found to be broken (every origin was
        reflected back) before the fix.
        """
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        client = _fresh_app_client()

        response = client.get(
            "/health", headers={"Origin": "https://totally-random-evil-site.example"}
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") is None

    def test_localhost_streamlit_origin_still_works_by_default(self, monkeypatch):
        """
        The default must still allow the genuine local-development
        Streamlit origin, so local `streamlit run frontend/app.py` against
        a locally-running API continues to work without any extra
        configuration.
        """
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        client = _fresh_app_client()

        response = client.get("/health", headers={"Origin": "http://localhost:8501"})
        assert response.headers.get("access-control-allow-origin") == "http://localhost:8501"

    def test_configured_deployment_origin_is_allowed_when_set(self, monkeypatch):
        """
        Confirms the environment-variable override genuinely works for a
        real deployment scenario -- setting CORS_ALLOWED_ORIGINS to a real
        deployed frontend URL allows that origin.
        """
        monkeypatch.setenv(
            "CORS_ALLOWED_ORIGINS",
            "https://my-real-deployed-app.azurewebsites.net,http://localhost:8501",
        )
        client = _fresh_app_client()

        response = client.get(
            "/health", headers={"Origin": "https://my-real-deployed-app.azurewebsites.net"}
        )
        assert (
            response.headers.get("access-control-allow-origin")
            == "https://my-real-deployed-app.azurewebsites.net"
        )

    def test_unconfigured_origin_still_rejected_even_with_override_set(self, monkeypatch):
        """
        Confirms setting CORS_ALLOWED_ORIGINS to a specific deployed
        origin does NOT also open the door to every other origin --
        only the explicitly configured ones are allowed.
        """
        monkeypatch.setenv(
            "CORS_ALLOWED_ORIGINS", "https://my-real-deployed-app.azurewebsites.net"
        )
        client = _fresh_app_client()

        response = client.get(
            "/health", headers={"Origin": "https://totally-random-evil-site.example"}
        )
        assert response.headers.get("access-control-allow-origin") is None

    def test_default_never_includes_a_literal_wildcard(self, monkeypatch):
        """
        Static check: settings.cors_allowed_origins must never default to
        a literal "*" -- that is precisely the configuration this test
        file exists to prevent from coming back.
        """
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        import app.config

        importlib.reload(app.config)
        assert "*" not in app.config.settings.cors_allowed_origins
