"""
AgentResult — output model returned by BaseAgent.run().
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class AgentResult(BaseModel):
    """The output of a completed agent run."""

    agent_type: str
    output: str                          # Final response text from the LLM
    tool_calls_made: int = 0             # Total successful tool calls executed
    context_compactions: int = 0         # Number of context compaction steps performed
    workflow_id: Optional[str] = None    # Set by the orchestrator for multi-agent workflows
    error: Optional[str] = None          # Set if the agent terminated with an unrecoverable error
    metadata: dict[str, Any] = Field(default_factory=dict)
    wave_number: int = 0                 # Wave number in orchestration workflow
    agent_index: int = 0                 # Index of agent within its wave
