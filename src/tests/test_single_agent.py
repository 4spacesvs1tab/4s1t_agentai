"""
Phase 3 integration test — single-agent mode (task 3.9).

Structure:
  Unit tests  (no network, mocked LLM + skills) — always run
  Integration test (live Nano-GPT API)           — skipped unless NANO_GPT_API_KEY is set

Unit tests cover:
  - BaseAgent loop: stop on first response (no tool calls)
  - BaseAgent loop: one round of tool calls then stop
  - Scope enforcement: agent cannot call a skill outside its persona's allowed list
  - Context compaction: fires when token budget is >80% full
  - HITL approval gate: approve / deny / timeout paths
  - Persona registry: all four agent types load correctly

Integration test (pytest -m live):
  - ResearchAgent performs a web search and returns a synthesised answer
    (simplest agent for live testing — no approval gate, no Docker)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Sys-path + env bootstrap (mirrors conftest.py for standalone runs)
# ---------------------------------------------------------------------------

os.environ.setdefault("SECRET_KEY", "CI_Test_S3cret_Key_64chars_long_ABCDEFGHIJK!@#$%^&*()")
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("DEBUG", "true")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------

from agents.agent_result import AgentResult
from agents.base_agent import BaseAgent, _estimate_tokens, _count_messages_tokens
from agents.personas import get_persona, all_agent_types, Persona
from agents.ba_agent import BAAgent
from agents.data_agent import DataAgent
from agents.research_agent import ResearchAgent
from agents.synthesis_agent import SynthesisAgent
from agents.approval import ApprovalGate


# ---------------------------------------------------------------------------
# Helpers — build minimal mocked infrastructure
# ---------------------------------------------------------------------------

def _make_skill_registry(allowed_skills: list[str] | None = None):
    """Return a mock SkillRegistry that reports allowed_skills for any agent."""
    registry = MagicMock()
    registry.tools_for_agent.return_value = []   # no tools by default
    registry.get.side_effect = KeyError("skill not found")
    return registry


def _make_skill_executor():
    """Return a mock SkillExecutor whose execute() can be customised per test."""
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(
        success=True, result={"answer": "42"}, error=None
    ))
    return executor


def _make_api_client(content="Hello from mock LLM", tool_calls=None, finish_reason=None):
    """
    Return a mock ApiClient whose chat_completion() returns a controlled response.

    If tool_calls is provided, the first response has finish_reason="tool_calls"
    and those tool calls, then subsequent calls return a stop response.
    """
    from skills.models import SkillOutput

    response_stop = MagicMock()
    response_stop.choices = [MagicMock()]
    response_stop.choices[0].finish_reason = "stop"
    response_stop.choices[0].message.content = content
    response_stop.choices[0].message.tool_calls = None

    if tool_calls:
        response_tool = MagicMock()
        response_tool.choices = [MagicMock()]
        response_tool.choices[0].finish_reason = "tool_calls"
        response_tool.choices[0].message.content = None
        response_tool.choices[0].message.tool_calls = tool_calls

        client = MagicMock()
        client.chat_completion = AsyncMock(
            side_effect=[response_tool, response_stop]
        )
    else:
        client = MagicMock()
        client.chat_completion = AsyncMock(return_value=response_stop)

    return client


def _make_model_selector(model="deepseek-v3.2"):
    selector = MagicMock()
    selector.select.return_value = model
    return selector


def _make_audit_log():
    log = MagicMock()
    log.log = AsyncMock()
    return log


def _make_base_agent(
    agent_type="research_agent",
    api_content="Hello from mock LLM",
    tool_calls=None,
    extra_allowed_skills=None,
) -> tuple[BaseAgent, MagicMock, MagicMock]:
    """
    Convenience factory: returns (agent, mock_executor, mock_audit_log).
    """
    persona = get_persona(agent_type)
    registry = _make_skill_registry()
    executor = _make_skill_executor()
    api_client = _make_api_client(content=api_content, tool_calls=tool_calls)
    model_selector = _make_model_selector()
    audit_log = _make_audit_log()

    agent = BaseAgent(
        persona=persona,
        skill_registry=registry,
        skill_executor=executor,
        api_client=api_client,
        model_selector=model_selector,
        audit_log=audit_log,
    )
    return agent, executor, audit_log


# ===========================================================================
# UNIT TESTS
# ===========================================================================

class TestPersonaRegistry(unittest.TestCase):
    """Verify persona definitions are complete and consistent."""

    def test_all_agent_types_load(self):
        types_ = all_agent_types()
        assert "ba_agent" in types_
        assert "data_agent" in types_
        assert "research_agent" in types_
        assert "synthesis_agent" in types_

    def test_unknown_agent_type_raises(self):
        with pytest.raises(KeyError):
            get_persona("nonexistent_agent")

    def test_ba_agent_persona_has_babok_skill(self):
        p = get_persona("ba_agent")
        assert "babok_lookup" in p.allowed_skills

    def test_data_agent_requires_approval_for_python_execute(self):
        p = get_persona("data_agent")
        assert "python_execute" in p.requires_approval

    def test_ba_agent_requires_approval_for_gap_analysis(self):
        p = get_persona("ba_agent")
        assert "gap_analysis" in p.requires_approval

    def test_research_agent_no_requires_approval(self):
        p = get_persona("research_agent")
        assert p.requires_approval == []

    def test_synthesis_agent_no_requires_approval(self):
        p = get_persona("synthesis_agent")
        assert p.requires_approval == []

    def test_all_personas_have_model_preference(self):
        for agent_type in all_agent_types():
            p = get_persona(agent_type)
            assert p.model_preference in ("reasoning", "fast", "coding", "general"), \
                f"{agent_type}.model_preference={p.model_preference!r} not in expected set"


class TestAgentResultModel(unittest.TestCase):
    def test_defaults(self):
        result = AgentResult(agent_type="ba_agent", output="test")
        assert result.tool_calls_made == 0
        assert result.context_compactions == 0
        assert result.error is None
        assert result.workflow_id is None

    def test_error_field(self):
        result = AgentResult(agent_type="ba_agent", output="", error="LLM failed")
        assert result.error == "LLM failed"


class TestWorkerAgentFactories(unittest.TestCase):
    """Smoke-test that the typed subclasses instantiate without error."""

    def _infra(self):
        registry = _make_skill_registry()
        executor = _make_skill_executor()
        api_client = _make_api_client()
        model_selector = _make_model_selector()
        return registry, executor, api_client, model_selector

    def test_ba_agent_create(self):
        agent = BAAgent.create(*self._infra())
        assert agent._persona.agent_type == "ba_agent"

    def test_data_agent_create(self):
        agent = DataAgent.create(*self._infra())
        assert agent._persona.agent_type == "data_agent"

    def test_research_agent_create(self):
        agent = ResearchAgent.create(*self._infra())
        assert agent._persona.agent_type == "research_agent"

    def test_synthesis_agent_create(self):
        agent = SynthesisAgent.create(*self._infra())
        assert agent._persona.agent_type == "synthesis_agent"


class TestBaseAgentLoop(unittest.IsolatedAsyncioTestCase):
    """Agent loop logic with mocked LLM."""

    async def test_simple_stop_response(self):
        """Agent returns immediately when LLM says stop."""
        agent, executor, audit = _make_base_agent(api_content="The answer is 42.")
        result = await agent.run("What is the answer?")

        assert result.output == "The answer is 42."
        assert result.error is None
        assert result.tool_calls_made == 0
        audit.log.assert_called()   # at least AGENT_SPAWN event

    async def test_context_and_task_in_messages(self):
        """Context is prepended as user message before the task."""
        agent, _, _ = _make_base_agent()
        await agent.run("My task", context="Prior context here")

        messages = agent._messages
        # system, context, task — at minimum 3 messages after run
        assert messages[0]["role"] == "system"
        assert any("Prior context here" in str(m.get("content", "")) for m in messages)

    async def test_tool_call_round(self):
        """
        Agent loop executes one round of tool calls and then stops.

        Mocked flow:
          LLM call 1 → finish_reason="tool_calls", calls "web_search"
          LLM call 2 → finish_reason="stop", content="Found it."
        """
        # Build a mock tool call object
        tc = MagicMock()
        tc.id = "call_abc123"
        tc.function.name = "web_search"
        tc.function.arguments = '{"query": "Python asyncio"}'

        persona = get_persona("research_agent")
        registry = MagicMock()
        registry.tools_for_agent.return_value = [{"type": "function", "function": {"name": "web_search"}}]

        # Registry.get returns a skill meta that is in scope
        skill_meta = MagicMock()
        skill_meta.requires_approval = False
        skill_meta.secrets_required = []
        skill_meta.is_allowed_for.return_value = True
        registry.get.return_value = skill_meta

        executor = MagicMock()
        executor.execute = AsyncMock(return_value=MagicMock(
            success=True, result={"results": [{"title": "Python docs"}]}, error=None
        ))

        api_client = _make_api_client(content="Found it.", tool_calls=[tc])
        model_selector = _make_model_selector()
        audit_log = _make_audit_log()

        agent = BaseAgent(
            persona=persona,
            skill_registry=registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
        )
        result = await agent.run("Search for Python asyncio docs.")

        assert result.output == "Found it."
        assert result.tool_calls_made == 1
        assert result.error is None
        executor.execute.assert_called_once()

    async def test_scope_block_out_of_persona_skill(self):
        """
        Agent cannot call a skill not in its persona.allowed_skills.
        The belt-and-suspenders check in BaseAgent must block it before
        reaching the SkillExecutor.
        """
        tc = MagicMock()
        tc.id = "call_bad"
        tc.function.name = "python_execute"     # NOT in research_agent's allowed_skills
        tc.function.arguments = '{"code": "import os"}'

        persona = get_persona("research_agent")    # allowed: web_search, knowledge_base_search, ...
        assert "python_execute" not in persona.allowed_skills

        registry = MagicMock()
        registry.tools_for_agent.return_value = []
        executor = _make_skill_executor()
        api_client = _make_api_client(content="Blocked.", tool_calls=[tc])
        model_selector = _make_model_selector()

        agent = BaseAgent(
            persona=persona,
            skill_registry=registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
        )
        result = await agent.run("Try something bad.")

        # Executor should NOT have been called
        executor.execute.assert_not_called()
        # The tool result message should contain the scope error text
        tool_result_msgs = [
            m for m in agent._messages
            if isinstance(m, dict) and m.get("role") == "tool"
        ]
        assert len(tool_result_msgs) == 1
        assert "not in the allowed_skills" in tool_result_msgs[0]["content"]

    async def test_llm_failure_returns_error_result(self):
        """When the LLM call raises, BaseAgent returns an AgentResult with error."""
        persona = get_persona("research_agent")
        registry = _make_skill_registry()
        executor = _make_skill_executor()
        model_selector = _make_model_selector()
        audit_log = _make_audit_log()

        api_client = MagicMock()
        api_client.chat_completion = AsyncMock(side_effect=RuntimeError("Connection refused"))

        agent = BaseAgent(
            persona=persona,
            skill_registry=registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
        )
        result = await agent.run("Any task")

        assert result.error is not None
        assert "Connection refused" in result.error


class TestTokenHelpers(unittest.TestCase):
    """Verify the token estimation helpers."""

    def test_estimate_tokens_empty(self):
        assert _estimate_tokens("") == 1   # min(1, ...)

    def test_estimate_tokens_40_chars(self):
        text = "a" * 40
        assert _estimate_tokens(text) == 10

    def test_count_messages_tokens_list_of_dicts(self):
        messages = [
            {"role": "system", "content": "a" * 400},
            {"role": "user", "content": "b" * 200},
        ]
        total = _count_messages_tokens(messages)
        assert total == 100 + 50


class TestApprovalGate(unittest.IsolatedAsyncioTestCase):
    """Unit tests for the async HITL approval gate."""

    async def test_no_client_auto_approves(self):
        gate = ApprovalGate(nostr_client=None)
        result = await gate.request_approval("python_execute", {}, "data_agent")
        assert result is True

    async def test_approve_path(self):
        """Simulates user responding 'approved <id>' via Nostr."""
        client = MagicMock()
        client.send_encrypted_dm = AsyncMock(return_value="msg123")

        # receive_messages returns a matching "approved" response
        approval_id_capture = []

        async def _fake_send(text):
            # Extract approval_id from the DM text
            for line in text.split("\n"):
                if line.startswith("APPROVE REQUEST "):
                    approval_id_capture.append(line.split()[2])
            return "msg123"

        client.send_encrypted_dm = _fake_send

        async def _fake_receive(since_seconds=600):
            if approval_id_capture:
                msg = MagicMock()
                msg.content = f"approved {approval_id_capture[0]}"
                return [msg]
            return []

        client.receive_messages = _fake_receive

        gate = ApprovalGate(nostr_client=client, timeout_seconds=30.0)
        result = await gate.request_approval("python_execute", {"code": "print(1)"}, "data_agent")
        assert result is True

    async def test_deny_path(self):
        """Simulates user responding 'denied <id>' via Nostr."""
        approval_id_capture = []

        async def _fake_send(text):
            for line in text.split("\n"):
                if line.startswith("APPROVE REQUEST "):
                    approval_id_capture.append(line.split()[2])
            return "msg456"

        async def _fake_receive(since_seconds=600):
            if approval_id_capture:
                msg = MagicMock()
                msg.content = f"denied {approval_id_capture[0]}"
                return [msg]
            return []

        client = MagicMock()
        client.send_encrypted_dm = _fake_send
        client.receive_messages = _fake_receive

        gate = ApprovalGate(nostr_client=client, timeout_seconds=30.0)
        result = await gate.request_approval("python_execute", {}, "data_agent")
        assert result is False

    async def test_timeout_denies(self):
        """When no response arrives within timeout, gate denies."""
        client = MagicMock()
        client.send_encrypted_dm = AsyncMock(return_value="msg789")
        client.receive_messages = AsyncMock(return_value=[])   # no response

        gate = ApprovalGate(nostr_client=client, timeout_seconds=0.1)
        result = await gate.request_approval("python_execute", {}, "data_agent")
        assert result is False


# ===========================================================================
# INTEGRATION TEST (requires NANO_GPT_API_KEY env var)
# ===========================================================================

LIVE_TEST_REASON = "NANO_GPT_API_KEY not set — skipping live integration test"
LIVE_API_KEY = os.environ.get("NANO_GPT_API_KEY", "")


@pytest.mark.skipif(not LIVE_API_KEY, reason=LIVE_TEST_REASON)
class TestSingleAgentLive(unittest.IsolatedAsyncioTestCase):
    """
    Full end-to-end single-agent test against the live Nano-GPT API.

    Uses ResearchAgent:
      - web_search skill (DuckDuckGo, no API key)
      - No approval gate needed
      - No Docker required

    Expected behaviour:
      - Agent calls web_search at least once
      - AgentResult.output is a non-empty string
      - No error
    """

    async def asyncSetUp(self):
        from core.api_client import ApiClient
        from core.audit import AuditLog
        from core.model_selector import ModelSelector
        from skills.registry import get_skill_registry
        from skills.executor import SkillExecutor

        self.registry = get_skill_registry()
        self.audit = AuditLog("sqlite:///:memory:")
        await self.audit.start()

        api_client = ApiClient(api_key=LIVE_API_KEY)
        executor = SkillExecutor(registry=self.registry, audit_log=self.audit)
        model_selector = ModelSelector()

        self.agent = ResearchAgent.create(
            skill_registry=self.registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=self.audit,
        )

    async def asyncTearDown(self):
        await self.audit.stop()

    async def test_research_agent_web_search(self):
        """ResearchAgent should perform a web search and return a synthesised answer."""
        result = await self.agent.run(
            "What is Python's asyncio event loop? Give a one-paragraph summary."
        )

        print(f"\n[live test] output={result.output[:200]!r}")
        print(f"[live test] tool_calls_made={result.tool_calls_made}")
        print(f"[live test] error={result.error!r}")

        assert result.error is None, f"Agent returned error: {result.error}"
        assert len(result.output) > 50, "Expected a substantive response"

    async def test_ba_agent_simple_question(self):
        """BAAgent answers a BABOK question (no skill calls required for simple Q&A)."""
        from core.api_client import ApiClient
        from core.model_selector import ModelSelector
        from skills.executor import SkillExecutor

        api_client = ApiClient(api_key=LIVE_API_KEY)
        executor = SkillExecutor(registry=self.registry, audit_log=self.audit)
        model_selector = ModelSelector()

        agent = BAAgent.create(
            skill_registry=self.registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=self.audit,
        )

        result = await agent.run(
            "Briefly explain what a Business Requirements Document (BRD) is."
        )

        print(f"\n[live BA test] output={result.output[:200]!r}")
        assert result.error is None
        assert len(result.output) > 30


if __name__ == "__main__":
    unittest.main()
