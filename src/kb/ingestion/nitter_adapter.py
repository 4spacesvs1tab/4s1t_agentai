"""
Nitter / Twitter ingestion adapter — Phase KB-2.

Fetches tweets/posts by scraping Nitter's RSS feed for a given Twitter handle.
Nitter provides `/handle/rss` endpoints on public instances.

Multiple Nitter instance URLs are tried in order; failed instances are skipped.
All HTTP requests use the Tor SOCKS5 proxy when configured (privacy.yaml).

Platform identifier (`platform_id`): Twitter handle, e.g. "@JeffSnider_EDU"
or "JeffSnider_EDU" (leading @ is stripped automatically).

Design reference: KnowledgeBase_design.md §6.2
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from kb.ingestion.base_adapter import BaseIngestionAdapter, RawFetchResult
from kb.ingestion.website_adapter import _fetch_rss, _parse_rss_date, _get_http_client

from utils.logger import setup_logger
logger = setup_logger(__name__)

# Public Nitter instances tried in order. The first healthy one wins.
# These are well-known community instances; users can override via env var.
_DEFAULT_NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
    "https://nitter.kavin.rocks",
]

_MAX_TEXT_CHARS = 4_000  # tweets are short; cap to avoid chunking noise


def _get_nitter_instances() -> list[str]:
    """Return Nitter instance list from env override or defaults."""
    env = os.environ.get("NITTER_INSTANCES", "")
    if env:
        return [u.strip().rstrip("/") for u in env.split(",") if u.strip()]
    return _DEFAULT_NITTER_INSTANCES


def _normalize_handle(handle: str) -> str:
    """Strip leading @ and whitespace from a Twitter handle."""
    return handle.strip().lstrip("@")


def _fetch_nitter_rss(handle: str) -> list[dict]:
    """
    Try each Nitter instance until one returns a non-empty feed.

    Returns the parsed items list (same format as website_adapter._fetch_rss).
    """
    for base in _get_nitter_instances():
        url = f"{base}/{handle}/rss"
        try:
            items = _fetch_rss(url)
            if items:
                logger.info("Nitter: fetched %d items for @%s via %s", len(items), handle, base)
                return items
        except Exception as exc:
            logger.debug("Nitter instance %s failed for @%s: %s", base, handle, exc)
            continue
    logger.warning("Nitter: all instances failed for @%s", handle)
    return []


class NitterAdapter(BaseIngestionAdapter):
    """
    Ingests Twitter content via Nitter RSS.

    The *platform_id* is a Twitter handle (with or without leading @).
    """

    @property
    def platform(self) -> str:
        return "twitter"

    @staticmethod
    def supports_platform(platform: str) -> bool:
        return platform in ("twitter", "nitter")

    def fetch(
        self,
        account_id: str,
        platform_id: str,
        max_items: int = 50,
        domains: str = "",
        user_id: str = "default",
        layer: int = 1,
        source_tag: str = "twitter",
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
        source_tag: str = "twitter",
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
        handle = _normalize_handle(platform_id)
        raw_items = _fetch_nitter_rss(handle)
        if not raw_items:
            return []

        results = []
        for item in raw_items[:max_items]:
            pub_at = item.get("published_at", "")
            # Apply since_iso filter
            if since_iso and pub_at:
                try:
                    pub_dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                    since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
                    if pub_dt <= since_dt:
                        continue
                except Exception:
                    pass

            # Nitter RSS puts tweet text in title + description; combine them
            title = item.get("title", "").strip()

            # Skip retweets — we want original L1 content only, not content
            # from L2 candidates that the L1 account happened to repost.
            # Nitter marks retweets as "RT @handle: ..." in the title.
            if title.startswith("RT @"):
                continue

            body = item.get("content", "") or item.get("description", "")
            # Avoid duplicating the text if title == body start
            if body.startswith(title):
                text = body
            else:
                text = f"{title}\n\n{body}" if title else body
            text = text.strip()[:_MAX_TEXT_CHARS]
            if not text:
                continue

            # Nitter link points to the tweet on the Nitter instance;
            # construct the canonical twitter.com URL from the handle
            link = item.get("link", "")
            if "nitter" in link and handle:
                try:
                    # e.g. https://nitter.net/JeffSnider_EDU/status/1234567890
                    canonical = link.replace(
                        link.split("/")[0] + "//" + link.split("/")[2],
                        "https://twitter.com",
                    )
                except Exception:
                    canonical = link
            else:
                canonical = link

            results.append(RawFetchResult(
                text=text,
                source_url=canonical or f"https://twitter.com/{handle}",
                platform=self.platform,
                published_at=pub_at,
                author=item.get("author", handle),
                title=title,
                account_id=account_id,
                domains=domains,
                user_id=user_id,
                layer=layer,
                source=source_tag,
            ))

        logger.info("NitterAdapter: %d items for @%s", len(results), handle)
        return results
