"""
Async API client for OpenAI-compatible LLM endpoints.

Wraps the OpenAI-compatible endpoint with:
- asyncio.Semaphore(3) to cap concurrent in-flight requests
- Exponential backoff retry (3 attempts, factor 2.0, ±25 % jitter)
- Retry on rate-limit, connection, and timeout errors

Privacy features (controlled via src/config/privacy.yaml):
- Tor SOCKS5 proxy routing to break IP linkability
- SDK fingerprint header stripping (X-Stainless-*, User-Agent normalisation)
- Per-request timing jitter to resist timing correlation
- Configurable Tor fallback: kill-switch or NIP-17 user approval
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any

from utils.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------
_MAX_RETRIES = 3
_BACKOFF_FACTOR = 2.0      # seconds
_JITTER_FRACTION = 0.25    # ±25 % of the computed delay

# ---------------------------------------------------------------------------
# Errors that are safe to retry
# ---------------------------------------------------------------------------
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# ---------------------------------------------------------------------------
# SDK fingerprint headers to strip (lowercase names)
# The openai Python SDK injects these automatically; they reveal SDK version,
# OS, Python version, and architecture to the provider.
# ---------------------------------------------------------------------------
_STRIP_HEADERS: frozenset[str] = frozenset({
    "x-stainless-lang",
    "x-stainless-package-version",
    "x-stainless-runtime",
    "x-stainless-runtime-version",
    "x-stainless-os",
    "x-stainless-arch",
    "x-stainless-async",
})
_GENERIC_UA = "python-httpx/0.27.0"


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class TorRequiredError(RuntimeError):
    """Raised when Tor is unavailable and tor_fallback is 'kill'."""


# ---------------------------------------------------------------------------
# StripHeadersTransport
# ---------------------------------------------------------------------------

class _StripHeadersTransport:
    """
    httpx AsyncBaseTransport wrapper that removes SDK fingerprint headers
    and replaces User-Agent with a generic value before forwarding requests.
    """

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    async def handle_async_request(self, request: Any) -> Any:
        try:
            import httpx
        except ImportError:
            return await self._wrapped.handle_async_request(request)

        filtered = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _STRIP_HEADERS
        }
        filtered["user-agent"] = _GENERIC_UA

        new_request = httpx.Request(
            method=request.method,
            url=request.url,
            headers=filtered,
            content=request.content,
            extensions=request.extensions,
        )
        return await self._wrapped.handle_async_request(new_request)

    async def aclose(self) -> None:
        if hasattr(self._wrapped, "aclose"):
            await self._wrapped.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_model_unavailable(exc: Exception) -> bool:
    """Return True if the exception indicates this specific model is unavailable.

    Used by BaseAgent to decide whether to try the next model in the fallback list.
    Matches 403 PermissionDenied (Tor IP block or model tier restriction),
    404 NotFound (model removed or renamed), and 400 BadRequestError with
    code='empty_response' (model returns blank output — content filter or model defect).
    """
    try:
        import openai
        if isinstance(exc, (
            openai.PermissionDeniedError,  # 403 — IP block or subscription tier
            openai.NotFoundError,          # 404 — model removed or renamed
        )):
            return True
        # 400 empty_response: model refuses to generate (content filter or model defect).
        # Treat as unavailable so the fallback model is tried instead of hard-failing.
        if isinstance(exc, openai.BadRequestError):
            body = getattr(exc, "body", None) or {}
            return (body.get("error") or {}).get("code") == "empty_response"
        return False
    except ImportError:
        return False


def _retryable_exception(exc: Exception) -> bool:
    """Return True if the exception is transient and worth retrying."""
    try:
        import openai
        # 403 can be a transient Tor exit-node block — rotate circuit and retry
        if isinstance(exc, openai.PermissionDeniedError):
            return True
        return isinstance(exc, (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
        )) or _is_vercel_blocked(exc)
    except ImportError:
        return False


def _is_vercel_blocked(exc: Exception) -> bool:
    """Return True if the exception is a Vercel Security Checkpoint 403 block."""
    return "Vercel Security Checkpoint" in str(exc)


async def _rotate_tor_circuit() -> None:
    """
    Send NEWNYM signal to the Tor control port to obtain a fresh exit node.

    Reads TOR_CONTROL_HOST / TOR_CONTROL_PORT / TOR_CONTROL_PASSWORD from env.
    Waits 3 s after NEWNYM so Tor has time to build the new circuit.
    Logs a warning and returns silently if the control port is unreachable.
    """
    host = os.environ.get("TOR_CONTROL_HOST", "172.20.0.1")
    port = int(os.environ.get("TOR_CONTROL_PORT", "9051"))
    password = os.environ.get("TOR_CONTROL_PASSWORD", "")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5
        )
        writer.write(f'AUTHENTICATE "{password}"\r\n'.encode())
        await asyncio.wait_for(reader.readline(), timeout=5)
        writer.write(b"SIGNAL NEWNYM\r\n")
        await asyncio.wait_for(reader.readline(), timeout=5)
        writer.write(b"QUIT\r\n")
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=2)
        except Exception:
            pass
        logger.info("Tor circuit rotated (NEWNYM sent) — waiting 3 s for new circuit")
        await asyncio.sleep(3)
    except Exception as exc:
        logger.warning(f"Could not rotate Tor circuit: {exc}")


async def _sleep_with_jitter(attempt: int) -> None:
    """Exponential backoff sleep with ±jitter."""
    base = _BACKOFF_FACTOR * (2 ** attempt)          # 2s, 4s, 8s …
    jitter = base * _JITTER_FRACTION * (2 * random.random() - 1)
    delay = max(0.0, base + jitter)
    logger.debug(f"Retry {attempt + 1}: sleeping {delay:.2f}s")
    await asyncio.sleep(delay)


def _is_tor_proxy_error(exc: Exception) -> bool:
    """Return True if the exception indicates the SOCKS5 proxy is unreachable."""
    try:
        import httpx
        return isinstance(exc, (httpx.ProxyError, httpx.ConnectError))
    except ImportError:
        return False


def _build_http_client(
    tor_proxy: str,
    strip_headers: bool,
    ssl_verify: bool,
) -> Any:
    """
    Build an httpx.AsyncClient with the requested transport stack.

    Stack (inner → outer):
      1. httpx.AsyncHTTPTransport (with or without SOCKS5 proxy)
      2. _StripHeadersTransport wrapper (if strip_headers=True)
    """
    try:
        import httpx
    except ImportError:
        return None

    # NOTE: when an explicit transport is passed to AsyncClient, httpx ignores
    # the client-level `verify` kwarg.  ssl_verify must be set on the transport.
    if tor_proxy:
        try:
            base = httpx.AsyncHTTPTransport(proxy=httpx.Proxy(tor_proxy), verify=ssl_verify)
        except Exception as exc:
            logger.warning(f"Failed to create SOCKS transport ({exc}), falling back to direct")
            base = httpx.AsyncHTTPTransport(verify=ssl_verify)
    else:
        base = httpx.AsyncHTTPTransport(verify=ssl_verify)

    transport = _StripHeadersTransport(base) if strip_headers else base
    return httpx.AsyncClient(transport=transport, verify=ssl_verify)


# ---------------------------------------------------------------------------
# ApiClient
# ---------------------------------------------------------------------------

class ApiClient:
    """
    Async client for OpenAI-compatible chat completions endpoints.

    Works with any OpenAI-compatible provider (Nano-GPT, OpenRouter, OpenAI)
    by accepting base_url, api_key, and optional extra_headers at construction.

    Usage::

        client = ApiClient()                          # reads active provider config
        client = ApiClient(base_url="...", api_key="...")   # explicit override
        response = await client.chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
            model="deepseek-v3.2",
        )
        print(response.choices[0].message.content)
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        max_concurrent: int = 3,
        extra_headers: dict | None = None,
        ssl_verify: bool = True,
        skip_tor: bool = False,
        supports_tools: bool = True,
        timeout: int | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("NANO_GPT_API_KEY", "")
        self._base_url = (
            base_url
            or os.getenv("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
        )
        self._extra_headers: dict = extra_headers or {}
        self._ssl_verify = ssl_verify
        self._skip_tor = skip_tor          # True for local/LAN providers Tor cannot reach
        self.supports_tools = supports_tools  # False for models that reject tool params
        self._timeout = timeout            # None = use SDK default (600s)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._client: Any = None           # lazy-initialised, uses Tor if configured
        self._direct_client: Any = None    # lazy-initialised, never uses Tor (fallback only)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Return (or create) the primary openai.AsyncOpenAI client."""
        if self._client is None:
            try:
                import openai
            except ImportError as exc:
                raise ImportError("openai package is required: pip install openai") from exc

            from config.privacy_config import get_privacy_config
            privacy = get_privacy_config()

            http_client = None
            if privacy.enabled:
                # Local/LAN providers must bypass Tor — Tor exits cannot reach .local or RFC-1918
                tor_proxy = "" if self._skip_tor else privacy.tor_proxy
                if self._skip_tor and privacy.tor_proxy:
                    logger.info("ApiClient: Tor bypassed for local provider")
                http_client = _build_http_client(
                    tor_proxy=tor_proxy,
                    strip_headers=privacy.strip_sdk_headers,
                    ssl_verify=self._ssl_verify,
                )
                if http_client is None and not self._ssl_verify:
                    # httpx unavailable; best-effort no-verify fallback
                    try:
                        import httpx
                        http_client = httpx.AsyncClient(verify=False)
                    except ImportError:
                        pass
            elif not self._ssl_verify:
                try:
                    import httpx
                    http_client = httpx.AsyncClient(verify=False)
                except ImportError:
                    logger.warning("httpx not installed — cannot disable SSL verification")

            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                default_headers=self._extra_headers or None,
                http_client=http_client,
            )

            if privacy.enabled and privacy.tor_proxy and not self._skip_tor:
                logger.info(f"ApiClient: Tor proxy active → {privacy.tor_proxy}")
            if privacy.enabled and privacy.strip_sdk_headers:
                logger.info("ApiClient: SDK fingerprint header stripping enabled")

        return self._client

    def _get_direct_client(self) -> Any:
        """
        Return (or create) a direct openai.AsyncOpenAI client that bypasses Tor.
        Used only when tor_fallback='approve' and the user has approved a bypass.
        """
        if self._direct_client is None:
            try:
                import openai
            except ImportError as exc:
                raise ImportError("openai package is required: pip install openai") from exc

            from config.privacy_config import get_privacy_config
            privacy = get_privacy_config()

            # Build without Tor, but still strip headers if configured
            http_client = _build_http_client(
                tor_proxy="",                       # no Tor
                strip_headers=privacy.strip_sdk_headers,
                ssl_verify=self._ssl_verify,
            )

            self._direct_client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                default_headers=self._extra_headers or None,
                http_client=http_client,
            )
            logger.warning("ApiClient: created direct (non-Tor) client for fallback use")

        return self._direct_client

    async def _request_tor_bypass_approval(self, provider_url: str) -> bool:
        """
        Send a NIP-17 approval request asking the user whether to proceed
        without Tor. Returns True if approved, False otherwise.
        """
        from config.privacy_config import get_privacy_config
        privacy = get_privacy_config()

        try:
            from services.nostr_service import get_nostr_service
            service = get_nostr_service()
            if service and service.chat_agent:
                from services.approval_gateway import request_approval
                return await request_approval(
                    action="tor_bypass",
                    details=(
                        f"Tor proxy is unreachable. The next LLM request would be sent "
                        f"directly from your server IP to {provider_url}. "
                        f"Approve to proceed without Tor (this request only). "
                        f"Reject to block the request."
                    ),
                    timeout=privacy.tor_approval_timeout,
                )
        except Exception as exc:
            logger.error(f"Could not send Tor bypass approval request: {exc}")

        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        messages: list[dict],
        model: str,
        **kwargs: Any,
    ) -> Any:
        """
        Call the chat completions endpoint with retry + concurrency cap.

        Applies privacy transformations:
          - Routes through Tor if configured
          - Falls back to kill/approve on Tor failure
          - Adds per-request timing jitter

        Args:
            messages: OpenAI-format message list.
            model:    Model ID (e.g. ``"deepseek-v3.2"``).
            **kwargs: Forwarded to ``openai.AsyncOpenAI.chat.completions.create``.

        Returns:
            ``openai.types.chat.ChatCompletion`` object.

        Raises:
            TorRequiredError: If Tor is down and tor_fallback is 'kill'.
            Exception: After all retries are exhausted.
        """
        from config.privacy_config import get_privacy_config
        privacy = get_privacy_config()

        # Request timing jitter
        if privacy.enabled and privacy.jitter_max_ms > 0:
            jitter_s = random.randint(privacy.jitter_min_ms, privacy.jitter_max_ms) / 1000.0
            await asyncio.sleep(jitter_s)

        client = self._get_client()
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                async with self._semaphore:
                    logger.debug(
                        f"LLM call attempt {attempt + 1}/{_MAX_RETRIES} "
                        f"model={model} messages={len(messages)}"
                    )
                    if self._timeout is not None:
                        kwargs.setdefault("timeout", self._timeout)
                    response = await client.chat.completions.create(
                        messages=messages,
                        model=model,
                        **kwargs,
                    )
                    logger.info(
                        f"LLM call succeeded: model={model} "
                        f"tokens={getattr(getattr(response, 'usage', None), 'total_tokens', '?')}"
                    )
                    return response

            except Exception as exc:
                # Tor / SOCKS proxy unreachable — handle according to fallback policy
                if privacy.tor_enabled and _is_tor_proxy_error(exc):
                    logger.warning(f"Tor proxy unreachable: {exc}")
                    if privacy.tor_fallback == "kill":
                        raise TorRequiredError(
                            f"Tor proxy unreachable and tor_fallback='kill'. Request blocked."
                        ) from exc
                    elif privacy.tor_fallback == "approve":
                        approved = await self._request_tor_bypass_approval(self._base_url)
                        if approved:
                            logger.warning("User approved Tor bypass — retrying without Tor")
                            direct = self._get_direct_client()
                            async with self._semaphore:
                                return await direct.chat.completions.create(
                                    messages=messages, model=model, **kwargs
                                )
                        else:
                            raise TorRequiredError(
                                "Tor proxy unreachable. User rejected bypass. Request blocked."
                            ) from exc
                    # Unknown fallback value — block
                    raise TorRequiredError(f"Tor proxy unreachable: {exc}") from exc

                last_exc = exc
                if not _retryable_exception(exc):
                    logger.error(f"LLM call failed (non-retryable): {exc}")
                    raise
                logger.warning(
                    f"LLM call attempt {attempt + 1} failed (retryable): {exc}"
                )
                if attempt < _MAX_RETRIES - 1:
                    if _is_vercel_blocked(exc):
                        logger.warning(
                            "Vercel Security Checkpoint detected — rotating Tor circuit"
                        )
                        await _rotate_tor_circuit()
                    elif _is_model_unavailable(exc):
                        # 403/404 from provider — may be transient Tor IP block;
                        # rotate circuit before retry (no-op if Tor not configured)
                        logger.warning(
                            f"Provider returned {exc.__class__.__name__} — "
                            "rotating Tor circuit before retry"
                        )
                        await _rotate_tor_circuit()
                    else:
                        await _sleep_with_jitter(attempt)

        logger.error(f"LLM call failed after {_MAX_RETRIES} attempts: {last_exc}")
        raise last_exc  # type: ignore[misc]

    async def close(self) -> None:
        """Close the underlying HTTP clients if created."""
        for attr in ("_client", "_direct_client"):
            client = getattr(self, attr, None)
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass
                setattr(self, attr, None)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_api_client: ApiClient | None = None
_client_lock = asyncio.Lock()


async def reset_api_client() -> None:
    """
    Close and discard the shared ApiClient singleton.

    The next call to get_api_client() will rebuild it from the current
    provider config and obtain a fresh Tor circuit (if Tor is configured).
    Call this after repeated provider failures to force a full reconnect.
    """
    global _api_client
    async with _client_lock:
        if _api_client is not None:
            try:
                await _api_client.close()
            except Exception:
                pass
            _api_client = None
    logger.info("ApiClient singleton reset — will reconnect on next request")


async def get_api_client() -> ApiClient:
    """
    Return the shared ApiClient singleton (thread-safe, lazy init).

    Reads provider settings from the active provider in providers.yaml
    (overridable via ACTIVE_PROVIDER env var).  Falls back to the legacy
    NANO_GPT_API_KEY / NANO_GPT_BASE_URL env vars if the provider config
    cannot be loaded.
    """
    global _api_client
    if _api_client is None:
        async with _client_lock:
            if _api_client is None:
                try:
                    from config.provider_config import get_active_provider
                    provider = get_active_provider()
                    _api_client = ApiClient(
                        api_key=provider.api_key,
                        base_url=provider.base_url,
                        extra_headers=provider.extra_headers or None,
                        ssl_verify=getattr(provider, "ssl_verify", True),
                    )
                    logger.info(
                        f"ApiClient initialised → provider={provider.name!r}  "
                        f"base_url={provider.base_url!r}"
                    )
                except Exception as exc:
                    logger.warning(
                        f"Could not load provider config ({exc}), "
                        "falling back to NANO_GPT_* env vars"
                    )
                    _api_client = ApiClient()
                    logger.info(
                        f"ApiClient initialised (fallback) → base_url={_api_client._base_url!r}"
                    )
    return _api_client
