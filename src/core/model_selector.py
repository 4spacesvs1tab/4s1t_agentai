"""
ModelSelector — maps agent model_preference strings to provider model IDs.

Source of truth priority:
  1. Active provider's model mappings (config/providers.yaml via ProviderConfig)
  2. NanoGPTConfig.default_models (legacy fallback when provider config unavailable)

Usage::

    selector = ModelSelector()
    model_id = selector.select("reasoning")          # → "deepseek-r1" (nano_gpt default)
    model_id = selector.select("unknown")            # → "deepseek-v3.2" (general fallback)
    models   = selector.select_ordered("reasoning")  # → ["primary", "fallback", ...]

    # Override the "general" preference slot (user-selected model):
    selector = ModelSelector(provider_config=provider, override_model_id="some-model")
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from utils.logger import setup_logger

logger = setup_logger(__name__)

_FALLBACK_PREFERENCE = "general"
_HARDCODED_FALLBACK = "deepseek-v3.2"


class ModelSelector:
    """
    Maps a symbolic model_preference to a provider-specific model ID.

    When a ProviderConfig is supplied (preferred), model IDs come from the
    provider's YAML model mapping.  When only NanoGPTConfig is supplied
    (legacy path), behaviour is unchanged from Phase 1/2.
    """

    def __init__(
        self,
        provider_config: Any | None = None,    # config.provider_config.ProviderConfig
        nano_gpt_config: Any | None = None,    # config.nano_gpt_config.NanoGPTConfig
        override_model_id: str | None = None,  # overrides the "general" preference slot
    ) -> None:
        self._provider_config = provider_config
        self._nano_gpt_config = nano_gpt_config
        self._override_model_id = override_model_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_model_map(self) -> dict[str, str]:
        """Return the {preference: model_id} dict from whichever config is active."""
        if self._provider_config is not None:
            base = dict(self._provider_config.models)
            if self._override_model_id:
                base["general"] = self._override_model_id
            return base
        if self._nano_gpt_config is not None:
            return dict(self._nano_gpt_config.default_models)
        # Neither configured — try to load provider config dynamically
        try:
            from config.provider_config import get_active_provider
            self._provider_config = get_active_provider()
            base = dict(self._provider_config.models)
            if self._override_model_id:
                base["general"] = self._override_model_id
            return base
        except Exception as exc:
            logger.warning(f"ModelSelector: could not load provider config ({exc}), using empty map")
            return {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select(
        self,
        preference: str,
        allowed_models: Sequence[str] | None = None,
    ) -> str:
        """
        Resolve a model_preference to a provider-specific model ID.

        Args:
            preference:     Symbolic preference key (e.g. ``"reasoning"``).
            allowed_models: Optional explicit allowlist. When provided, the
                            resolved ID must be in this list; otherwise the
                            first allowed model is returned.

        Returns:
            A model ID string for the active provider.
        """
        model_map = self._resolve_model_map()

        # Resolve preference → model ID, falling back to "general" key
        model_id = model_map.get(preference)
        if model_id is None:
            logger.info(
                f"ModelSelector: unknown preference '{preference}', "
                f"falling back to '{_FALLBACK_PREFERENCE}'"
            )
            model_id = model_map.get(_FALLBACK_PREFERENCE, _HARDCODED_FALLBACK)

        # Subscription gate (NanoGPTConfig path only)
        if self._nano_gpt_config is not None and self._provider_config is None:
            if not self._nano_gpt_config.is_model_allowed(model_id):
                logger.warning(
                    f"ModelSelector: model '{model_id}' not allowed by subscription "
                    f"(tier={self._nano_gpt_config.subscription_tier}), falling back"
                )
                model_id = model_map.get(_FALLBACK_PREFERENCE, _HARDCODED_FALLBACK)

        # Optional caller-supplied allowlist gate
        if allowed_models is not None and model_id not in allowed_models:
            logger.warning(
                f"ModelSelector: model '{model_id}' not in caller allowlist "
                f"{list(allowed_models)}, using first allowed"
            )
            model_id = allowed_models[0] if allowed_models else _HARDCODED_FALLBACK

        logger.debug(f"ModelSelector: '{preference}' → '{model_id}'")
        return model_id

    def select_ordered(self, preference: str) -> list[str]:
        """
        Return the full ordered list of model IDs for *preference*
        (primary first, then fallbacks).

        When ``override_model_id`` is set and ``preference == "general"``,
        the override is prepended to the provider's fallback list.

        Falls back to a single-element list from ``select()`` on the legacy path.
        """
        if self._provider_config is not None:
            result = self._provider_config.model_ids(preference)
            if self._override_model_id and preference == "general":
                # Prepend override; keep provider models as fallbacks (deduplicated)
                rest = [m for m in result if m != self._override_model_id]
                return [self._override_model_id] + rest
            return result
        # Legacy / dynamic-load path — return single-item list
        primary = self.select(preference)
        return [primary] if primary else []

    def available_preferences(self) -> list[str]:
        """Return the list of valid model preference keys."""
        return list(self._resolve_model_map().keys())

    @property
    def provider_name(self) -> str:
        """Return the name of the active provider, or 'unknown'."""
        if self._provider_config is not None:
            return self._provider_config.name
        return "nano_gpt"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_model_selector: ModelSelector | None = None


def get_model_selector() -> ModelSelector:
    """
    Return the shared ModelSelector singleton.

    Uses the active provider's model mappings from providers.yaml.
    Falls back to NanoGPTConfig if provider config cannot be loaded.
    """
    global _model_selector
    if _model_selector is None:
        try:
            from config.provider_config import get_active_provider
            provider = get_active_provider()
            _model_selector = ModelSelector(provider_config=provider)
            logger.info(
                f"ModelSelector initialised — provider={provider.name!r}  "
                f"preferences={list(provider.models.keys())}"
            )
        except Exception as exc:
            logger.warning(
                f"ModelSelector: provider config unavailable ({exc}), "
                "falling back to NanoGPTConfig"
            )
            from config.nano_gpt_config import get_nano_gpt_config
            _model_selector = ModelSelector(nano_gpt_config=get_nano_gpt_config())
            logger.info(
                f"ModelSelector initialised (legacy fallback), "
                f"preferences: {_model_selector.available_preferences()}"
            )
    return _model_selector
