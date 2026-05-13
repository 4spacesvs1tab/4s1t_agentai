"""
SynthesisAgent — Technical writing and report generation specialist agent.

Wraps BaseAgent with the "synthesis_agent" persona.
Primary skills: report_generate, file_write.

Usage::

    agent = SynthesisAgent.create(registry, executor, api_client, model_selector)
    result = await agent.run("Write an executive summary from the following research: ...")
"""
from __future__ import annotations

from typing import Any

from agents.base_agent import BaseAgent
from agents.personas import get_persona
from core.api_client import ApiClient
from core.audit import AuditLog
from core.model_selector import ModelSelector
from skills.executor import SkillExecutor
from skills.registry import SkillRegistry


class SynthesisAgent(BaseAgent):
    """Technical writing and report generation specialist agent."""

    @classmethod
    def create(
        cls,
        skill_registry: SkillRegistry,
        skill_executor: SkillExecutor,
        api_client: ApiClient,
        model_selector: ModelSelector,
        audit_log: AuditLog | None = None,
        approval_gate: Any | None = None,
        workflow_id: str | None = None,
    ) -> "SynthesisAgent":
        return cls(
            persona=get_persona("synthesis_agent"),
            skill_registry=skill_registry,
            skill_executor=skill_executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
            approval_gate=approval_gate,
            workflow_id=workflow_id,
        )
