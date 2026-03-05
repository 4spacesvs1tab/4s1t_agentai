"""
Tests for ApiClient (task 1.5).

Uses unittest.mock to avoid real network calls while testing:
- Semaphore limits concurrency to max_concurrent
- Retry fires on retryable exceptions (RateLimitError)
- Non-retryable exceptions propagate immediately
- Successful calls return the mocked response
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SECRET_KEY", "CI_Test_S3cret_Key_64chars_long_ABCDEFGHIJK!@#$%^&*()")
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("DEBUG", "true")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.api_client import ApiClient, _MAX_RETRIES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_openai_module():
    """Build a minimal mock openai module with the error classes we need."""
    mod = types.ModuleType("openai")

    class RateLimitError(Exception): pass
    class APIConnectionError(Exception): pass
    class APITimeoutError(Exception): pass

    mod.RateLimitError = RateLimitError
    mod.APIConnectionError = APIConnectionError
    mod.APITimeoutError = APITimeoutError

    return mod


def _fake_response(content="Hello from mock"):
    """Build a minimal ChatCompletion-like mock response."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage.total_tokens = 42
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestApiClientSuccess:
    async def test_successful_call_returns_response(self):
        fake_openai = _make_openai_module()
        fake_client = AsyncMock()
        fake_client.chat.completions.create = AsyncMock(return_value=_fake_response())

        client = ApiClient(api_key="test-key", base_url="http://fake", max_concurrent=3)

        with patch.dict("sys.modules", {"openai": fake_openai}):
            fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)
            client._client = None  # force lazy reinit
            response = await client.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
                model="deepseek-v3.2",
            )

        assert response.choices[0].message.content == "Hello from mock"

    async def test_semaphore_limits_concurrency(self):
        """At most max_concurrent tasks run inside the semaphore at once."""
        max_concurrent = 2
        active = 0
        peak = 0

        async def slow_create(**kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            return _fake_response()

        fake_openai = _make_openai_module()
        fake_client = AsyncMock()
        fake_client.chat.completions.create = slow_create

        client = ApiClient(api_key="test-key", base_url="http://fake", max_concurrent=max_concurrent)

        with patch.dict("sys.modules", {"openai": fake_openai}):
            fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)
            client._client = None

            # Launch more tasks than max_concurrent
            tasks = [
                asyncio.create_task(
                    client.chat_completion([{"role": "user", "content": "hi"}], "model")
                )
                for _ in range(5)
            ]
            await asyncio.gather(*tasks)

        assert peak <= max_concurrent, f"Concurrency exceeded: peak={peak} > max={max_concurrent}"


@pytest.mark.asyncio
class TestApiClientRetry:
    async def test_retries_on_rate_limit_error(self):
        fake_openai = _make_openai_module()
        call_count = 0

        async def flaky_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < _MAX_RETRIES:
                raise fake_openai.RateLimitError("rate limited")
            return _fake_response()

        fake_client = AsyncMock()
        fake_client.chat.completions.create = flaky_create

        client = ApiClient(api_key="test-key", base_url="http://fake", max_concurrent=3)

        with patch.dict("sys.modules", {"openai": fake_openai}):
            fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)
            client._client = None
            # Patch sleep to avoid actual delay in tests
            with patch("core.api_client.asyncio.sleep", new_callable=AsyncMock):
                response = await client.chat_completion(
                    [{"role": "user", "content": "retry test"}], "model"
                )

        assert response.choices[0].message.content == "Hello from mock"
        assert call_count == _MAX_RETRIES

    async def test_non_retryable_exception_propagates_immediately(self):
        fake_openai = _make_openai_module()
        call_count = 0

        async def always_fail(**kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("non-retryable boom")

        fake_client = AsyncMock()
        fake_client.chat.completions.create = always_fail

        client = ApiClient(api_key="test-key", base_url="http://fake", max_concurrent=3)

        with patch.dict("sys.modules", {"openai": fake_openai}):
            fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)
            client._client = None
            with pytest.raises(ValueError, match="non-retryable boom"):
                await client.chat_completion([{"role": "user", "content": ""}], "model")

        # Should fail on the first attempt without retrying
        assert call_count == 1

    async def test_exhausts_retries_and_raises(self):
        fake_openai = _make_openai_module()

        async def always_rate_limit(**kwargs):
            raise fake_openai.RateLimitError("always rate limited")

        fake_client = AsyncMock()
        fake_client.chat.completions.create = always_rate_limit

        client = ApiClient(api_key="test-key", base_url="http://fake", max_concurrent=3)

        with patch.dict("sys.modules", {"openai": fake_openai}):
            fake_openai.AsyncOpenAI = MagicMock(return_value=fake_client)
            client._client = None
            with patch("core.api_client.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(fake_openai.RateLimitError):
                    await client.chat_completion([{"role": "user", "content": ""}], "model")
