"""
OrchestratorAgent — multi-agent workflow coordinator (Phase 4).

Responsibilities:
  1. Receive a user task.
  2. Decompose it into a TaskGraph using an LLM call (decompose_task).
  3. Execute the graph wave-by-wave:
       - Each wave runs its agents concurrently (asyncio.gather).
       - Inter-wave results are compressed if they exceed the handoff threshold
         (summarise_for_handoff).
  4. Single-subtask bypass: if decompose_task returns a graph with exactly one
     subtask (or returns None), skip orchestration overhead and spawn directly.
  5. Log WORKFLOW_START, WAVE_COMPLETE, and WORKFLOW_END audit events.

Built-in tools (trusted Python coroutines, NOT sandboxed skills):
  - decompose_task   : LLM → TaskGraph JSON → Pydantic parse (1 retry)
  - spawn_agent      : persona lookup → BaseAgent → await result
  - summarise_for_handoff : LLM compression for inter-wave context

Design reference: Design_aiAgentOrchestrationOfMany.md §3.5 (Phase 4)
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
import logging
from typing import Any

from pydantic import ValidationError

from agents.agent_result import AgentResult
from agents.base_agent import BaseAgent
from agents.personas import get_persona
from agents.task_graph import SubTask, TaskGraph
from core.api_client import ApiClient
from core.audit import AuditLog, AuditEventType
from core.model_selector import ModelSelector
from i18n._keywords import ALL_COMPLEX_KEYWORDS
from privacy.pii_session_state import PIISessionState
from skills.executor import SkillExecutor
from skills.registry import SkillRegistry
from utils.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Chars above which inter-wave results are compressed before the next wave.
# ~8 000 tokens × 4 chars/token
_HANDOFF_TOKEN_THRESHOLD = 8_000 * 4

# Number of LLM retry attempts when decompose_task gets invalid JSON
_MAX_DECOMPOSE_RETRIES = 1

# Prompt length above which a task is considered complex even without keywords
_TRIVIAL_MAX_LEN = 300

# Keywords (lowercase) that signal a task needs multi-agent orchestration.
# Checked against the lowercased prompt before the decomposition LLM call.
# ALL_COMPLEX_KEYWORDS is the union of every supported language's keyword set
# (see src/i18n/_keywords.py) so prompts in any language trigger complex routing.
_COMPLEX_KEYWORDS: frozenset[str] = ALL_COMPLEX_KEYWORDS

# 6H.1: Maximum number of subtasks allowed in a single decomposition.
# Protects against prompt injection that returns a huge task graph.
MAX_SUBTASKS = 10

# System prompt for the decomposition LLM call
_DECOMPOSE_SYSTEM_PROMPT = """\
You are a task decomposition engine. Given a user task, output ONLY a valid JSON \
object — no markdown fences, no explanation, no leading text. The JSON must match \
this schema exactly:

{
  "workflow_id": "<uuid string>",
  "subtasks": [
    {
      "task_id": "t1",
      "description": "<task description for the agent>",
      "agent_type": "research_agent",
      "model_preference": "general",
      "depends_on": []
    }
  ]
}

Rules:
- agent_type must be one of: research_agent, ba_agent, data_agent, synthesis_agent
- model_preference must be one of: reasoning, fast, coding, general
- Use depends_on (list of task_id strings) to encode sequential dependencies.
- Tasks with empty depends_on run in parallel.
- Use research_agent for web research and information gathering.
- Use ba_agent for business requirements, process modelling, gap analysis.
- Use data_agent for data manipulation, Python analysis, visualisation.
- Use synthesis_agent for composing final reports from prior agents' results.
- For a simple single-step task, output exactly one subtask.
- Do NOT wrap the JSON in markdown code blocks.\
"""


# ---------------------------------------------------------------------------
# OrchestratorAgent
# ---------------------------------------------------------------------------

class OrchestratorAgent:
    """
    Coordinates multi-agent workflows.

    Can be used standalone or wired into the FastAPI agent endpoints (Phase 5).

    Args:
        skill_registry:  Shared SkillRegistry (forwarded to spawned agents).
        skill_executor:  Shared SkillExecutor (forwarded to spawned agents).
        api_client:      AsyncOpenAI-compatible client (Nano-GPT).
        model_selector:  Maps model_preference keys → model_id strings.
        audit_log:       Optional AuditLog (skipped if None).
        approval_gate:   Optional HITL ApprovalGate (forwarded to spawned agents).
        workflow_id:     Override the auto-generated UUID workflow identifier.
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        skill_executor: SkillExecutor,
        api_client: ApiClient,
        model_selector: ModelSelector,
        audit_log: AuditLog | None = None,
        approval_gate: Any | None = None,
        workflow_id: str | None = None,
        user_pii_scrubbing: bool = False,
    ) -> None:
        self._registry = skill_registry
        self._executor = skill_executor
        self._api_client = api_client
        self._model_selector = model_selector
        self._audit_log = audit_log
        self._approval_gate = approval_gate
        self._workflow_id = workflow_id or str(uuid.uuid4())
        self._user_pii_scrubbing = user_pii_scrubbing
        # One PIISessionState shared across all agents in this workflow
        self._pii_session_state = PIISessionState()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, task: str, context: str = "") -> AgentResult:
        """
        Execute the full orchestration loop.

        1. Decompose task → TaskGraph (or None for trivial single-step).
        2. Single-subtask bypass if graph has ≤1 subtask.
        3. Otherwise: wave-by-wave parallel execution with optional handoff compression.

        Returns:
            AgentResult containing the accumulated output of all waves.
        """
        t0 = time.monotonic()
        logger.info(
            f"[orchestrator] run started  workflow={self._workflow_id}  "
            f"task_len={len(task)}"
        )
        await self._audit(
            AuditEventType.WORKFLOW_START,
            metadata={"workflow_id": self._workflow_id, "task_preview": task[:200]},
        )

        # ----------------------------------------------------------------
        # Step 0: Trivial-task fast path — skip decomposition entirely
        # ----------------------------------------------------------------
        if not self._is_complex(task):
            logger.info(
                f"[orchestrator] trivial task — skipping decomposition  "
                f"workflow={self._workflow_id}"
            )
            result = await self._spawn_agent("synthesis_agent", task, context)
            elapsed = time.monotonic() - t0
            await self._audit(
                AuditEventType.WORKFLOW_END,
                metadata={
                    "workflow_id": self._workflow_id,
                    "elapsed_s": round(elapsed, 3),
                    "bypass": True,
                    "reason": "trivial",
                },
            )
            result.workflow_id = self._workflow_id
            return result

        # ----------------------------------------------------------------
        # Step 1: Decompose
        # ----------------------------------------------------------------
        graph = await self._decompose_task(task, context)

        # ----------------------------------------------------------------
        # Step 2: Single-subtask bypass
        # ----------------------------------------------------------------
        if graph is None or len(graph.subtasks) <= 1:
            subtask = graph.subtasks[0] if graph and graph.subtasks else SubTask(
                task_id="t1",
                description=task,
                agent_type="research_agent",
                model_preference="general",
            )
            logger.info(
                f"[orchestrator] single-subtask bypass → "
                f"agent={subtask.agent_type}  workflow={self._workflow_id}"
            )
            result = await self._spawn_agent(subtask.agent_type, subtask.description, context)
            elapsed = time.monotonic() - t0
            await self._audit(
                AuditEventType.WORKFLOW_END,
                metadata={
                    "workflow_id": self._workflow_id,
                    "elapsed_s": round(elapsed, 3),
                    "bypass": True,
                },
            )
            result.workflow_id = self._workflow_id
            return result

        # ----------------------------------------------------------------
        # Step 3: Multi-wave parallel execution
        # ----------------------------------------------------------------
        try:
            waves = graph.topological_waves()
        except ValueError as exc:
            logger.error(f"[orchestrator] topological sort failed: {exc}")
            await self._audit(
                AuditEventType.WORKFLOW_END,
                metadata={"workflow_id": self._workflow_id, "error": str(exc)},
            )
            return AgentResult(
                agent_type="orchestrator",
                output="",
                workflow_id=self._workflow_id,
                error=f"Workflow DAG error: {exc}",
            )

        accumulated_context = context
        total_tool_calls = 0

        for wave_num, wave in enumerate(waves):
            wave_t0 = time.monotonic()
            agent_types = [t.agent_type for t in wave]
            logger.info(
                f"[orchestrator] wave {wave_num}  agents={agent_types}  "
                f"workflow={self._workflow_id}"
            )

            # Run all tasks in this wave concurrently
            results: list[AgentResult] = await asyncio.gather(
                *[
                    self._spawn_agent(t.agent_type, t.description, accumulated_context, wave_num, idx)
                    for idx, t in enumerate(wave)
                ]
            )

            wave_elapsed = time.monotonic() - wave_t0
            total_tool_calls += sum(r.tool_calls_made for r in results)

            await self._audit(
                AuditEventType.WAVE_COMPLETE,
                metadata={
                    "workflow_id": self._workflow_id,
                    "wave": wave_num,
                    "agents": agent_types,
                    "elapsed_s": round(wave_elapsed, 3),
                },
            )

            # Combine results from this wave
            combined = "\n\n".join(r.output for r in results if r.output)

            # Compress if context would bloat next wave's input
            if len(combined) > _HANDOFF_TOKEN_THRESHOLD:
                logger.info(
                    f"[orchestrator] wave {wave_num} output exceeds threshold "
                    f"({len(combined)} chars) — compressing for handoff"
                )
                accumulated_context = await self._summarise_for_handoff(combined)
            else:
                accumulated_context = combined

        # ----------------------------------------------------------------
        # Done
        # ----------------------------------------------------------------
        elapsed = time.monotonic() - t0
        await self._audit(
            AuditEventType.WORKFLOW_END,
            metadata={
                "workflow_id": self._workflow_id,
                "elapsed_s": round(elapsed, 3),
                "waves": len(waves),
                "total_tool_calls": total_tool_calls,
            },
        )
        logger.info(
            f"[orchestrator] finished  workflow={self._workflow_id}  "
            f"elapsed={elapsed:.2f}s  waves={len(waves)}"
        )

        return AgentResult(
            agent_type="orchestrator",
            output=accumulated_context,
            tool_calls_made=total_tool_calls,
            workflow_id=self._workflow_id,
        )

    # ------------------------------------------------------------------
    # Complexity heuristic
    # ------------------------------------------------------------------

    @staticmethod
    def _is_complex(task: str) -> bool:
        """
        Return True if the task warrants full decomposition.

        A task is considered complex if EITHER:
        - its length exceeds _TRIVIAL_MAX_LEN characters, OR
        - it contains at least one keyword from _COMPLEX_KEYWORDS.

        This avoids burning a slow reasoning-model call on trivial prompts
        like "Who are you?" or "Hello".
        """
        if len(task) > _TRIVIAL_MAX_LEN:
            return True
        lowered = task.lower()
        return any(kw in lowered for kw in _COMPLEX_KEYWORDS)

    # ------------------------------------------------------------------
    # Built-in: decompose_task
    # ------------------------------------------------------------------

    async def _decompose_task(
        self,
        task: str,
        context: str,
    ) -> TaskGraph | None:
        """
        Ask the reasoning model to decompose the task into a TaskGraph.

        Returns:
            TaskGraph on success, or None if parsing fails after retries.
        """
        model_id = self._model_selector.select("reasoning")
        messages: list[dict] = [
            {"role": "system", "content": _DECOMPOSE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Task: {task}"
                    + (f"\n\nContext from previous steps:\n{context[:2000]}" if context else "")
                ),
            },
        ]

        last_error: str | None = None

        for attempt in range(_MAX_DECOMPOSE_RETRIES + 1):
            if attempt > 0 and last_error:
                # Append a correction prompt on retry
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous response could not be parsed: {last_error}\n"
                        "Please output ONLY the raw JSON object, no markdown, no explanation."
                    ),
                })

            try:
                response = await self._api_client.chat_completion(
                    messages=messages,
                    model=model_id,
                )
            except Exception as exc:
                logger.error(f"[orchestrator] decompose_task LLM call failed: {exc}")
                return None

            raw = (response.choices[0].message.content or "").strip()

            # Strip accidental markdown fences
            if raw.startswith("```"):
                lines = raw.splitlines()
                raw = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                ).strip()

            try:
                # Inject workflow_id if the model omitted it
                data = json.loads(raw)
                if "workflow_id" not in data or not data["workflow_id"]:
                    data["workflow_id"] = self._workflow_id
                graph = TaskGraph.model_validate(data)
                # 6H.1: Guard against decompositions that are too large
                if len(graph.subtasks) > MAX_SUBTASKS:
                    logger.warning(
                        f"[orchestrator] decompose_task returned {len(graph.subtasks)} "
                        f"subtasks — truncating to {MAX_SUBTASKS} (injection guard)"
                    )
                    graph.subtasks = graph.subtasks[:MAX_SUBTASKS]
                logger.info(
                    f"[orchestrator] decomposed into {len(graph.subtasks)} subtask(s) "
                    f"on attempt {attempt}"
                )
                return graph
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = str(exc)
                logger.warning(
                    f"[orchestrator] decompose_task parse failed (attempt {attempt}): {exc}"
                )
                # Append LLM response to conversation so retry has context
                messages.append({"role": "assistant", "content": raw})

        logger.error(
            f"[orchestrator] decompose_task failed after {_MAX_DECOMPOSE_RETRIES + 1} "
            "attempts — falling back to single-agent bypass"
        )
        return None

    # ------------------------------------------------------------------
    # Built-in: spawn_agent
    # ------------------------------------------------------------------

    async def _spawn_agent(
        self,
        agent_type: str,
        task: str,
        context: str,
        wave_num: int = 0,
        agent_idx: int = 0,
    ) -> AgentResult:
        """
        Instantiate the requested worker agent and run it.

        Each spawned agent gets its own BaseAgent instance but shares the
        same infrastructure singletons (api_client, registry, executor, audit).
        The parent workflow_id, wave_number, and agent_index are forwarded for
        audit correlation and enhanced logging.

        Args:
            agent_type: The type of agent to spawn (e.g., "research_agent", "data_agent")
            task: The task description for the agent
            context: Context from previous steps
            wave_num: Wave number in the orchestration workflow (for logging)
            agent_idx: Index of agent within its wave (for logging)
        """
        try:
            persona = get_persona(agent_type)
        except KeyError as exc:
            logger.error(f"[orchestrator] unknown agent_type '{agent_type}': {exc}")
            return AgentResult(
                agent_type=agent_type,
                output="",
                workflow_id=self._workflow_id,
                error=f"Unknown agent type: {agent_type}",
            )

        agent = BaseAgent(
            persona=persona,
            skill_registry=self._registry,
            skill_executor=self._executor,
            api_client=self._api_client,
            model_selector=self._model_selector,
            audit_log=self._audit_log,
            approval_gate=self._approval_gate,
            workflow_id=self._workflow_id,
            wave_number=wave_num,
            agent_index=agent_idx,
            pii_session_state=self._pii_session_state,
            user_pii_scrubbing=self._user_pii_scrubbing,
        )

        try:
            result = await agent.run(task, context)
        except Exception as exc:
            logger.error(
                f"[orchestrator] spawn_agent '{agent_type}' raised unexpectedly: {exc}",
                exc_info=True,
            )
            result = AgentResult(
                agent_type=agent_type,
                output="",
                workflow_id=self._workflow_id,
                error=f"Unexpected error in spawned agent: {exc}",
            )

        result.workflow_id = self._workflow_id
        return result

    # ------------------------------------------------------------------
    # Built-in: summarise_for_handoff
    # ------------------------------------------------------------------

    async def _summarise_for_handoff(
        self,
        text: str,
        max_tokens: int = 2_000,
    ) -> str:
        """
        Compress inter-wave results to fit within the next wave's context budget.

        Falls back to returning the original text if the LLM call fails.
        """
        model_id = self._model_selector.select("fast")
        try:
            response = await self._api_client.chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a concise summarisation assistant.",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Summarise the following in ≤{max_tokens} tokens, "
                            "preserving ALL key facts, decisions, data values, "
                            "and conclusions:\n\n" + text
                        ),
                    },
                ],
                model=model_id,
            )
            summary = response.choices[0].message.content or text
            logger.info(
                f"[orchestrator] handoff summary: {len(text)} chars → {len(summary)} chars"
            )
            return summary
        except Exception as exc:
            logger.error(f"[orchestrator] summarise_for_handoff failed: {exc}")
            # Return truncated original rather than losing context entirely
            return text[:_HANDOFF_TOKEN_THRESHOLD]

    # ------------------------------------------------------------------
    # Audit helper
    # ------------------------------------------------------------------

    async def _audit(
        self,
        event_type: str,
        metadata: dict | None = None,
    ) -> None:
        if self._audit_log is None:
            return
        try:
            await self._audit_log.log(
                event_type=event_type,
                actor="orchestrator",
                target=self._workflow_id,
                metadata=metadata,
            )
        except Exception as exc:
            logger.error(f"[orchestrator] AuditLog write failed: {exc}")
