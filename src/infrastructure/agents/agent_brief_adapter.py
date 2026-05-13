"""
AgentBriefAdapter — infrastructure adapter implementing BriefGenerationPort
via the kb_monitor_agent BaseAgent.

Wraps the per-domain agent-invocation logic extracted from
kb/scheduling/brief_job.py (E5 refactor).

This file is allowed to import from agents/ — it lives in the infrastructure
layer. It must contain no domain logic: task prompt construction is considered
formatting/orchestration, not business logic.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from utils.logger import setup_logger
from kb.ports.brief_generation_port import BriefGenerationPort

if TYPE_CHECKING:
    from components.system.agent_infrastructure import AgentInfrastructure

logger = setup_logger(__name__)


class AgentBriefAdapter(BriefGenerationPort):
    """Generates domain briefs by running kb_monitor_agent as a BaseAgent.

    Creates a fresh BaseAgent per domain call to keep each session's context
    small (one domain's data, not all domains accumulated).
    """

    def __init__(self, agent_infra: "AgentInfrastructure") -> None:
        self._agent_infra = agent_infra

    async def generate_domain_brief(
        self,
        domain: str,
        user_id: str,
        today: str,
    ) -> str:
        """Run kb_monitor_agent to generate and write a brief for one domain.

        Args:
            domain: KB domain identifier.
            user_id: User for whom to generate the brief (user isolation).
            today: ISO date string e.g. "2026-04-15".

        Returns:
            Agent output text (may be empty string if the agent produced none).
        """
        from agents.base_agent import BaseAgent
        from agents.personas import get_persona
        from core.model_selector import ModelSelector
        from config.provider_config import get_active_provider

        infra = self._agent_infra
        selector = ModelSelector(provider_config=get_active_provider())
        persona = get_persona("kb_monitor_agent")

        task = (
            f"Generate an intelligence brief for the '{domain}' domain for today ({today}).\n"
            f"MANDATORY: pass user_id='{user_id}' to the knowledge_base_search call — "
            "without it you will get zero results due to user isolation.\n"
            f"Call knowledge_base_search with: query=<relevant topic for {domain}>, "
            f"domain='{domain}', n_results=5, since='24h', user_id='{user_id}'. "
            "If fewer than 3 results, retry with since='7d' and note the extended window in the brief.\n"
            f"Write the structured brief to briefs/{domain}_{today}.md using file_write. "
            "Format: Top Stories → Key Signals → Expert Predictions. "
            "Cite each item with author, published_age, and source_url.\n"
            "If knowledge_base_search returns 0 results even with 7d window, write a one-line "
            f"skip notice: 'No new {domain} content in the past 7 days. Brief skipped.'"
        )

        agent = BaseAgent(
            persona=persona,
            skill_registry=infra.skill_registry,
            skill_executor=infra.skill_executor,
            api_client=infra.api_client,
            model_selector=selector,
            audit_log=infra.audit_log,
        )
        result = await agent.run(task=task)
        return result.output or ""
