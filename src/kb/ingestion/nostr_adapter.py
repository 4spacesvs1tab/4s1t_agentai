"""
Nostr ingestion adapter — Phase KB-2.

Fetches public notes (kind:1) from Nostr relays for a given author pubkey.

Platform identifier (`platform_id`): npub (bech32) or hex pubkey.
  Examples:
    "npub1dergigi..."
    "npub1hodlonaut..."

Uses nostr-sdk (already in requirements.txt) to connect to relays,
subscribe to kind:1 events by author, and disconnect after collecting
or hitting the timeout.

Relay list is read from env var NOSTR_RELAYS (comma-separated); falls
back to a curated set of public relays.

Since Nostr SDK is async, this adapter wraps async calls in asyncio.run().
The ingestion runner calls this from a sync context (background thread).

Design reference: KnowledgeBase_design.md §6.2
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

# nostr-sdk ≥ 0.44 API notes:
#   - PublicKey.parse(npub_or_hex)  (replaces from_bech32 / from_hex)
#   - RelayUrl.parse(url_str)       (replaces bare string URL)
#   - ClientBuilder().build()       (replaces Client())
#   - events.to_vec()               (replaces list(events))
#   - fetch_events(filter, timedelta) (timeout is datetime.timedelta)

from kb.ingestion.base_adapter import BaseIngestionAdapter, RawFetchResult

from utils.logger import setup_logger
logger = setup_logger(__name__)

# Default public relays — user can override via NOSTR_RELAYS env var
_DEFAULT_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
    "wss://nostr.mom",
]

# Timeout waiting for relay events (seconds)
_RELAY_TIMEOUT_S = 15

_MAX_TEXT_CHARS = 4_000  # Nostr notes are short-form


def _get_relay_urls() -> list[str]:
    env = os.environ.get("NOSTR_RELAYS", "")
    if env:
        return [u.strip() for u in env.split(",") if u.strip()]
    return _DEFAULT_RELAYS


def _parse_nostr_timestamp(ts) -> str:
    """Convert a Nostr SDK Timestamp (or integer) to ISO 8601 UTC string."""
    try:
        if hasattr(ts, "as_secs"):
            secs = ts.as_secs()
        else:
            secs = int(ts)
        return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    except Exception:
        return ""


async def _fetch_notes_async(
    platform_id: str,
    since_unix: int | None,
    max_items: int,
) -> list[dict]:
    """
    Async: connect to relays and fetch kind:1 notes for *platform_id* (npub or hex).

    Returns list of {text, created_at_iso, note_id, pubkey_hex}.
    """
    try:
        from nostr_sdk import (
            ClientBuilder, PublicKey, RelayUrl, Filter, Kind, Timestamp,
        )
    except ImportError:
        logger.error("nostr-sdk not installed — cannot run NostrAdapter; pip install nostr-sdk")
        return []

    try:
        pk = PublicKey.parse(platform_id.strip())
    except Exception as exc:
        logger.warning("NostrAdapter: could not parse pubkey %r: %s", platform_id[:20], exc)
        return []

    pubkey_hex = pk.to_hex()
    client = ClientBuilder().build()
    relay_urls = _get_relay_urls()
    for url in relay_urls:
        try:
            await client.add_relay(RelayUrl.parse(url))
        except Exception as exc:
            logger.debug("Could not add relay %s: %s", url, exc)

    await client.connect()

    # Build subscription filter
    f = Filter().author(pk).kind(Kind(1)).limit(max_items)
    if since_unix is not None:
        f = f.since(Timestamp.from_secs(since_unix))

    events = []
    try:
        result = await asyncio.wait_for(
            client.fetch_events(f, timedelta(seconds=_RELAY_TIMEOUT_S)),
            timeout=_RELAY_TIMEOUT_S + 5,
        )
        for event in result.to_vec():
            events.append({
                "text": event.content(),
                "created_at_iso": _parse_nostr_timestamp(event.created_at()),
                "note_id": event.id().to_hex(),
                "pubkey": pubkey_hex,
            })
    except asyncio.TimeoutError:
        logger.warning("NostrAdapter: timeout fetching notes for %s", platform_id[:20])
    except Exception as exc:
        logger.warning("NostrAdapter: fetch_events failed for %s: %s", platform_id[:20], exc)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        # Force Rust Tokio runtime shutdown for this client.
        # nostr-sdk keeps background threads alive until the Python Client object
        # is garbage collected. Without explicit deletion + gc, those threads
        # keep WebSocket connections open and buffer relay events indefinitely,
        # leaking ~85 MB/sec. del + gc.collect() triggers the Rust destructor
        # immediately, cleaning up all threads from this fetch session.
        # This only affects this temporary client — the NIP-17 chat_agent client
        # is a separate long-lived object and is unaffected.
        import gc
        del client
        gc.collect()

    return events


def _resolve_pubkey_hex(platform_id: str) -> Optional[str]:
    """
    Resolve *platform_id* to a hex pubkey.

    Handles npub1... (bech32) and raw 64-char hex pubkeys via PublicKey.parse().
    """
    try:
        from nostr_sdk import PublicKey
        return PublicKey.parse(platform_id.strip()).to_hex()
    except Exception as exc:
        logger.warning("NostrAdapter: could not parse platform_id %r: %s", platform_id[:20], exc)
        return None


def _run_async(coro) -> list[dict]:
    """Run an async coroutine synchronously. Safe to call from non-async context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an existing event loop (e.g., from tests).
            # Create a new thread event loop.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=_RELAY_TIMEOUT_S + 30)
        else:
            return loop.run_until_complete(coro)
    except Exception:
        return asyncio.run(coro)


class NostrAdapter(BaseIngestionAdapter):
    """
    Ingests Nostr kind:1 notes for a given author pubkey via relay subscription.

    The *platform_id* is an npub (bech32) or hex pubkey.
    """

    @property
    def platform(self) -> str:
        return "nostr"

    @staticmethod
    def supports_platform(platform: str) -> bool:
        return platform == "nostr"

    def fetch(
        self,
        account_id: str,
        platform_id: str,
        max_items: int = 50,
        domains: str = "",
        user_id: str = "default",
        layer: int = 1,
        source_tag: str = "nostr",
    ) -> list[RawFetchResult]:
        return self._fetch_internal(
            account_id=account_id,
            platform_id=platform_id,
            since_iso="",
            max_items=max_items,
            domains=domains,
            user_id=user_id,
            layer=layer,
            source_tag=source_tag,
        )

    def get_new_since(
        self,
        account_id: str,
        platform_id: str,
        since_iso: str,
        max_items: int = 50,
        domains: str = "",
        user_id: str = "default",
        layer: int = 1,
        source_tag: str = "nostr",
    ) -> list[RawFetchResult]:
        return self._fetch_internal(
            account_id=account_id,
            platform_id=platform_id,
            since_iso=since_iso,
            max_items=max_items,
            domains=domains,
            user_id=user_id,
            layer=layer,
            source_tag=source_tag,
        )

    def _fetch_internal(
        self,
        account_id: str,
        platform_id: str,
        since_iso: str,
        max_items: int,
        domains: str,
        user_id: str,
        layer: int,
        source_tag: str,
    ) -> list[RawFetchResult]:
        since_unix: Optional[int] = None
        if since_iso:
            try:
                dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
                since_unix = int(dt.timestamp())
            except Exception:
                pass

        notes = _run_async(
            _fetch_notes_async(platform_id, since_unix, max_items)
        )

        results = []
        for note in notes:
            text = note.get("text", "").strip()[:_MAX_TEXT_CHARS]
            if not text:
                continue
            note_id = note.get("note_id", "")
            source_url = f"https://nostr.com/e/{note_id}" if note_id else ""

            results.append(RawFetchResult(
                text=text,
                source_url=source_url,
                platform=self.platform,
                published_at=note.get("created_at_iso", ""),
                author=platform_id,  # display as npub; no name available without kind:0
                account_id=account_id,
                domains=domains,
                user_id=user_id,
                layer=layer,
                source=source_tag,
                extra={"note_id": note_id, "pubkey": note.get("pubkey", "")},
            ))

        logger.info("NostrAdapter: %d notes for %s", len(results), platform_id[:20])
        return results
