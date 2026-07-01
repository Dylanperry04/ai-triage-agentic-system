"""v12 abuse protection: rate limiting + max prompt length at the agent boundary."""
import pytest

from app.security import rate_limit
from app.security.rate_limit import (
    check_rate, check_prompt_length, reset_state, record_block,
    repeated_blocks_exceeded,
)
from app.security.identity import AuthContext, ROLE_TRIAGE_NURSE
from app.security.agent_gateway import authorise_agent_call


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    reset_state()
    monkeypatch.setenv("ACCESS_AUDIT_DIR", str(tmp_path))
    yield
    reset_state()


class TestRateLimiter:
    def test_window_allows_then_blocks(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_RATE_MAX_REQUESTS", "3")
        monkeypatch.setenv("CHATBOT_RATE_WINDOW_SECONDS", "60")
        res = [check_rate("u1", now=100 + i).allowed for i in range(4)]
        assert res == [True, True, True, False]

    def test_window_slides(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_RATE_MAX_REQUESTS", "2")
        monkeypatch.setenv("CHATBOT_RATE_WINDOW_SECONDS", "10")
        assert check_rate("u2", now=0).allowed
        assert check_rate("u2", now=1).allowed
        assert not check_rate("u2", now=2).allowed       # 3rd within window blocked
        assert check_rate("u2", now=15).allowed          # window passed -> allowed

    def test_separate_users_independent(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_RATE_MAX_REQUESTS", "1")
        assert check_rate("a", now=0).allowed
        assert not check_rate("a", now=0).allowed
        assert check_rate("b", now=0).allowed            # different user unaffected


class TestPromptLength:
    def test_within_limit(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_MAX_PROMPT_CHARS", "100")
        assert check_prompt_length("short question")[0] is True

    def test_exceeds_limit(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_MAX_PROMPT_CHARS", "10")
        ok, reason = check_prompt_length("x" * 50)
        assert ok is False and "exceeds" in reason


class TestRepeatedBlocks:
    def test_threshold(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_REPEATED_BLOCK_THRESHOLD", "3")
        for _ in range(2):
            record_block("u")
        assert repeated_blocks_exceeded("u") is False
        record_block("u")
        assert repeated_blocks_exceeded("u") is True


class TestGatewayIntegration:
    def _nurse(self):
        return AuthContext(authenticated=True, user_id="nurse1", roles=[ROLE_TRIAGE_NURSE])

    def test_gateway_blocks_too_long(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_MAX_PROMPT_CHARS", "10")
        d = authorise_agent_call(self._nurse(), action="ask_chatbot",
                                 case_uid="MIMIC#1", user_text="x" * 50)
        assert d.allowed is False and d.too_long is True

    def test_gateway_rate_limits(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_RATE_MAX_REQUESTS", "2")
        monkeypatch.setenv("CHATBOT_RATE_WINDOW_SECONDS", "60")
        nurse = self._nurse()
        a = authorise_agent_call(nurse, action="ask_chatbot", case_uid="MIMIC#1", user_text="q1")
        b = authorise_agent_call(nurse, action="ask_chatbot", case_uid="MIMIC#1", user_text="q2")
        c = authorise_agent_call(nurse, action="ask_chatbot", case_uid="MIMIC#1", user_text="q3")
        assert a.allowed and b.allowed
        assert c.allowed is False and c.rate_limited is True
        assert c.retry_after_seconds > 0

    def test_clean_question_passes(self, monkeypatch):
        monkeypatch.setenv("CHATBOT_RATE_MAX_REQUESTS", "20")
        monkeypatch.setenv("CHATBOT_MAX_PROMPT_CHARS", "2000")
        d = authorise_agent_call(self._nurse(), action="ask_chatbot",
                                 case_uid="MIMIC#1", user_text="Why was this Red?")
        assert d.allowed is True
