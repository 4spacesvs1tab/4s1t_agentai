"""
YouTube channel ingestion adapter — Phase KB-2.

Fetches video metadata from YouTube's public Atom feed (no API key required).

Feed URL pattern:
  https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}

Platform identifier (`platform_id`): YouTube channel ID, e.g. "UCYdgBE5yGEFO4zZ4KxLJGKQ"
or a full channel URL. Channel IDs start with "UC".

Content extracted:
  - Video title + description (from <media:description> or <summary>)
  - Published date
  - Video URL (canonical youtube.com/watch?v=...)

Note: Full transcripts require yt-dlp (optional, not installed by default).
This adapter ingests title+description as a lightweight signal. Transcript
ingestion can be layered in KB-3 when yt-dlp is available.

Members-only / subscriber-only content: YouTube's public Atom feed only
includes videos that are publicly visible. Members-only videos are never
listed in the feed, so this adapter inherently ingests free content only.
No extra filtering is required.

Privacy: All HTTP requests go through Tor SOCKS5 proxy when configured.

Design reference: KnowledgeBase_design.md §6.2
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from kb.ingestion.base_adapter import BaseIngestionAdapter, RawFetchResult
from kb.ingestion.website_adapter import _get_http_client, _strip_html

from utils.logger import setup_logger
logger = setup_logger(__name__)

_YOUTUBE_FEED_BASE = "https://www.youtube.com/feeds/videos.xml?channel_id="
_MAX_TEXT_CHARS = 8_000


def _resolve_handle_to_channel_id(handle: str) -> str:
    """
    Resolve a YouTube @handle to a channel ID by fetching the channel page.

    YouTube embeds the channel ID in the og:url meta tag and JSON-LD data,
    both of which are present in the static HTML (no JavaScript required).

    Returns the channel ID if found, or the original handle string on failure.
    """
    url = f"https://www.youtube.com/{handle}"
    try:
        client = _get_http_client()
        resp = client.get(url, timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        # og:url meta tag: <meta property="og:url" content="https://www.youtube.com/channel/UCxxxxxx">
        match = re.search(r'youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})', html)
        if match:
            logger.debug("Resolved YouTube handle %r → %s", handle, match.group(1))
            return match.group(1)
        # JSON-LD / inline data fallback
        match = re.search(r'"externalId":"(UC[a-zA-Z0-9_-]{22})"', html)
        if match:
            logger.debug("Resolved YouTube handle %r → %s (externalId)", handle, match.group(1))
            return match.group(1)
    except Exception as exc:
        logger.warning("Could not resolve YouTube handle %r: %s", handle, exc)
    logger.warning("Could not resolve YouTube handle %r — feed fetch will likely fail", handle)
    return handle


def _extract_channel_id(platform_id: str) -> str:
    """
    Extract the channel ID from a full URL, raw ID, or @handle.

    Handles:
      - Raw channel ID: "UCYdgBE5yGEFO4zZ4KxLJGKQ"
      - URL: "https://www.youtube.com/channel/UCYdgBE5yGEFO4zZ4KxLJGKQ"
      - Feed URL with channel_id= query param
      - @handle: resolved dynamically by fetching the channel page (no API key needed)
    """
    pid = platform_id.strip()
    # Already a raw channel ID
    if re.match(r"^UC[a-zA-Z0-9_-]{22}$", pid):
        return pid
    # Extract from channel URL
    match = re.search(r"youtube\.com/channel/(UC[a-zA-Z0-9_-]{22})", pid)
    if match:
        return match.group(1)
    # Feed URL already
    match = re.search(r"channel_id=(UC[a-zA-Z0-9_-]{22})", pid)
    if match:
        return match.group(1)
    # @handle format — resolve via HTTP (free, no API key required)
    if pid.startswith("@"):
        return _resolve_handle_to_channel_id(pid)
    logger.debug("Could not extract channel ID from %r — using as-is", pid)
    return pid


def _fetch_youtube_feed(channel_id: str) -> list[dict]:
    """
    Fetch and parse YouTube's Atom feed for *channel_id*.

    Returns a list of dicts:
      {title, link, description, published_at, author}
    """
    url = f"{_YOUTUBE_FEED_BASE}{channel_id}"
    try:
        client = _get_http_client()
        resp = client.get(url, timeout=20.0)
        resp.raise_for_status()
        xml = resp.text
    except Exception as exc:
        logger.warning("YouTube feed fetch failed for channel %s: %s", channel_id, exc)
        return []

    items = []
    # Parse Atom <entry> blocks
    entry_re = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)
    for block in entry_re.findall(xml):
        def _tag(name: str) -> str:
            """Extract text of first occurrence of <name>...</name>."""
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.DOTALL)
            return _strip_html(m.group(1).strip()) if m else ""

        title = _tag("title")
        # video link is in <link rel="alternate" href="..."/>
        link_match = re.search(r'<link[^>]+href=["\']([^"\']+)["\']', block)
        link = link_match.group(1) if link_match else ""

        # YouTube feed uses <media:description> for video description
        desc_match = re.search(r"<media:description[^>]*>(.*?)</media:description>", block, re.DOTALL)
        description = _strip_html(desc_match.group(1).strip()) if desc_match else ""
        if not description:
            description = _tag("summary") or _tag("content")

        # Published date
        published = _tag("published") or _tag("updated")

        # Author from channel name (appears in feed header; entries often omit it)
        author = _tag("name")

        items.append({
            "title": title,
            "link": link,
            "description": description,
            "published_at": published,
            "author": author,
        })

    logger.info("YouTube: parsed %d entries for channel %s", len(items), channel_id)
    return items


class YouTubeAdapter(BaseIngestionAdapter):
    """
    Ingests YouTube channel videos via the public Atom feed.

    The *platform_id* is a YouTube channel ID (e.g., "UCYdgBE5yGEFO4zZ4KxLJGKQ")
    or a full channel URL.
    """

    @property
    def platform(self) -> str:
        return "youtube"

    @staticmethod
    def supports_platform(platform: str) -> bool:
        return platform == "youtube"

    def fetch(
        self,
        account_id: str,
        platform_id: str,
        max_items: int = 50,
        domains: str = "",
        user_id: str = "default",
        layer: int = 1,
        source_tag: str = "youtube",
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
        source_tag: str = "youtube",
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
        channel_id = _extract_channel_id(platform_id)
        raw_items = _fetch_youtube_feed(channel_id)
        if not raw_items:
            return []

        results = []
        for item in raw_items[:max_items]:
            pub_at = item.get("published_at", "")
            if since_iso and pub_at:
                try:
                    pub_dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                    since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
                    if pub_dt <= since_dt:
                        continue
                except Exception:
                    pass

            title = item.get("title", "").strip()
            description = item.get("description", "").strip()
            text = f"{title}\n\n{description}" if title else description
            text = text.strip()[:_MAX_TEXT_CHARS]
            if not text:
                continue

            results.append(RawFetchResult(
                text=text,
                source_url=item.get("link", f"https://www.youtube.com/channel/{channel_id}"),
                platform=self.platform,
                published_at=pub_at,
                author=item.get("author", ""),
                title=title,
                account_id=account_id,
                domains=domains,
                user_id=user_id,
                layer=layer,
                source=source_tag,
            ))

        logger.info("YouTubeAdapter: %d items for channel %s", len(results), channel_id)
        return results
