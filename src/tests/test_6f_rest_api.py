"""
Tests for Phase 6F — REST API real implementation.

6F.1  POST /api/v1/execute reaches OrchestratorAgent (replaces stub).
6F.2  conversation_id maintains history across calls.
"""
from __future__ import annotations

import os
import sys
import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SECRET_KEY", "CI_Test_S3cret_Key_64chars_long_ABCDEFGHIJK!@#$%^&*()")
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("DEBUG", "true")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_infra():
    """Return a minimal mock AgentInfrastructure."""
    infra = MagicMock()
    infra.skill_registry = MagicMock()
    infra.skill_executor = MagicMock()
    infra.api_client = MagicMock()
    infra.audit_log = MagicMock()
    return infra


def _make_agent_result(text: str = "hello") -> MagicMock:
    result = MagicMock()
    result.output = text
    result.workflow_id = "wf-test-001"
    return result


def _make_key_info(
    user_id: str = "user123",
    provider_override: str | None = None,
    model_override: str | None = None,
) -> Dict[str, Any]:
    return {
        "key_id": "key-abc",
        "user_id": user_id,
        "role": "user",
        "scopes": "read,write",
        "name": "test key",
        "provider_override": provider_override,
        "model_override": model_override,
    }


# ---------------------------------------------------------------------------
# 6F.1 — Real orchestrator is invoked
# ---------------------------------------------------------------------------

class TestExecuteEndpoint6F1:
    """6F.1: POST /api/v1/execute reaches OrchestratorAgent."""

    def _make_request(self, action: str, parameters: dict | None = None,
                      conversation_id: str | None = None):
        """Create a mock AgentRequest."""
        from api.agent_routes import AgentRequest
        return AgentRequest(
            action=action,
            parameters=parameters or {},
            conversation_id=conversation_id,
        )

    def test_execute_calls_orchestrator_and_returns_output(self):
        """orchestrator.run() is awaited; result.output is in the response."""
        from api.agent_routes import execute_agent_request

        infra = _make_infra()
        key_info = _make_key_info()
        mock_result = _make_agent_result("orchestrated response")

        mock_orch = AsyncMock()
        mock_orch.run = AsyncMock(return_value=mock_result)

        http_req = MagicMock()
        http_req.app.state.agent_infra = infra

        with patch("api.agent_routes.create_orchestrator", return_value=mock_orch), \
             patch("api.agent_routes.get_database_connection") as mock_db_conn:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_db_conn.return_value = mock_db

            body = self._make_request("Write a poem about cats")
            coro = execute_agent_request(body, http_req, key_info)
            response = asyncio.get_event_loop().run_until_complete(coro)

        assert response.success is True
        assert response.data["output"] == "orchestrated response"
        mock_orch.run.assert_awaited_once()

    def test_execute_uses_api_key_model_override(self):
        """When the API key has model_override, that model is passed to create_orchestrator."""
        from api.agent_routes import execute_agent_request

        infra = _make_infra()
        key_info = _make_key_info(
            provider_override="openrouter",
            model_override="google/gemma-3-27b-it:free",
        )
        mock_result = _make_agent_result("result")
        mock_orch = AsyncMock()
        mock_orch.run = AsyncMock(return_value=mock_result)

        http_req = MagicMock()
        http_req.app.state.agent_infra = infra

        captured: dict = {}

        def fake_create(infra, model_id=None, provider_name=None):
            captured["model_id"] = model_id
            captured["provider_name"] = provider_name
            return mock_orch

        with patch("api.agent_routes.create_orchestrator", side_effect=fake_create):
            body = self._make_request("Run analysis")
            coro = execute_agent_request(body, http_req, key_info)
            asyncio.get_event_loop().run_until_complete(coro)

        assert captured["model_id"] == "google/gemma-3-27b-it:free"
        assert captured["provider_name"] == "openrouter"

    def test_execute_falls_back_to_user_api_default_pref(self):
        """If key has no override, user's api_default preference is used."""
        from api.agent_routes import execute_agent_request

        infra = _make_infra()
        key_info = _make_key_info()  # no overrides
        mock_result = _make_agent_result("result")
        mock_orch = AsyncMock()
        mock_orch.run = AsyncMock(return_value=mock_result)

        http_req = MagicMock()
        http_req.app.state.agent_infra = infra

        captured: dict = {}

        def fake_create(infra, model_id=None, provider_name=None):
            captured["model_id"] = model_id
            captured["provider_name"] = provider_name
            return mock_orch

        # Simulate user has api_default preference in DB
        mock_pref_row = MagicMock()
        mock_pref_row.__getitem__ = lambda self, key: {
            "provider_name": "nano_gpt",
            "model_id": "moonshotai/kimi-k2.5",
        }[key]

        with patch("api.agent_routes.create_orchestrator", side_effect=fake_create), \
             patch("api.agent_routes.get_database_connection") as mock_db_conn:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = [mock_pref_row]
            mock_db_conn.return_value = mock_db

            body = self._make_request("Analyse data")
            coro = execute_agent_request(body, http_req, key_info)
            asyncio.get_event_loop().run_until_complete(coro)

        assert captured["model_id"] == "moonshotai/kimi-k2.5"
        assert captured["provider_name"] == "nano_gpt"

    def test_orchestrator_error_returns_success_false(self):
        """If orchestrator raises, response.success is False and error is set."""
        from api.agent_routes import execute_agent_request

        infra = _make_infra()
        key_info = _make_key_info()
        mock_orch = AsyncMock()
        mock_orch.run = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        http_req = MagicMock()
        http_req.app.state.agent_infra = infra

        with patch("api.agent_routes.create_orchestrator", return_value=mock_orch), \
             patch("api.agent_routes.get_database_connection") as mock_db_conn:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_db_conn.return_value = mock_db

            body = self._make_request("Do something")
            coro = execute_agent_request(body, http_req, key_info)
            response = asyncio.get_event_loop().run_until_complete(coro)

        assert response.success is False
        assert "LLM timeout" in response.error

    def test_parameters_appended_to_task(self):
        """Non-empty parameters are JSON-serialised and appended to the task text."""
        from api.agent_routes import execute_agent_request

        infra = _make_infra()
        key_info = _make_key_info()
        mock_result = _make_agent_result("ok")
        mock_orch = AsyncMock()
        mock_orch.run = AsyncMock(return_value=mock_result)

        http_req = MagicMock()
        http_req.app.state.agent_infra = infra

        captured: dict = {}

        async def fake_run(task, context=""):
            captured["task"] = task
            return mock_result

        mock_orch.run = fake_run

        with patch("api.agent_routes.create_orchestrator", return_value=mock_orch), \
             patch("api.agent_routes.get_database_connection") as mock_db_conn:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_db_conn.return_value = mock_db

            body = self._make_request("Summarise text", parameters={"text": "hello world", "max_words": 5})
            coro = execute_agent_request(body, http_req, key_info)
            asyncio.get_event_loop().run_until_complete(coro)

        assert "Summarise text" in captured["task"]
        assert "hello world" in captured["task"]


# ---------------------------------------------------------------------------
# 6F.2 — Conversation history
# ---------------------------------------------------------------------------

class TestExecuteEndpoint6F2:
    """6F.2: conversation_id maintains context across calls."""

    def _make_request(self, action: str, conv_id: str | None = None):
        from api.agent_routes import AgentRequest
        return AgentRequest(action=action, conversation_id=conv_id)

    def _run(self, body, http_req, key_info):
        from api.agent_routes import execute_agent_request
        return asyncio.get_event_loop().run_until_complete(
            execute_agent_request(body, http_req, key_info)
        )

    def setup_method(self):
        """Clear _api_conversations between tests."""
        from api.agent_routes import _api_conversations
        _api_conversations.clear()

    def test_no_conversation_id_no_history(self):
        """Without conversation_id, no history is sent to orchestrator."""
        infra = _make_infra()
        key_info = _make_key_info()

        turn_contexts: list = []

        async def fake_run(task, context=""):
            turn_contexts.append(context)
            return _make_agent_result("reply")

        mock_orch = MagicMock()
        mock_orch.run = fake_run

        http_req = MagicMock()
        http_req.app.state.agent_infra = infra

        with patch("api.agent_routes.create_orchestrator", return_value=mock_orch), \
             patch("api.agent_routes.get_database_connection") as mock_db_conn:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_db_conn.return_value = mock_db

            self._run(self._make_request("Hello"), http_req, key_info)

        assert turn_contexts[0] == ""

    def test_history_grows_across_calls(self):
        """Two calls with same conversation_id; second call sees first turn in context."""
        from api.agent_routes import _api_conversations

        infra = _make_infra()
        key_info = _make_key_info()
        conv_id = "test-conv-001"

        turn_contexts: list = []

        async def fake_run(task, context=""):
            turn_contexts.append(context)
            return _make_agent_result(f"reply to {task}")

        mock_orch = MagicMock()
        mock_orch.run = fake_run

        http_req = MagicMock()
        http_req.app.state.agent_infra = infra

        with patch("api.agent_routes.create_orchestrator", return_value=mock_orch), \
             patch("api.agent_routes.get_database_connection") as mock_db_conn:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_db_conn.return_value = mock_db

            # Turn 1
            self._run(self._make_request("What is 2+2?", conv_id=conv_id), http_req, key_info)
            # Turn 2
            self._run(self._make_request("And 3+3?", conv_id=conv_id), http_req, key_info)

        # First call has empty context
        assert turn_contexts[0] == ""
        # Second call context references first turn
        assert "What is 2+2?" in turn_contexts[1]
        assert "reply to" in turn_contexts[1]

    def test_history_bounded_to_40_entries(self):
        """History store never exceeds 40 entries (20 turns)."""
        from api.agent_routes import _api_conversations

        infra = _make_infra()
        key_info = _make_key_info()
        conv_id = "test-conv-overflow"

        async def fake_run(task, context=""):
            return _make_agent_result("ok")

        mock_orch = MagicMock()
        mock_orch.run = fake_run

        http_req = MagicMock()
        http_req.app.state.agent_infra = infra

        with patch("api.agent_routes.create_orchestrator", return_value=mock_orch), \
             patch("api.agent_routes.get_database_connection") as mock_db_conn:
            mock_db = MagicMock()
            mock_db.execute_query.return_value = []
            mock_db_conn.return_value = mock_db

            for i in range(30):  # 30 calls → 60 entries before bounding
                self._run(
                    self._make_request(f"Message {i}", conv_id=conv_id),
                    http_req, key_info,
                )

        assert len(_api_conversations[conv_id]) <= 40


# ---------------------------------------------------------------------------
# verify_api_token — real DB validation
# ---------------------------------------------------------------------------

class TestVerifyApiToken:
    """verify_api_token now validates against DB instead of accepting any token."""

    def test_missing_header_raises_401(self):
        from api.agent_routes import verify_api_token
        from fastapi import HTTPException

        coro = verify_api_token(authorization=None)
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(coro)
        assert exc_info.value.status_code == 401

    def test_bad_format_raises_401(self):
        from api.agent_routes import verify_api_token
        from fastapi import HTTPException

        coro = verify_api_token(authorization="Token abc123")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.get_event_loop().run_until_complete(coro)
        assert exc_info.value.status_code == 401

    def test_invalid_key_raises_401(self):
        from api.agent_routes import verify_api_token
        from fastapi import HTTPException

        with patch("api.agent_routes.get_api_key_service") as mock_svc:
            mock_svc.return_value.validate_api_key.return_value = None
            coro = verify_api_token(authorization="Bearer invalid_key_123")
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(coro)
        assert exc_info.value.status_code == 401

    def test_valid_key_returns_key_info(self):
        from api.agent_routes import verify_api_token

        expected = {
            "key_id": "k1",
            "user_id": "u1",
            "role": "user",
            "scopes": "read",
            "name": "my key",
            "provider_override": "openrouter",
            "model_override": "gemma:free",
        }

        with patch("api.agent_routes.get_api_key_service") as mock_svc:
            mock_svc.return_value.validate_api_key.return_value = expected
            coro = verify_api_token(authorization="Bearer 4s1t_validtoken")
            result = asyncio.get_event_loop().run_until_complete(coro)

        assert result == expected
        assert result["provider_override"] == "openrouter"
        assert result["model_override"] == "gemma:free"
