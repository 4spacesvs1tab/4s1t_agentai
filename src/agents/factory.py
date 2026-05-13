"""
Agent factory — creates OrchestratorAgent instances from shared infrastructure.

Usage::

    # In a FastAPI route handler:
    from agents.factory import create_orchestrator

    orchestrator = create_orchestrator(
        infra=request.app.state.agent_infra,
        model_id="moonshotai/kimi-k2.5",
        provider_name="nano_gpt",
    )
    result = await orchestrator.run(task=message, context=history)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from components.system.agent_infrastructure import AgentInfrastructure

from agents.orchestrator import OrchestratorAgent
from core.api_client import ApiClient
from core.model_selector import ModelSelector
from config.provider_config import get_active_provider, load_all_providers
from utils.logger import setup_logger

logger = setup_logger(__name__)


def create_orchestrator(
    infra: "AgentInfrastructure",
    model_id: str | None = None,
    provider_name: str | None = None,
    user_pii_scrubbing: bool = False,
    extra_skill_grants: frozenset[str] = frozenset(),
) -> OrchestratorAgent:
    """
    Create an OrchestratorAgent using shared infrastructure singletons.

    The orchestrator is lightweight (no IO at init time) and is safe to
    create per-request.

    Args:
        infra:         Shared AgentInfrastructure from ``app.state.agent_infra``.
        model_id:      Concrete model ID that overrides the "general" agent
                       preference for this request (user-selected model).
                       Reasoning/fast/coding preferences are unaffected.
        provider_name: Provider key from providers.yaml.  Defaults to the
                       system active provider.  When different from the active
                       provider a fresh ApiClient is created for the request.

    Returns:
        A freshly instantiated OrchestratorAgent ready to call ``.run()``.
    """
    # Resolve provider config
    if provider_name:
        providers = load_all_providers()
        provider = providers.get(provider_name)
        if provider is None:
            # Backward compat: old saved prefs may store display_name instead of slug
            provider = next(
                (p for p in providers.values() if p.display_name == provider_name),
                None,
            )
            if provider is not None:
                logger.debug(
                    f"create_orchestrator: resolved display_name {provider_name!r} "
                    f"→ slug {provider.name!r}"
                )
        if provider is None:
            logger.warning(
                f"create_orchestrator: unknown provider {provider_name!r}, "
                "falling back to active provider"
            )
            provider = get_active_provider()
    else:
        provider = get_active_provider()

    # Build ModelSelector; model_id overrides the "general" preference slot
    selector = ModelSelector(provider_config=provider, override_model_id=model_id)

    # Reuse the shared ApiClient when using the active provider;
    # create a fresh one for a different provider (different base_url/api_key)
    active_provider = get_active_provider()
    if provider.name == active_provider.name:
        api_client = infra.api_client
    else:
        api_client = ApiClient(
            api_key=provider.api_key,
            base_url=provider.base_url,
            extra_headers=provider.extra_headers or None,
            ssl_verify=getattr(provider, "ssl_verify", True),
            skip_tor=getattr(provider, "skip_tor", False),
            supports_tools=getattr(provider, "supports_tools", True),
            max_concurrent=getattr(provider, "max_concurrent", 3),
            timeout=getattr(provider, "timeout", None),
        )
        logger.debug(
            f"create_orchestrator: fresh ApiClient for provider={provider.name!r}"
        )

    logger.debug(
        f"create_orchestrator: provider={provider.name!r}  "
        f"model_id={model_id!r}  "
        f"selector_preferences={selector.available_preferences()}"
    )

    return OrchestratorAgent(
        skill_registry=infra.skill_registry,
        skill_executor=infra.skill_executor,
        api_client=api_client,
        model_selector=selector,
        audit_log=infra.audit_log,
        user_pii_scrubbing=user_pii_scrubbing,
        extra_skill_grants=extra_skill_grants,
    )
