"""
BAAgent — Business Analysis specialist agent.

Wraps BaseAgent with the "ba_agent" persona (CBAP / BABOK v3).
Primary skills: knowledge_base_search, stakeholder_analysis, process_model,
                requirements_template, gap_analysis, vision_analyze.

Usage::

    agent = BAAgent.create(registry, executor, api_client, model_selector)
    result = await agent.run("Perform a stakeholder analysis for a CRM migration project.")
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


class BAAgent(BaseAgent):
    """Business Analysis specialist agent."""

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
    ) -> "BAAgent":
        return cls(
            persona=get_persona("ba_agent"),
            skill_registry=skill_registry,
            skill_executor=skill_executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
            approval_gate=approval_gate,
            workflow_id=workflow_id,
        )
