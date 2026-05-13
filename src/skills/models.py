"""
Pydantic models for the Skills Framework.

SkillMeta     — validated representation of a skill's meta.json
SkillInput    — what the SkillExecutor sends to the handler subprocess
SkillOutput   — what the handler subprocess returns
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# SkillMeta — mirrors the meta.json schema (FR-11)
# ---------------------------------------------------------------------------

class SkillMeta(BaseModel):
    """Validated representation of a skill's meta.json file."""

    name: str
    version: str
    description: str

    # Access control
    agent_scope: list[str] = Field(
        description="Agent types allowed to call this skill."
    )

    # Execution
    execution_mode: Literal["subprocess", "docker"] = "subprocess"
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    max_memory_mb: int = Field(default=256, ge=16, le=2048)

    # Permissions
    network_allowed: bool = False
    filesystem_access: Literal["none", "read", "read_write"] = "none"
    secrets_required: list[str] = Field(default_factory=list)

    # Approval gate (FR-15)
    requires_approval: bool = False

    # JSON Schema for input/output (used to build OpenAI tools format)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    # Internal — set by registry, not stored in meta.json
    handler_path: str = Field(default="", exclude=True)

    @model_validator(mode="after")
    def _validate_agent_scope(self) -> "SkillMeta":
        if not self.agent_scope:
            raise ValueError(f"Skill '{self.name}' must declare at least one agent_scope entry.")
        return self

    def is_allowed_for(self, agent_type: str) -> bool:
        """Return True if this skill is in scope for the given agent type."""
        return agent_type in self.agent_scope

    def to_openai_tool(self) -> dict[str, Any]:
        """
        Convert this skill's metadata to OpenAI function-calling format.

        The result is injected into the `tools` parameter of every LLM call
        for agents whose type is in agent_scope.
        """
        input_schema = self.input_schema or {
            "type": "object",
            "properties": {},
            "required": [],
        }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": input_schema,
            },
        }


# ---------------------------------------------------------------------------
# SkillInput — what the executor writes to input.json
# ---------------------------------------------------------------------------

class SkillInput(BaseModel):
    """Serialised input written to the handler subprocess's input.json."""

    skill_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    calling_agent_type: str


# ---------------------------------------------------------------------------
# SkillOutput — what handler.py writes to output.json
# ---------------------------------------------------------------------------

class SkillOutput(BaseModel):
    """
    Parsed output read from the handler subprocess's output.json.

    Every handler.py must produce this structure. If the handler crashes
    before writing output.json, the executor synthesises a failure output.
    """

    success: bool
    result: Any = None
    error: Optional[str] = None
    logs: list[str] = Field(default_factory=list)

    @classmethod
    def from_error(cls, message: str) -> "SkillOutput":
        return cls(success=False, error=message)
