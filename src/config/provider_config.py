"""
Multi-provider LLM configuration for 4S1T Agent AI.

Loads provider definitions from providers.yaml and exposes the active
provider's settings (base_url, api_key, model mappings, extra_headers).

Active provider selection order:
  1. ACTIVE_PROVIDER environment variable
  2. `active:` key in providers.yaml
  3. Hard-coded fallback: "nano_gpt"

Usage::

    from config.provider_config import get_active_provider

    provider = get_active_provider()
    client = ApiClient(
        api_key=provider.api_key,
        base_url=provider.base_url,
        extra_headers=provider.extra_headers,
    )
    model_id = provider.model_id("reasoning")    # → primary model string
    models   = provider.model_ids("reasoning")   # → [primary, fallback, ...]
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from config.loader import load_yaml
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Relative to this file: src/config/providers.yaml
_PROVIDERS_YAML = Path(__file__).parent / "providers.yaml"

_FALLBACK_PROVIDER_NAME = "nano_gpt"
_FALLBACK_BASE_URL = "https://nano-gpt.com/api/v1"
_FALLBACK_API_KEY_ENV = "NANO_GPT_API_KEY"
_FALLBACK_MODELS: dict[str, str] = {
    "reasoning": "deepseek-r1",
    "fast":      "kimi-k2-0905",
    "coding":    "qwen3-coder",
    "general":   "deepseek-v3.2",
}

# catalog_strategy / model_filter defaults
_DEFAULT_CATALOG_STRATEGY = "api"
_DEFAULT_MODEL_FILTER = "all"


# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    """
    Runtime configuration for one LLM provider.

    All fields are populated from providers.yaml; the api_key is resolved
    lazily from the environment variable named by api_key_env.

    Model preferences are stored as ordered lists (primary + fallbacks) in
    ``agent_preferences``.  The legacy ``models`` field (single string per
    preference) is kept as a backward-compat shim and is kept in sync via
    ``__post_init__``.
    """

    name: str
    display_name: str
    base_url: str
    api_key_env: str
    # Legacy: single string per preference.  Kept for backward compat.
    models: dict[str, str] = field(default_factory=dict)
    # New: ordered list per preference (primary + fallbacks).
    agent_preferences: dict[str, list[str]] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)
    ssl_verify: bool = True          # set False for self-signed certs (e.g. local Start9)
    skip_tor: bool = False           # set True for local/LAN providers that Tor cannot reach
    supports_tools: bool = True      # set False for models that reject tool/function-call params
    max_concurrent: int = 3          # max parallel in-flight requests; use 1 for CPU-only Ollama
    timeout: int | None = None       # per-request timeout in seconds; None = SDK default (600s)
    catalog_strategy: str = _DEFAULT_CATALOG_STRATEGY  # "api" | "static"
    model_filter: str = _DEFAULT_MODEL_FILTER          # "all" | "free"

    def __post_init__(self) -> None:
        """Keep ``models`` and ``agent_preferences`` in sync."""
        if self.agent_preferences and not self.models:
            # New format supplied → derive legacy compat dict
            self.models = {k: v[0] for k, v in self.agent_preferences.items() if v}
        elif self.models and not self.agent_preferences:
            # Legacy format supplied → derive new ordered-list format
            self.agent_preferences = {k: [v] for k, v in self.models.items()}

    @property
    def api_key(self) -> str:
        """Return the API key from the environment (never stored in this object)."""
        return os.getenv(self.api_key_env, "")

    def model_id(self, preference: str) -> str:
        """
        Return the primary (first) model ID for the given preference.

        Falls back to the "general" preference if *preference* is not defined.
        """
        candidates = (
            self.agent_preferences.get(preference)
            or self.agent_preferences.get("general", [])
        )
        if not candidates:
            logger.warning(
                f"[{self.name}] No model mapped for preference '{preference}' "
                f"and no 'general' fallback — returning empty string"
            )
            return ""
        return candidates[0]

    def model_ids(self, preference: str) -> list[str]:
        """
        Return the full ordered list of model IDs for *preference*
        (primary first, then fallbacks).

        Falls back to the "general" preference list if *preference* is absent.
        """
        return list(
            self.agent_preferences.get(preference)
            or self.agent_preferences.get("general", [])
        )

    def __str__(self) -> str:
        return f"ProviderConfig(name={self.name!r}, base_url={self.base_url!r})"


# ---------------------------------------------------------------------------
# Fallback builder
# ---------------------------------------------------------------------------

def _build_fallback() -> ProviderConfig:
    """Return a hard-coded Nano-GPT config used when YAML is unavailable."""
    logger.warning(
        "Using hard-coded fallback provider config (nano_gpt). "
        "Check that src/config/providers.yaml exists and pyyaml is installed."
    )
    return ProviderConfig(
        name=_FALLBACK_PROVIDER_NAME,
        display_name="Nano-GPT (fallback)",
        base_url=_FALLBACK_BASE_URL,
        api_key_env=_FALLBACK_API_KEY_ENV,
        models=dict(_FALLBACK_MODELS),
    )


def load_all_providers(
    yaml_path: Path | None = None,
) -> dict[str, ProviderConfig]:
    """
    Load all provider definitions from providers.yaml.

    Returns:
        Dict mapping provider name → ProviderConfig.
        Empty dict if the file cannot be read.
    """
    data = load_yaml(yaml_path or _PROVIDERS_YAML)
    providers_raw: dict = data.get("providers", {})
    result: dict[str, ProviderConfig] = {}

    for name, cfg in providers_raw.items():
        if not isinstance(cfg, dict):
            logger.warning(f"providers.yaml: skipping malformed entry '{name}'")
            continue
        try:
            # Support both old `models: {pref: str}` and new `agent_preferences: {pref: list}`
            raw_agent_prefs: dict = cfg.get("agent_preferences", {})
            raw_models: dict = cfg.get("models", {})

            if raw_agent_prefs:
                # New format: values may be strings or lists
                agent_preferences: dict[str, list[str]] = {
                    k: ([v] if isinstance(v, str) else list(v))
                    for k, v in raw_agent_prefs.items()
                }
                models: dict[str, str] = {
                    k: v[0] for k, v in agent_preferences.items() if v
                }
            else:
                # Legacy format: convert single strings to single-element lists
                models = {k: str(v) for k, v in raw_models.items()}
                agent_preferences = {k: [v] for k, v in models.items()}

            # base_url_env (optional) lets the real URL live in .env, keeping
            # providers.yaml free of host-specific addresses for safe git commits.
            raw_base_url: str = cfg["base_url"]
            base_url_env_name: str | None = cfg.get("base_url_env")
            if base_url_env_name:
                raw_base_url = os.getenv(base_url_env_name, raw_base_url)

            result[name] = ProviderConfig(
                name=name,
                display_name=cfg.get("display_name", name),
                base_url=raw_base_url,
                api_key_env=cfg["api_key_env"],
                models=models,
                agent_preferences=agent_preferences,
                extra_headers=dict(cfg.get("extra_headers", {})),
                ssl_verify=bool(cfg.get("ssl_verify", True)),
                skip_tor=bool(cfg.get("skip_tor", False)),
                supports_tools=bool(cfg.get("supports_tools", True)),
                max_concurrent=int(cfg.get("max_concurrent", 3)),
                timeout=int(cfg["timeout"]) if cfg.get("timeout") is not None else None,
                catalog_strategy=str(cfg.get("catalog_strategy", _DEFAULT_CATALOG_STRATEGY)),
                model_filter=str(cfg.get("model_filter", _DEFAULT_MODEL_FILTER)),
            )
        except KeyError as exc:
            logger.warning(f"providers.yaml: provider '{name}' missing key {exc} — skipped")

    return result


# ---------------------------------------------------------------------------
# Active provider selection
# ---------------------------------------------------------------------------

def get_active_provider(
    yaml_path: Path | None = None,
) -> ProviderConfig:
    """
    Return the active provider's ProviderConfig.

    Selection order:
      1. ACTIVE_PROVIDER env var
      2. `active:` key in providers.yaml
      3. First provider defined in providers.yaml
      4. Hard-coded nano_gpt fallback

    This function is intentionally stateless (no module-level singleton) so
    that tests can override ACTIVE_PROVIDER freely without side effects.
    """
    data = load_yaml(yaml_path or _PROVIDERS_YAML)
    providers = load_all_providers(yaml_path)

    if not providers:
        return _build_fallback()

    # Priority 1: environment variable
    env_provider = os.getenv("ACTIVE_PROVIDER", "").strip().lower()

    # Priority 2: providers.yaml `active:` key
    yaml_provider = str(data.get("active", "")).strip().lower()

    # Resolve name
    chosen_name = env_provider or yaml_provider or _FALLBACK_PROVIDER_NAME

    if chosen_name in providers:
        provider = providers[chosen_name]
        logger.info(
            f"Active LLM provider: {provider.display_name!r} "
            f"(name={chosen_name!r}, base_url={provider.base_url!r})"
        )
        return provider

    # Chosen name not in providers — warn and use first available
    fallback_name = next(iter(providers))
    logger.warning(
        f"Provider '{chosen_name}' not found in providers.yaml — "
        f"falling back to '{fallback_name}'"
    )
    return providers[fallback_name]
