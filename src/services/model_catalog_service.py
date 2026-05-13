"""
ModelCatalogService — live model list fetcher with 1-hour TTL cache.

For each provider the service:
  1. Fetches ``GET {base_url}/models`` (OpenAI-compatible endpoint).
  2. Applies the provider's ``model_filter`` ("all" | "free" | "pro").
  3. Caches the result for ``CACHE_TTL_SECONDS`` (default 3600 s).
  4. Falls back to the static ``agent_preferences`` list on any error.

Usage::

    from services.model_catalog_service import get_model_catalog_service

    service = get_model_catalog_service()
    models = await service.get_models(provider_config)
    # → [{"id": "some-model", "object": "model", "source": "api"}, ...]
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from utils.logger import setup_logger

if TYPE_CHECKING:
    from config.provider_config import ProviderConfig

logger = setup_logger(__name__)

CACHE_TTL_SECONDS: int = 3600  # 1 hour


@dataclass
class _CacheEntry:
    models: list[dict]
    fetched_at: float = field(default_factory=time.monotonic)

    def is_valid(self) -> bool:
        return (time.monotonic() - self.fetched_at) < CACHE_TTL_SECONDS


class ModelCatalogService:
    """
    Fetches and caches the model catalog from each LLM provider's API.

    Thread / task safety: one ``asyncio.Lock`` per provider prevents concurrent
    cache-miss requests from hammering the upstream API.
    """

    def __init__(self) -> None:
        self._cache: dict[str, _CacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lock(self, provider_name: str) -> asyncio.Lock:
        if provider_name not in self._locks:
            self._locks[provider_name] = asyncio.Lock()
        return self._locks[provider_name]

    def _apply_filter(self, models: list[dict], model_filter: str) -> list[dict]:
        """Return *models* after applying the provider's model_filter."""
        if model_filter == "free":
            return [m for m in models if m.get("id", "").endswith(":free")]
        if model_filter == "pro":
            return [m for m in models if not m.get("id", "").endswith(":free")]
        return models  # "all" — no filtering

    def _static_fallback(self, provider: "ProviderConfig") -> list[dict]:
        """
        Derive a model list from the provider's static ``agent_preferences``.

        De-duplicates model IDs while preserving insertion order.
        """
        seen: set[str] = set()
        result: list[dict] = []
        for pref, model_ids in provider.agent_preferences.items():
            for mid in model_ids:
                if mid and mid not in seen:
                    seen.add(mid)
                    result.append({
                        "id": mid,
                        "object": "model",
                        "source": "static",
                        "preference": pref,
                    })
        return result

    async def _fetch_api(self, provider: "ProviderConfig") -> list[dict]:
        """
        Fetch model list from ``GET {base_url}/models``.

        Returns the raw list of ``{"id": ..., "object": "model", "source": "api"}``
        dicts, or delegates to ``_static_fallback`` on any HTTP / network error.
        """
        url = provider.base_url.rstrip("/") + "/models"
        headers: dict[str, str] = {}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
        headers.update(provider.extra_headers)

        try:
            async with httpx.AsyncClient(
                verify=provider.ssl_verify,
                timeout=10.0,
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            raw_models: list = data.get("data", [])
            if not isinstance(raw_models, list):
                raise ValueError(
                    f"Unexpected /models response — 'data' is {type(raw_models).__name__}, "
                    f"expected list"
                )

            models = [
                {"id": m.get("id", ""), "object": "model", "source": "api"}
                for m in raw_models
                if isinstance(m, dict) and m.get("id")
            ]
            logger.info(
                f"[{provider.name}] Fetched {len(models)} model(s) from API"
            )
            return models

        except Exception as exc:
            logger.warning(
                f"[{provider.name}] Could not fetch models from {url!r} "
                f"({type(exc).__name__}: {exc}); falling back to static list"
            )
            return self._static_fallback(provider)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_models(self, provider: "ProviderConfig") -> list[dict]:
        """
        Return the filtered model list for *provider*.

        Uses the in-memory cache (TTL = ``CACHE_TTL_SECONDS``) and fetches
        from the provider API only when the cache is cold or stale.
        """
        async with self._lock(provider.name):
            entry = self._cache.get(provider.name)
            if entry is not None and entry.is_valid():
                return entry.models

            if provider.catalog_strategy == "static":
                models = self._static_fallback(provider)
            else:  # "api"
                models = await self._fetch_api(provider)

            models = self._apply_filter(models, provider.model_filter)

            self._cache[provider.name] = _CacheEntry(models=models)
            logger.debug(
                f"[{provider.name}] Cached {len(models)} model(s) "
                f"(filter={provider.model_filter!r})"
            )
            return models

    def invalidate(self, provider_name: str | None = None) -> None:
        """
        Invalidate the cache for *provider_name*, or for all providers when
        *provider_name* is ``None``.
        """
        if provider_name:
            self._cache.pop(provider_name, None)
        else:
            self._cache.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_catalog_service: ModelCatalogService | None = None


def get_model_catalog_service() -> ModelCatalogService:
    """Return the shared ``ModelCatalogService`` singleton."""
    global _catalog_service
    if _catalog_service is None:
        _catalog_service = ModelCatalogService()
    return _catalog_service
