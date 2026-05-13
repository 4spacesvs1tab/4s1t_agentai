"""
Rumble ingestion adapter — Phase KB-3.

Fetches content from Rumble channel RSS/Atom feeds.

Feed URL pattern:
  https://rumble.com/feeds/user/{username}.rss

Each feed item represents one video/livestream. The adapter extracts
the title, description, published date, and link.

Design reference: KnowledgeBase_design.md §6.2 (adapter pattern)
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

from kb.ingestion.base_adapter import BaseIngestionAdapter, RawFetchResult

from utils.logger import setup_logger
logger = setup_logger(__name__)

# Rumble RSS namespace
_MEDIA_NS = "http://search.yahoo.com/mrss/"

# Feed URL template — username is the Rumble channel name
_FEED_URL = "https://rumble.com/feeds/user/{username}.rss"

_HTTP_TIMEOUT = 20.0
_USER_AGENT = "Mozilla/5.0 (compatible; 4S1T-KB/1.0)"


def _build_feed_url(platform_id: str) -> str:
    """
    Build the Rumble RSS URL from platform_id.

    platform_id formats accepted:
      - "username"                   → https://rumble.com/feeds/user/username.rss
      - "https://rumble.com/c/..."   → returned as-is (custom channel URL)
      - "https://..."                → returned as-is (direct feed URL)
    """
    if platform_id.startswith("http"):
        if "/feeds/" in platform_id:
            return platform_id
        # Channel page URL — convert to feed URL
        # e.g. https://rumble.com/c/ChannelName → feeds/user/ChannelName
        slug = platform_id.rstrip("/").split("/")[-1]
        return _FEED_URL.format(username=slug)
    return _FEED_URL.format(username=platform_id)


def _parse_iso(dt_str: str) -> str:
    """Parse RFC 2822 or ISO 8601 datetime string to ISO 8601 UTC string."""
    if not dt_str:
        return ""
    try:
        # RFC 2822 (standard RSS pubDate)
        dt = parsedate_to_datetime(dt_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        # ISO 8601 fallback
        return dt_str if "T" in dt_str else dt_str + "T00:00:00+00:00"
    except Exception:
        return dt_str


def _fetch_feed(feed_url: str) -> list[ET.Element]:
    """Fetch and parse a Rumble RSS feed. Returns list of <item> elements."""
    try:
        resp = httpx.get(
            feed_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Rumble RSS fetch failed for %s: %s", feed_url, exc)
        return []

    try:
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> in Rumble feed: %s", feed_url)
            return []
        return channel.findall("item")
    except ET.ParseError as exc:
        logger.warning("Rumble RSS parse error for %s: %s", feed_url, exc)
        return []


def _item_to_result(
    item: ET.Element,
    account_id: str,
    domains: str,
    user_id: str,
    layer: int,
) -> Optional[RawFetchResult]:
    """Convert a parsed RSS <item> element to a RawFetchResult."""
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    description = (item.findtext("description") or "").strip()
    pub_date = (item.findtext("pubDate") or "").strip()

    if not link:
        return None

    text = f"{title}\n\n{description}".strip() if description else title
    if not text:
        return None

    return RawFetchResult(
        text=text,
        source_url=link,
        platform="rumble",
        published_at=_parse_iso(pub_date),
        author="",
        title=title,
        account_id=account_id,
        domains=domains,
        user_id=user_id,
        layer=layer,
        source="rumble",
    )


class RumbleAdapter(BaseIngestionAdapter):
    """Ingestion adapter for Rumble channel RSS feeds."""

    @property
    def platform(self) -> str:
        return "rumble"

    def fetch(
        self,
        account_id: str,
        platform_id: str,
        max_items: int = 50,
        domains: str = "",
        user_id: str = "default",
        layer: int = 1,
    ) -> list[RawFetchResult]:
        """Fetch recent Rumble videos (full backlog up to max_items)."""
        feed_url = _build_feed_url(platform_id)
        items = _fetch_feed(feed_url)
        results = []
        for item in items[:max_items]:
            r = _item_to_result(item, account_id, domains, user_id, layer)
            if r:
                results.append(r)
        logger.info("Rumble fetch: %d items from %s", len(results), feed_url)
        return results

    def get_new_since(
        self,
        account_id: str,
        platform_id: str,
        since_iso: str,
        max_items: int = 50,
        domains: str = "",
        user_id: str = "default",
        layer: int = 1,
    ) -> list[RawFetchResult]:
        """Fetch Rumble videos published after since_iso."""
        feed_url = _build_feed_url(platform_id)
        items = _fetch_feed(feed_url)

        try:
            since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
        except Exception:
            since_dt = None

        results = []
        for item in items:
            if len(results) >= max_items:
                break
            r = _item_to_result(item, account_id, domains, user_id, layer)
            if not r:
                continue
            if since_dt and r.published_at:
                try:
                    pub_dt = datetime.fromisoformat(r.published_at.replace("Z", "+00:00"))
                    if pub_dt <= since_dt:
                        continue
                except Exception:
                    pass
            results.append(r)

        logger.info(
            "Rumble incremental fetch: %d new items since %s from %s",
            len(results), since_iso, feed_url,
        )
        return results

    @staticmethod
    def supports_platform(platform: str) -> bool:
        return platform.lower() == "rumble"
