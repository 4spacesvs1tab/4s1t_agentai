"""
BaseAgent — the shared agent loop for all worker agent types.

The loop (FR-2, §3.4.2):
  1. Build messages = [system_prompt] + history + [task]
  2. Get tools = skill_registry.tools_for_agent(agent_type)
  3. POST to LLM (ApiClient, semaphore-limited)
  4. If finish_reason == "tool_calls":
       a. Execute each tool call (parallel gather) with scope + approval checks
       b. Append tool results to messages
       c. Compact context if >80% token budget
       d. Loop
  5. If finish_reason == "stop":
       Return AgentResult

Security:
  - Scope gate: SkillExecutor.execute() raises SkillScopeError if agent not allowed
  - Second validation: persona.allowed_skills enforced before calling executor (belt+suspenders)
  - Secrets: pulled from env vars for declared secrets_required, never logged
  - Approval gate: required skills pause and await HITL confirmation before execution

Design reference: Design_aiAgentOrchestrationOfMany.md §3.4.2
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import Any

from agents.agent_result import AgentResult
from agents.personas import Persona
from core.api_client import ApiClient, _is_model_unavailable
from core.audit import AuditLog, AuditEventType
from core.model_selector import ModelSelector
from privacy.prompt_obfuscator import PromptObfuscator
from privacy.pii_session_state import PIISessionState
from skills.executor import SkillExecutor, SkillScopeError, SkillTimeoutError
from skills.registry import SkillRegistry
from utils.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_AGENT_STEPS = 30   # safety ceiling on tool-call iterations per agent run

# Compact after this many messages regardless of token budget.
# Keeps per-call latency predictable and prevents runaway context growth.
_MAX_MESSAGES_BEFORE_COMPACT = 22

# Rough token limits per model (characters ÷ 4 ≈ tokens).
# Used only for the 80 % safety-net threshold (don't hit the hard context limit).
# The message-count threshold (_MAX_MESSAGES_BEFORE_COMPACT) fires first for
# normal conversations.
_MODEL_CONTEXT_TOKENS: dict[str, int] = {
    "deepseek-r1":                    64_000,
    "deepseek-v3.2":                  64_000,
    "deepseek-v3.1":                  64_000,
    "glm-4.6":                       128_000,
    "glm-4.5":                       128_000,
    "glm-4.7":                       128_000,
    "zai-org/glm-4.7":               128_000,
    "kimi-k2-0905":                  131_072,
    "kimi-k2-0711":                  131_072,
    "moonshotai/kimi-k2.5:thinking": 131_072,
    "moonshotai/kimi-k2.5":          131_072,
    "qwen3-coder":                    32_768,
}
_DEFAULT_CONTEXT_TOKENS = 32_768   # conservative fallback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 characters per token."""
    return max(1, len(text) // 4)


def _msg_content(msg: Any) -> str:
    """Extract text content from a message (dict or SDK object)."""
    if isinstance(msg, dict):
        return str(msg.get("content") or "")
    return str(getattr(msg, "content", "") or "")


def _count_messages_tokens(messages: list[Any]) -> int:
    return sum(_estimate_tokens(_msg_content(m)) for m in messages)


def _extract_message_content(msg: Any) -> str:
    """Extract text content from an LLM response message.

    Handles the standard ``content`` field and thinking-model variants
    (e.g. moonshotai/kimi-k2.5:thinking) that may return the final answer
    in ``reasoning_content`` when ``content`` is null/empty.
    """
    content = getattr(msg, "content", None)
    if content:
        return content

    # Fallback: Pydantic v2 stores unknown API fields in model_extra;
    # some SDKs also set them directly as attributes.
    model_extra: dict = getattr(msg, "model_extra", None) or {}
    reasoning = (
        getattr(msg, "reasoning_content", None)
        or model_extra.get("reasoning_content")
    )
    if reasoning:
        logger.debug(
            "content field empty; falling back to reasoning_content "
            f"({len(reasoning)} chars)"
        )
        return reasoning
    return ""


def _tool_call_to_dict(tc: Any) -> dict:
    """Convert an openai ToolCall object to a plain dict for messages[]."""
    return {
        "id": tc.id,
        "type": "function",
        "function": {
            "name": tc.function.name,
            "arguments": tc.function.arguments,
        },
    }


def _assistant_msg_to_dict(msg: Any) -> dict:
    """Convert openai ChatCompletionMessage → plain dict for messages[]."""
    d: dict = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [_tool_call_to_dict(tc) for tc in msg.tool_calls]
    return d


# ---------------------------------------------------------------------------
# Conversation history parser
# ---------------------------------------------------------------------------

def _parse_conversation_history(context: str) -> list[dict] | None:
    """
    Parse a context string formatted as 'role: content\\n...' into message dicts.

    Returns a list of {role, content} dicts if the string looks like conversation
    history, or None if it looks like free-form inter-agent context.
    """
    import re as _re
    # Must start with "user: " or "assistant: " (after optional instruction block)
    stripped = context.lstrip()
    # Skip leading instruction blocks like [INSTRUCTION: ...]
    stripped = _re.sub(r'^\[INSTRUCTION:[^\]]*\]\s*', '', stripped, flags=_re.DOTALL)
    if not _re.match(r'^(user|assistant):', stripped.strip()):
        return None

    msgs: list[dict] = []
    # Split on role boundaries — each new "user: " or "assistant: " starts a new turn
    parts = _re.split(r'\n(?=(?:user|assistant):)', stripped.strip())
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = _re.match(r'^(user|assistant):\s*(.*)', part, _re.DOTALL)
        if not m:
            return None  # Unexpected format — fall back to legacy
        role, content = m.group(1), m.group(2).strip()
        if content:
            msgs.append({"role": role, "content": content})

    return msgs if msgs else None


# ---------------------------------------------------------------------------
# BaseAgent
# ---------------------------------------------------------------------------

class BaseAgent:
    """
    Generic LLM agent with tool-calling loop, context management, and audit.

    Instantiate directly (for tests) or via the typed subclass wrappers
    (BAAgent, DataAgent, etc.) which pre-configure the Persona.
    """

    def __init__(
        self,
        persona: Persona,
        skill_registry: SkillRegistry,
        skill_executor: SkillExecutor,
        api_client: ApiClient,
        model_selector: ModelSelector,
        audit_log: AuditLog | None = None,
        approval_gate: Any | None = None,   # agents.approval.ApprovalGate (avoids circular import)
        workflow_id: str | None = None,
        wave_number: int | None = None,
        agent_index: int | None = None,
        pii_session_state: PIISessionState | None = None,
        user_pii_scrubbing: bool = False,
        model_preference_override: str | None = None,
        extra_skill_grants: frozenset[str] = frozenset(),
    ) -> None:
        self._persona = persona
        self._registry = skill_registry
        self._executor = skill_executor
        self._api_client = api_client
        self._model_selector = model_selector
        self._audit_log = audit_log
        self._approval_gate = approval_gate
        self._workflow_id = workflow_id or str(uuid.uuid4())
        self._wave_number = wave_number or 0
        self._agent_index = agent_index or 0
        self._pii_session_state: PIISessionState = pii_session_state or PIISessionState()
        self._user_pii_scrubbing: bool = user_pii_scrubbing
        self._model_preference_override: str | None = model_preference_override
        self._extra_skill_grants: frozenset[str] = extra_skill_grants
        self._obfuscator = PromptObfuscator()

        # Agent loop state (reset on each run() call)
        self._messages: list[Any] = []
        self._tool_calls_made: int = 0
        self._context_compactions: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, task: str, context: str = "") -> AgentResult:
        """
        Execute the agent loop for the given task.

        Args:
            task:    The natural language task description.
            context: Optional context from a prior agent's output (used
                     in orchestrated workflows to pass intermediate results).

        Returns:
            AgentResult with the final output or error.
        """
        agent_type = self._persona.agent_type
        preference = self._model_preference_override or self._persona.model_preference
        model_ids = self._model_selector.select_ordered(preference)
        if not model_ids:
            model_ids = [self._model_selector.select(preference)]
        model_id = model_ids[0]
        model_idx = 0   # index into model_ids; advances on per-model failures

        logger.info(
            f"[{agent_type}] SUBTASK_START  workflow={self._workflow_id}  "
            f"model={model_id}  task_len={len(task)}  task_desc={task[:80]!r}..."
        )
        await self._audit(AuditEventType.AGENT_SPAWN, target=agent_type,
                          metadata={"workflow_id": self._workflow_id, "model": model_id})

        # Build initial messages — use obfuscated system prompt to break
        # static provider fingerprinting (privacy requirement §3).
        system_prompt = self._obfuscator.randomize(self._persona.system_prompt_variants)
        self._messages = [{"role": "system", "content": system_prompt}]
        self._tool_calls_made = 0
        self._context_compactions = 0

        if context:
            # If context is formatted conversation history ("role: content\n..." lines),
            # inject it as real user/assistant turns so the agent treats it as chat memory.
            # Otherwise fall back to the legacy inter-agent handoff label.
            _history_msgs = _parse_conversation_history(context)
            if _history_msgs:
                self._messages.extend(_history_msgs)
            else:
                self._messages.append({
                    "role": "user",
                    "content": f"[Context from previous step]\n{context}",
                })

        # PII detection gate (privacy requirement §3) ----------------------
        # Always runs detection. Scrubbing and/or alerting depend on settings.
        task = await self._apply_pii_gate(task, agent_type)
        if task is None:
            # User aborted the task via NIP-17 choice or timeout
            return AgentResult(
                agent_type=agent_type,
                output="",
                tool_calls_made=0,
                context_compactions=0,
                workflow_id=self._workflow_id,
                wave_number=self._wave_number,
                agent_index=self._agent_index,
                error="Task aborted: PII detected and user chose to abort (or approval timed out).",
            )
        # ------------------------------------------------------------------

        self._messages.append({"role": "user", "content": task})

        # Get tools for this agent type; skip if provider doesn't support function calling
        tools = self._registry.tools_for_agent(agent_type, extra_skill_names=self._extra_skill_grants or None)
        if tools and not getattr(self._api_client, "supports_tools", True):
            logger.debug(f"[{agent_type}] provider does not support tools — skipping {len(tools)} tool(s)")
            tools = []

        # Agent loop
        for step in range(_MAX_AGENT_STEPS):
            # --- LLM call with model fallback ---
            # On PermissionDeniedError / NotFoundError try the next model in the
            # ordered list (primary → fallbacks defined in providers.yaml).
            # api_client already retried 3× with Tor rotation before raising here.
            response = None
            last_exc: Exception | None = None
            while model_idx < len(model_ids):
                try:
                    response = await self._api_client.chat_completion(
                        messages=self._messages,
                        model=model_id,
                        tools=tools if tools else None,
                        tool_choice="auto" if tools else None,
                    )
                    break  # success
                except Exception as exc:
                    last_exc = exc
                    if _is_model_unavailable(exc) and model_idx + 1 < len(model_ids):
                        model_idx += 1
                        model_id = model_ids[model_idx]
                        logger.warning(
                            f"[{agent_type}] Model {model_ids[model_idx - 1]!r} unavailable "
                            f"({exc.__class__.__name__}), falling back to {model_id!r}"
                        )
                        continue
                    break  # non-recoverable or no more fallbacks

            if response is None:
                exc = last_exc or RuntimeError("No LLM response")
                logger.error(f"[{agent_type}] LLM call failed at step {step}: {exc}")
                await self._audit(AuditEventType.AGENT_ERROR,
                                  target=agent_type,
                                  metadata={"step": step, "error": str(exc)})
                return AgentResult(
                    agent_type=agent_type,
                    output="",
                    tool_calls_made=self._tool_calls_made,
                    context_compactions=self._context_compactions,
                    workflow_id=self._workflow_id,
                    wave_number=self._wave_number,
                    agent_index=self._agent_index,
                    error=f"LLM call failed: {exc}",
                )

            choice = response.choices[0]
            finish_reason = choice.finish_reason

            if finish_reason == "stop" or finish_reason is None:
                output = _extract_message_content(choice.message)
                logger.info(
                    f"[{agent_type}] SUBTASK_COMPLETE  workflow={self._workflow_id}  "
                    f"step={step}  tool_calls={self._tool_calls_made}  "
                    f"compactions={self._context_compactions}  "
                    f"output_len={len(output)}"
                )
                return AgentResult(
                    agent_type=agent_type,
                    output=output,
                    tool_calls_made=self._tool_calls_made,
                    context_compactions=self._context_compactions,
                    workflow_id=self._workflow_id,
                    wave_number=self._wave_number,
                    agent_index=self._agent_index,
                )

            if finish_reason == "tool_calls":
                tool_calls = choice.message.tool_calls
                if not tool_calls:
                    # Malformed response — treat as stop
                    logger.warning(f"[{agent_type}] finish_reason=tool_calls but no tool_calls in message")
                    return AgentResult(
                        agent_type=agent_type,
                        output=_extract_message_content(choice.message),
                        tool_calls_made=self._tool_calls_made,
                        context_compactions=self._context_compactions,
                        workflow_id=self._workflow_id,
                    )

                # Add assistant message (with tool_calls) to history
                self._messages.append(_assistant_msg_to_dict(choice.message))

                # Execute tool calls in parallel
                tool_results = await asyncio.gather(
                    *[self._execute_tool_call(tc, agent_type, model_id) for tc in tool_calls],
                    return_exceptions=False,
                )

                # Add tool results to messages
                for tc, (result_content, _) in zip(tool_calls, tool_results):
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_content,
                    })
                    self._tool_calls_made += 1

                # Compact context if approaching token limit
                await self._maybe_compact_context(model_id)
                continue

            if finish_reason == "length":
                logger.warning(f"[{agent_type}] context length exceeded at step {step}")
                return AgentResult(
                    agent_type=agent_type,
                    output=_extract_message_content(choice.message),
                    tool_calls_made=self._tool_calls_made,
                    context_compactions=self._context_compactions,
                    workflow_id=self._workflow_id,
                    error="Context length exceeded",
                )

            # Unknown finish_reason — stop safely
            logger.warning(f"[{agent_type}] unexpected finish_reason={finish_reason!r} at step {step}")
            return AgentResult(
                agent_type=agent_type,
                output=_extract_message_content(choice.message),
                tool_calls_made=self._tool_calls_made,
                context_compactions=self._context_compactions,
                workflow_id=self._workflow_id,
            )

        # Exceeded _MAX_AGENT_STEPS
        logger.error(f"[{agent_type}] exceeded max steps ({_MAX_AGENT_STEPS})")
        return AgentResult(
            agent_type=agent_type,
            output="",
            tool_calls_made=self._tool_calls_made,
            context_compactions=self._context_compactions,
            workflow_id=self._workflow_id,
            error=f"Agent exceeded maximum step limit ({_MAX_AGENT_STEPS})",
        )

    # ------------------------------------------------------------------
    # Tool call execution
    # ------------------------------------------------------------------

    async def _execute_tool_call(
        self,
        tool_call: Any,
        agent_type: str,
        model_id: str,
    ) -> tuple[str, bool]:
        """
        Execute one tool call from the LLM.

        Returns:
            (result_json_str, success_bool)
        """
        skill_name = tool_call.function.name

        # Guard against malformed tool calls with empty function names (model bug)
        if not skill_name:
            err = "LLM returned a tool call with empty function name — ignoring malformed call."
            logger.warning(f"[{agent_type}] {err}")
            return err, False

        # Parse arguments
        try:
            parameters = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError as exc:
            err = f"Invalid JSON arguments for skill '{skill_name}': {exc}"
            logger.warning(f"[{agent_type}] {err}")
            return err, False

        # Belt-and-suspenders scope check before reaching executor
        if skill_name not in self._persona.allowed_skills and skill_name not in self._extra_skill_grants:
            err = (
                f"Skill '{skill_name}' is not in the allowed_skills list for "
                f"agent '{agent_type}'. Allowed: {self._persona.allowed_skills}"
            )
            logger.warning(f"[{agent_type}] SCOPE BLOCK: {err}")
            return err, False

        # HITL approval gate (FR-15)
        meta = None
        try:
            meta = self._registry.get(skill_name)
        except KeyError:
            err = f"Skill '{skill_name}' is not registered in the SkillRegistry."
            logger.warning(f"[{agent_type}] {err}")
            return err, False

        needs_approval = meta.requires_approval or (skill_name in self._persona.requires_approval)
        if needs_approval:
            approved = await self._request_approval(skill_name, parameters, agent_type)
            if not approved:
                msg = f"Skill '{skill_name}' execution denied by HITL approval gate."
                logger.info(f"[{agent_type}] {msg}")
                return msg, False

        # Collect secrets declared by the skill
        secrets: dict[str, str] = {}
        for secret_name in meta.secrets_required:
            value = os.getenv(secret_name)
            if value:
                secrets[secret_name] = value

        # Execute
        try:
            output = await self._executor.execute(
                skill_name=skill_name,
                parameters=parameters,
                calling_agent_type=agent_type,
                secrets=secrets,
                extra_granted_skills=self._extra_skill_grants or None,
            )
        except SkillScopeError as exc:
            err = str(exc)
            logger.warning(f"[{agent_type}] SkillScopeError: {err}")
            return err, False
        except SkillTimeoutError as exc:
            err = str(exc)
            logger.warning(f"[{agent_type}] SkillTimeoutError: {err}")
            return err, False
        except Exception as exc:
            err = f"Skill '{skill_name}' execution error: {exc}"
            logger.error(f"[{agent_type}] {err}", exc_info=True)
            return err, False

        if output.success:
            result_str = json.dumps(output.result, default=str) if output.result is not None else ""
            return result_str, True
        else:
            return output.error or f"Skill '{skill_name}' failed with no error message.", False

    # ------------------------------------------------------------------
    # HITL approval
    # ------------------------------------------------------------------

    async def _request_approval(
        self,
        skill_name: str,
        parameters: dict,
        agent_type: str,
    ) -> bool:
        """
        Request HITL approval before executing a sensitive skill.

        If no approval_gate is configured, defaults to APPROVED (logs a warning).
        """
        if self._approval_gate is None:
            logger.warning(
                f"[{agent_type}] Skill '{skill_name}' requires approval but no "
                f"ApprovalGate is configured — proceeding without approval."
            )
            return True

        try:
            approved = await self._approval_gate.request_approval(
                skill_name=skill_name,
                parameters=parameters,
                agent_type=agent_type,
                workflow_id=self._workflow_id,
            )
        except Exception as exc:
            logger.error(f"[{agent_type}] ApprovalGate error for '{skill_name}': {exc}")
            return False

        await self._audit(
            "SKILL_APPROVAL_GRANTED" if approved else "SKILL_APPROVAL_DENIED",
            actor=agent_type,
            target=skill_name,
            metadata={"workflow_id": self._workflow_id},
        )
        return approved

    # ------------------------------------------------------------------
    # Context compaction (FR-6, §3.6)
    # ------------------------------------------------------------------

    async def _maybe_compact_context(self, model_id: str) -> None:
        """
        Summarise the middle portion of message history when either:
          - message count exceeds _MAX_MESSAGES_BEFORE_COMPACT (performance guardrail), OR
          - token usage exceeds 80% of the model's context limit (safety net).

        Keeps: messages[0] (system prompt) + messages[-4:] (last 4 turns).
        Replaces everything in between with a single summary message.
        The compaction LLM call always uses the 'fast' model to minimise latency.
        """
        msg_count = len(self._messages)
        limit = _MODEL_CONTEXT_TOKENS.get(model_id, _DEFAULT_CONTEXT_TOKENS)
        current = _count_messages_tokens(self._messages)

        over_message_limit = msg_count > _MAX_MESSAGES_BEFORE_COMPACT
        over_token_limit = current >= int(limit * 0.80)

        if not over_message_limit and not over_token_limit:
            return

        # Need at least system + 4 turns + something in the middle to compact
        if msg_count <= 6:
            return

        system_msg = self._messages[0]
        last_four = self._messages[-4:]
        middle = self._messages[1:-4]

        middle_text = "\n".join(
            f"{_msg_content(m)[:1000]}"
            for m in middle
        )

        trigger = "msg_limit" if over_message_limit else "token_limit"
        logger.info(
            f"[{self._persona.agent_type}] Compacting context ({trigger}): "
            f"{len(middle)} messages → 1 summary  "
            f"(msgs={msg_count}, tokens ~{current}/{limit})"
        )

        # Always use the fast model for compaction — avoid slow thinking models here.
        compact_model = self._model_selector.select("fast")
        try:
            summary_response = await self._api_client.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a conversation summariser."},
                    {
                        "role": "user",
                        "content": (
                            "Summarise the following conversation in ≤500 tokens, "
                            "preserving all key facts, decisions, tool results, and data:\n\n"
                            + middle_text
                        ),
                    },
                ],
                model=compact_model,
            )
            summary = summary_response.choices[0].message.content or "(summary unavailable)"
        except Exception as exc:
            logger.error(f"Context compaction LLM call failed: {exc}")
            # Keep messages as-is rather than corrupting state
            return

        self._messages = (
            [system_msg]
            + [{"role": "assistant", "content": f"[Context summary: {summary}]"}]
            + last_four
        )
        self._context_compactions += 1
        logger.info(f"[{self._persona.agent_type}] Context compacted (compaction #{self._context_compactions})")

    # ------------------------------------------------------------------
    # PII gate (privacy requirement §3)
    # ------------------------------------------------------------------

    async def _apply_pii_gate(self, task: str, agent_type: str) -> str | None:
        """
        Run PII detection on *task* and apply scrubbing or alert logic.

        Returns:
            str  — the (possibly scrubbed) task text to use for the LLM call.
            None — the task should be aborted (user chose abort or alert timed out).
        """
        from config.privacy_config import get_privacy_config
        from privacy.pii_scrubber import PIIScrubber, format_pii_summary
        from services.approval_gateway import request_multichoice_approval

        privacy = get_privacy_config()
        if not privacy.enabled:
            return task

        scrubber = PIIScrubber()
        matches = scrubber.detect(task)

        if not matches:
            return task

        tier1 = [m for m in matches if m.tier == 1]
        tier2 = [m for m in matches if m.tier == 2]

        scrubbing_on = self._pii_session_state.scrub_session or self._user_pii_scrubbing

        should_alert = (
            (privacy.pii_tier1_always_alert and tier1)
            or (
                not scrubbing_on
                and len(tier2) >= privacy.pii_tier2_alert_threshold
            )
        )

        if should_alert and not self._pii_session_state.approved_proceed:
            found_summary = format_pii_summary(matches)
            status = "ON" if scrubbing_on else "OFF"
            choice = await request_multichoice_approval(
                title=f"PII DETECTED — scrubbing is {status}",
                body=(
                    f"Found in task:\n{found_summary}\n\n"
                    f"Task preview: {task[:100]}{'...' if len(task) > 100 else ''}"
                ),
                options=[
                    "1 → proceed as-is (remember for this workflow)",
                    "2 → scrub PII for this task only, then proceed",
                    "3 → enable scrubbing for the rest of this workflow",
                    "4 → abort this task",
                ],
                timeout=privacy.pii_approval_timeout,
            )

            if choice is None or choice == 4:
                logger.warning(
                    f"[{agent_type}] PII gate: task aborted "
                    f"(choice={choice}, workflow={self._workflow_id})"
                )
                return None
            elif choice == 1:
                self._pii_session_state.approved_proceed = True
                logger.info(f"[{agent_type}] PII gate: proceed as-is approved for workflow")
                return task
            elif choice == 2:
                scrubbing_on = True
                logger.info(f"[{agent_type}] PII gate: scrub this task only")
            elif choice == 3:
                self._pii_session_state.scrub_session = True
                scrubbing_on = True
                logger.info(f"[{agent_type}] PII gate: scrubbing enabled for remainder of workflow")

        if scrubbing_on and matches:
            scrubbed, rev_map = scrubber.scrub(task, matches)
            self._pii_session_state.reverse_maps.append(rev_map)
            logger.info(
                f"[{agent_type}] PII gate: scrubbed {len(matches)} instance(s) "
                f"({len(tier1)} tier-1, {len(tier2)} tier-2)"
            )
            return scrubbed

        return task

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    async def _audit(
        self,
        event_type: str,
        actor: str | None = None,
        target: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        if self._audit_log is None:
            return
        try:
            await self._audit_log.log(
                event_type=event_type,
                actor=actor or self._persona.agent_type,
                target=target,
                metadata=metadata,
            )
        except Exception as exc:
            logger.error(f"AuditLog write failed: {exc}")
