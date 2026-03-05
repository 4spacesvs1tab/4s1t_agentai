"""
AgentInfrastructure — shared singletons for the agent pipeline.

Instantiated once at startup (app lifespan) and stored on app.state.agent_infra.
Route handlers call create_orchestrator() from agents.factory to build a
per-request OrchestratorAgent using these shared resources.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.api_client import ApiClient
from core.audit import AuditLog
from skills.executor import SkillExecutor
from skills.registry import SkillRegistry
from utils.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class AgentInfrastructure:
    """Shared singletons wired together at startup."""

    skill_registry: SkillRegistry
    skill_executor: SkillExecutor
    api_client: ApiClient
    audit_log: AuditLog


async def create_agent_infrastructure() -> AgentInfrastructure:
    """
    Build and wire all shared agent singletons.

    Must be called *after* ``audit_log.start()`` has been awaited so that the
    AuditLog background writer is already running.

    Returns:
        A fully initialised AgentInfrastructure ready for use by route handlers.
    """
    from skills.registry import get_skill_registry
    from core.api_client import get_api_client
    from core.audit import get_audit_log

    registry = get_skill_registry()
    audit_log = get_audit_log()
    api_client = await get_api_client()
    executor = SkillExecutor(registry=registry, audit_log=audit_log)

    logger.info(
        f"AgentInfrastructure created — "
        f"skills={len(registry._skills)}  "
        f"base_url={api_client._base_url!r}"
    )

    return AgentInfrastructure(
        skill_registry=registry,
        skill_executor=executor,
        api_client=api_client,
        audit_log=audit_log,
    )
