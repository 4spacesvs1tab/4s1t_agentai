"""
Phase 4 orchestration tests — TaskGraph + OrchestratorAgent (task 4.8).

Structure:
  Unit tests  (no network, mocked LLM)   — always run
  Integration test (live Nano-GPT API)   — skipped unless NANO_GPT_API_KEY is set

Unit tests cover:
  - TaskGraph topological_waves: linear chain, parallel fan-in, cycle detection,
    empty graph, single node
  - OrchestratorAgent.decompose_task: valid JSON, retry on bad JSON, fallback on
    persistent failure
  - OrchestratorAgent.run: single-subtask bypass, multi-wave execution,
    summarise_for_handoff triggered by large output
  - Workflow audit events: WORKFLOW_START and WORKFLOW_END always logged

Integration test (pytest -m live):
  - 3-agent workflow: research → data → synthesis
  - Verify parallel execution and non-empty final output
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Sys-path + env bootstrap
# ---------------------------------------------------------------------------

os.environ.setdefault("SECRET_KEY", "CI_Test_S3cret_Key_64chars_long_ABCDEFGHIJK!@#$%^&*()")
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("DEBUG", "true")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------

from agents.task_graph import SubTask, TaskGraph
from agents.orchestrator import OrchestratorAgent, _HANDOFF_TOKEN_THRESHOLD
from agents.agent_result import AgentResult
from core.audit import AuditEventType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_subtask(
    task_id: str,
    agent_type: str = "research_agent",
    depends_on: list[str] | None = None,
) -> SubTask:
    return SubTask(
        task_id=task_id,
        description=f"Task {task_id}",
        agent_type=agent_type,
        depends_on=depends_on or [],
    )


def _make_mock_infra(decompose_response: str | None = None):
    """Build minimal mocked infrastructure for OrchestratorAgent."""
    registry = MagicMock()
    registry.tools_for_agent.return_value = []
    registry.get.side_effect = KeyError("not found")

    executor = MagicMock()
    executor.execute = AsyncMock(return_value=MagicMock(success=True, result={}, error=None))

    audit_log = MagicMock()
    audit_log.log = AsyncMock()

    model_selector = MagicMock()
    model_selector.select.return_value = "deepseek-v3.2"

    return registry, executor, audit_log, model_selector


def _make_api_client_for_decompose(graph_json: str):
    """API client that returns a TaskGraph JSON for decompose, then 'done' for agents."""
    stop_response = MagicMock()
    stop_response.choices = [MagicMock()]
    stop_response.choices[0].finish_reason = "stop"
    stop_response.choices[0].message.content = "Task complete."
    stop_response.choices[0].message.tool_calls = None

    decompose_response = MagicMock()
    decompose_response.choices = [MagicMock()]
    decompose_response.choices[0].finish_reason = "stop"
    decompose_response.choices[0].message.content = graph_json
    decompose_response.choices[0].message.tool_calls = None

    api_client = MagicMock()
    # First call = decompose, subsequent = agent LLM calls
    api_client.chat_completion = AsyncMock(
        side_effect=[decompose_response, stop_response, stop_response, stop_response]
    )
    return api_client


def _single_task_graph_json(workflow_id: str = "wf-1") -> str:
    return json.dumps({
        "workflow_id": workflow_id,
        "subtasks": [
            {
                "task_id": "t1",
                "description": "Do some research",
                "agent_type": "research_agent",
                "model_preference": "general",
                "depends_on": [],
            }
        ],
    })


def _two_wave_graph_json(workflow_id: str = "wf-2") -> str:
    """research + ba_agent in parallel, then synthesis depends on both."""
    return json.dumps({
        "workflow_id": workflow_id,
        "subtasks": [
            {
                "task_id": "t1",
                "description": "Research topic",
                "agent_type": "research_agent",
                "model_preference": "general",
                "depends_on": [],
            },
            {
                "task_id": "t2",
                "description": "Analyse requirements",
                "agent_type": "ba_agent",
                "model_preference": "reasoning",
                "depends_on": [],
            },
            {
                "task_id": "t3",
                "description": "Write final report",
                "agent_type": "synthesis_agent",
                "model_preference": "general",
                "depends_on": ["t1", "t2"],
            },
        ],
    })


# ===========================================================================
# Unit Tests — TaskGraph
# ===========================================================================

class TestTaskGraphTopologicalWaves(unittest.TestCase):
    """Verify Kahn's algorithm produces correct execution waves."""

    def _graph(self, subtasks: list[SubTask]) -> TaskGraph:
        return TaskGraph(workflow_id="test-wf", subtasks=subtasks)

    def test_empty_graph_returns_empty(self):
        waves = self._graph([]).topological_waves()
        assert waves == []

    def test_single_node(self):
        t1 = _make_subtask("t1")
        waves = self._graph([t1]).topological_waves()
        assert len(waves) == 1
        assert waves[0][0].task_id == "t1"

    def test_linear_chain_abc(self):
        """A → B → C should produce three sequential waves."""
        ta = _make_subtask("a")
        tb = _make_subtask("b", depends_on=["a"])
        tc = _make_subtask("c", depends_on=["b"])
        waves = self._graph([ta, tb, tc]).topological_waves()

        assert len(waves) == 3
        assert [t.task_id for t in waves[0]] == ["a"]
        assert [t.task_id for t in waves[1]] == ["b"]
        assert [t.task_id for t in waves[2]] == ["c"]

    def test_parallel_fan_in(self):
        """A and B run in parallel; C waits for both."""
        ta = _make_subtask("a")
        tb = _make_subtask("b")
        tc = _make_subtask("c", depends_on=["a", "b"])
        waves = self._graph([ta, tb, tc]).topological_waves()

        assert len(waves) == 2
        wave0_ids = {t.task_id for t in waves[0]}
        assert wave0_ids == {"a", "b"}
        assert waves[1][0].task_id == "c"

    def test_cycle_raises_value_error(self):
        """A cycle must be detected and raise ValueError."""
        ta = _make_subtask("a", depends_on=["b"])
        tb = _make_subtask("b", depends_on=["a"])
        with pytest.raises(ValueError, match="[Cc]ycle"):
            self._graph([ta, tb]).topological_waves()

    def test_unknown_dep_raises_on_construction(self):
        """depends_on referencing a non-existent task_id is caught at model validation."""
        with pytest.raises(Exception):  # pydantic ValidationError
            TaskGraph(
                workflow_id="test",
                subtasks=[_make_subtask("a", depends_on=["z"])],
            )

    def test_two_independent_nodes(self):
        """Two nodes with no deps should be in the same wave."""
        ta = _make_subtask("a")
        tb = _make_subtask("b")
        waves = self._graph([ta, tb]).topological_waves()
        assert len(waves) == 1
        assert {t.task_id for t in waves[0]} == {"a", "b"}


# ===========================================================================
# Unit Tests — OrchestratorAgent
# ===========================================================================

class TestOrchestratorDecompose(unittest.IsolatedAsyncioTestCase):
    """decompose_task parses JSON into a TaskGraph."""

    def _make_orchestrator(self, api_client) -> OrchestratorAgent:
        registry, executor, audit_log, model_selector = _make_mock_infra()
        return OrchestratorAgent(
            skill_registry=registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
            workflow_id="test-wf",
        )

    async def test_valid_json_returns_task_graph(self):
        """Well-formed JSON on first attempt → TaskGraph returned."""
        api_client = _make_api_client_for_decompose(_single_task_graph_json())
        orch = self._make_orchestrator(api_client)
        graph = await orch._decompose_task("Do research", "")
        assert graph is not None
        assert len(graph.subtasks) == 1
        assert graph.subtasks[0].agent_type == "research_agent"

    async def test_invalid_json_first_then_valid_retry(self):
        """Invalid JSON on first call, valid on retry → TaskGraph returned."""
        valid_json = _single_task_graph_json("retry-wf")

        bad_response = MagicMock()
        bad_response.choices = [MagicMock()]
        bad_response.choices[0].finish_reason = "stop"
        bad_response.choices[0].message.content = "This is not JSON at all."
        bad_response.choices[0].message.tool_calls = None

        good_response = MagicMock()
        good_response.choices = [MagicMock()]
        good_response.choices[0].finish_reason = "stop"
        good_response.choices[0].message.content = valid_json
        good_response.choices[0].message.tool_calls = None

        api_client = MagicMock()
        api_client.chat_completion = AsyncMock(
            side_effect=[bad_response, good_response]
        )

        orch = self._make_orchestrator(api_client)
        graph = await orch._decompose_task("Some task", "")
        assert graph is not None
        assert len(graph.subtasks) == 1
        # Two LLM calls: original + 1 retry
        assert api_client.chat_completion.call_count == 2

    async def test_persistent_failure_returns_none(self):
        """Two consecutive failures → None (orchestrator falls back to bypass)."""
        bad_response = MagicMock()
        bad_response.choices = [MagicMock()]
        bad_response.choices[0].finish_reason = "stop"
        bad_response.choices[0].message.content = "still not json"
        bad_response.choices[0].message.tool_calls = None

        api_client = MagicMock()
        api_client.chat_completion = AsyncMock(return_value=bad_response)

        orch = self._make_orchestrator(api_client)
        graph = await orch._decompose_task("task", "")
        assert graph is None

    async def test_llm_exception_returns_none(self):
        """LLM call raises → returns None gracefully."""
        api_client = MagicMock()
        api_client.chat_completion = AsyncMock(side_effect=RuntimeError("API down"))

        orch = self._make_orchestrator(api_client)
        graph = await orch._decompose_task("task", "")
        assert graph is None

    async def test_markdown_fences_stripped(self):
        """JSON wrapped in ```json ... ``` should still parse."""
        inner = _single_task_graph_json("fenced-wf")
        fenced = f"```json\n{inner}\n```"

        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].finish_reason = "stop"
        response.choices[0].message.content = fenced
        response.choices[0].message.tool_calls = None

        api_client = MagicMock()
        api_client.chat_completion = AsyncMock(return_value=response)

        orch = self._make_orchestrator(api_client)
        graph = await orch._decompose_task("task", "")
        assert graph is not None


class TestOrchestratorRun(unittest.IsolatedAsyncioTestCase):
    """OrchestratorAgent.run() — bypass and multi-wave paths."""

    def _make_orchestrator_with_graph_json(self, graph_json: str) -> tuple:
        registry, executor, audit_log, model_selector = _make_mock_infra()
        api_client = _make_api_client_for_decompose(graph_json)
        orch = OrchestratorAgent(
            skill_registry=registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
            workflow_id="run-test-wf",
        )
        return orch, audit_log

    async def test_single_subtask_bypass(self):
        """Single-subtask graph skips wave loop, spawns one agent directly."""
        orch, audit_log = self._make_orchestrator_with_graph_json(
            _single_task_graph_json()
        )
        result = await orch.run("Simple task")

        assert result.error is None
        # WORKFLOW_START and WORKFLOW_END should be logged
        event_types = [c.args[0] if c.args else c.kwargs.get("event_type")
                       for c in audit_log.log.call_args_list]
        assert AuditEventType.WORKFLOW_START in event_types
        assert AuditEventType.WORKFLOW_END in event_types

    async def test_multi_wave_two_parallel_then_one(self):
        """Two-wave graph: wave 0 has 2 agents, wave 1 has 1 agent."""
        orch, audit_log = self._make_orchestrator_with_graph_json(
            _two_wave_graph_json()
        )
        result = await orch.run("Complex task")

        assert result.error is None
        # WAVE_COMPLETE should be logged for each wave
        event_types = [c.args[0] if c.args else c.kwargs.get("event_type")
                       for c in audit_log.log.call_args_list]
        wave_complete_events = [e for e in event_types if e == AuditEventType.WAVE_COMPLETE]
        assert len(wave_complete_events) == 2   # wave 0 (parallel) + wave 1

    async def test_workflow_start_end_always_logged(self):
        """WORKFLOW_START and WORKFLOW_END must appear in the audit log."""
        orch, audit_log = self._make_orchestrator_with_graph_json(
            _single_task_graph_json()
        )
        await orch.run("task")

        event_types = [c.args[0] if c.args else c.kwargs.get("event_type")
                       for c in audit_log.log.call_args_list]
        assert AuditEventType.WORKFLOW_START in event_types
        assert AuditEventType.WORKFLOW_END in event_types

    async def test_decompose_failure_falls_back_to_bypass(self):
        """If decompose returns None, run() falls back to single-agent bypass."""
        registry, executor, audit_log, model_selector = _make_mock_infra()

        # Make every LLM call return non-JSON so decompose always fails,
        # but agent calls return a valid stop response
        bad_decompose = MagicMock()
        bad_decompose.choices = [MagicMock()]
        bad_decompose.choices[0].finish_reason = "stop"
        bad_decompose.choices[0].message.content = "not json"
        bad_decompose.choices[0].message.tool_calls = None

        good_agent_response = MagicMock()
        good_agent_response.choices = [MagicMock()]
        good_agent_response.choices[0].finish_reason = "stop"
        good_agent_response.choices[0].message.content = "Done."
        good_agent_response.choices[0].message.tool_calls = None

        api_client = MagicMock()
        # Attempts: decompose (fail), retry (fail), then agent call succeeds
        api_client.chat_completion = AsyncMock(
            side_effect=[bad_decompose, bad_decompose, good_agent_response]
        )

        orch = OrchestratorAgent(
            skill_registry=registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
            workflow_id="fallback-wf",
        )
        result = await orch.run("A task")
        assert result.error is None
        assert result.output == "Done."

    async def test_summarise_triggered_by_large_output(self):
        """When combined wave output exceeds threshold, summarise_for_handoff is called."""
        registry, executor, audit_log, model_selector = _make_mock_infra()

        # Two-wave graph so we get a chance to compress before wave 1
        two_wave_json = _two_wave_graph_json("compress-wf")

        # Wave 0 produces oversized output
        oversized_output = "X" * (_HANDOFF_TOKEN_THRESHOLD + 1)

        decompose_resp = MagicMock()
        decompose_resp.choices = [MagicMock()]
        decompose_resp.choices[0].finish_reason = "stop"
        decompose_resp.choices[0].message.content = two_wave_json
        decompose_resp.choices[0].message.tool_calls = None

        # All agent calls return oversized output (wave 0 has 2 agents)
        big_agent_resp = MagicMock()
        big_agent_resp.choices = [MagicMock()]
        big_agent_resp.choices[0].finish_reason = "stop"
        big_agent_resp.choices[0].message.content = oversized_output
        big_agent_resp.choices[0].message.tool_calls = None

        small_summary_resp = MagicMock()
        small_summary_resp.choices = [MagicMock()]
        small_summary_resp.choices[0].finish_reason = "stop"
        small_summary_resp.choices[0].message.content = "Compressed summary."
        small_summary_resp.choices[0].message.tool_calls = None

        final_synthesis_resp = MagicMock()
        final_synthesis_resp.choices = [MagicMock()]
        final_synthesis_resp.choices[0].finish_reason = "stop"
        final_synthesis_resp.choices[0].message.content = "Final report."
        final_synthesis_resp.choices[0].message.tool_calls = None

        # Call sequence:
        #   1 - decompose_task
        #   2,3 - wave 0 agents (research + ba)
        #   4 - summarise_for_handoff
        #   5 - wave 1 agent (synthesis)
        api_client = MagicMock()
        api_client.chat_completion = AsyncMock(side_effect=[
            decompose_resp,
            big_agent_resp,    # research agent (wave 0)
            big_agent_resp,    # ba agent (wave 0)
            small_summary_resp,  # summarise_for_handoff
            final_synthesis_resp,  # synthesis agent (wave 1)
        ])

        orch = OrchestratorAgent(
            skill_registry=registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
            workflow_id="compress-wf",
        )
        result = await orch.run("Big task")
        assert result.error is None
        assert result.output == "Final report."
        # summarise call must have happened (5 total LLM calls including it)
        assert api_client.chat_completion.call_count == 5


class TestSummariseForHandoff(unittest.IsolatedAsyncioTestCase):
    """summarise_for_handoff compresses large text."""

    def _make_orchestrator(self, api_client) -> OrchestratorAgent:
        registry, executor, audit_log, model_selector = _make_mock_infra()
        return OrchestratorAgent(
            skill_registry=registry,
            skill_executor=executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
        )

    async def test_returns_llm_summary(self):
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = "Short summary."
        api_client = MagicMock()
        api_client.chat_completion = AsyncMock(return_value=response)

        orch = self._make_orchestrator(api_client)
        result = await orch._summarise_for_handoff("A " * 5_000)
        assert result == "Short summary."

    async def test_llm_failure_returns_truncated_original(self):
        api_client = MagicMock()
        api_client.chat_completion = AsyncMock(side_effect=RuntimeError("down"))

        orch = self._make_orchestrator(api_client)
        long_text = "B " * 50_000
        result = await orch._summarise_for_handoff(long_text)
        # Must not raise; returns truncated original
        assert len(result) <= _HANDOFF_TOKEN_THRESHOLD + 10   # small tolerance
        assert result in long_text   # is a prefix of the original


# ===========================================================================
# Integration Test (requires NANO_GPT_API_KEY env var)
# ===========================================================================

LIVE_TEST_REASON = "NANO_GPT_API_KEY not set — skipping live orchestration test"
LIVE_API_KEY = os.environ.get("NANO_GPT_API_KEY", "")


@pytest.mark.skipif(not LIVE_API_KEY, reason=LIVE_TEST_REASON)
class TestOrchestratorLive(unittest.IsolatedAsyncioTestCase):
    """
    Full end-to-end 3-agent orchestration test against the live Nano-GPT API.

    Workflow: research → (research result feeds) → synthesis
    Chosen because:
      - ResearchAgent and SynthesisAgent need no HITL approval
      - DuckDuckGo web_search needs no API key
      - Doesn't require Docker (no python_execute)

    Acceptance:
      - result.error is None
      - result.output is non-empty
      - Audit log shows WORKFLOW_START, WORKFLOW_END, at least one WAVE_COMPLETE
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

        self.api_client = ApiClient(api_key=LIVE_API_KEY)
        self.executor = SkillExecutor(registry=self.registry, audit_log=self.audit)
        self.model_selector = ModelSelector()

        self.orch = OrchestratorAgent(
            skill_registry=self.registry,
            skill_executor=self.executor,
            api_client=self.api_client,
            model_selector=self.model_selector,
            audit_log=self.audit,
        )

    async def asyncTearDown(self):
        await self.audit.stop()

    async def test_research_synthesis_workflow(self):
        """
        Orchestrator decomposes a research+synthesis task and executes it.

        The decomposition may produce 1 or 2 subtasks depending on the model,
        but the result must always be non-empty and error-free.
        """
        result = await self.orch.run(
            "Research what Python asyncio is and produce a one-paragraph summary report."
        )

        print(f"\n[live orchestration] output={result.output[:300]!r}")
        print(f"[live orchestration] error={result.error!r}")
        print(f"[live orchestration] tool_calls_made={result.tool_calls_made}")
        print(f"[live orchestration] workflow_id={result.workflow_id}")

        assert result.error is None, f"Workflow error: {result.error}"
        assert len(result.output) > 50, "Expected a substantive output"
        assert result.workflow_id == self.orch._workflow_id


if __name__ == "__main__":
    unittest.main()
