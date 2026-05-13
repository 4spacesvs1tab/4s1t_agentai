"""
DataAgent — Data analysis specialist agent.

Wraps BaseAgent with the "data_agent" persona (pandas/numpy/plotly).
Primary skills: python_execute, data_read, chart_generate, export_results.

Usage::

    agent = DataAgent.create(registry, executor, api_client, model_selector)
    result = await agent.run("Analyse sales.csv and produce a monthly trend chart.")
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


class DataAgent(BaseAgent):
    """Data analysis specialist agent."""

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
    ) -> "DataAgent":
        return cls(
            persona=get_persona("data_agent"),
            skill_registry=skill_registry,
            skill_executor=skill_executor,
            api_client=api_client,
            model_selector=model_selector,
            audit_log=audit_log,
            approval_gate=approval_gate,
            workflow_id=workflow_id,
        )
