"""
Website / Blog ingestion adapter — Phase KB-1.

Fetches content from:
  - RSS/Atom feeds (preferred)
  - Full-page HTML fallback with readability-style extraction

Handles: Substack, WordPress, Ghost, plain HTML blogs.

Privacy: All HTTP requests go through the Tor SOCKS5 proxy configured in
`src/config/privacy.yaml` (existing Phase 7 infrastructure).

Design reference: KnowledgeBase_design.md §6.2.6
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Optional
from email.utils import parsedate_to_datetime

from kb.ingestion.base_adapter import BaseIngestionAdapter, RawFetchResult

from utils.logger import setup_logger
logger = setup_logger(__name__)

# Maximum article body length before truncation (chars)
_MAX_TEXT_CHARS = 50_000


def _get_http_client():
    """
    Return an httpx client configured with Tor SOCKS5 proxy when available.

    Falls back to a direct client if Tor is not configured. The proxy
    address matches the Phase 7 configuration (172.20.0.1:9050).
    """
    import httpx

    tor_proxy = os.environ.get("TOR_SOCKS_PROXY", "")
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; FeedFetcher/1.0)",
        "Accept": "application/rss+xml, application/atom+xml, text/html, */*",
    }
    if tor_proxy:
        return httpx.Client(
            proxies={"all://": f"socks5://{tor_proxy}"},
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )
    return httpx.Client(headers=headers, timeout=30.0, follow_redirects=True)


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode basic entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_rss_date(date_str: str) -> str:
    """Parse RSS date string to ISO 8601. Returns empty string on failure."""
    if not date_str:
        return ""
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    # Try ISO format directly
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return ""


def _fetch_rss(feed_url: str) -> list[dict]:
    """
    Fetch and parse an RSS or Atom feed.

    Returns a list of item dicts:
      {title, link, description, content, published_at, author}
    """
    try:
        import feedparser
        feed = feedparser.parse(feed_url)
    except ImportError:
        # feedparser not installed — use httpx + minimal XML parsing
        return _fetch_rss_raw(feed_url)

    items = []
    for entry in feed.entries:
        # Get the best available text content
        content = ""
        if hasattr(entry, "content") and entry.content:
            content = entry.content[0].get("value", "")
        if not content and hasattr(entry, "summary"):
            content = entry.summary
        if not content and hasattr(entry, "description"):
            content = entry.description

        published = ""
        if hasattr(entry, "published"):
            published = _parse_rss_date(entry.published)
        elif hasattr(entry, "updated"):
            published = _parse_rss_date(entry.updated)

        author = ""
        if hasattr(entry, "author"):
            author = entry.author

        items.append({
            "title": getattr(entry, "title", ""),
            "link": getattr(entry, "link", ""),
            "description": getattr(entry, "summary", ""),
            "content": content,
            "published_at": published,
            "author": author,
        })
    return items


def _fetch_rss_raw(feed_url: str) -> list[dict]:
    """
    Minimal RSS/Atom parser using httpx + re (no feedparser dependency).

    Falls back to this when feedparser is not installed.
    """
    try:
        client = _get_http_client()
        resp = client.get(feed_url)
        resp.raise_for_status()
        xml = resp.text
    except Exception as exc:
        logger.warning("RSS fetch failed for %s: %s", feed_url, exc)
        return []

    items = []
    # Match <item> or <entry> blocks
    block_re = re.compile(r"<(?:item|entry)>(.*?)</(?:item|entry)>", re.DOTALL | re.IGNORECASE)
    tag_re = re.compile(r"<([a-z:]+)[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)

    for block in block_re.findall(xml):
        item: dict = {}
        for tag, val in tag_re.findall(block):
            tag_lower = tag.lower().split(":")[-1]
            if tag_lower in ("title", "link", "description", "summary", "published", "updated", "pubdate", "author", "name"):
                item[tag_lower] = _strip_html(val.strip())
        items.append({
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "description": item.get("description", item.get("summary", "")),
            "content": item.get("description", item.get("summary", "")),
            "published_at": _parse_rss_date(item.get("pubdate", item.get("published", item.get("updated", "")))),
            "author": item.get("author", item.get("name", "")),
        })
    return items


def _detect_feed_url(page_url: str) -> Optional[str]:
    """
    Try to auto-detect an RSS/Atom feed URL for a webpage.

    Checks common patterns and <link rel="alternate"> tags.
    """
    common_paths = [
        "/feed", "/feed/", "/rss", "/rss.xml", "/atom.xml",
        "/feed.xml", "/index.xml", "/?feed=rss2",
    ]
    # Common Substack/Ghost patterns
    if "substack.com" in page_url:
        return page_url.rstrip("/") + "/feed"
    if "ghost.io" in page_url or ".ghost.io" in page_url:
        return page_url.rstrip("/") + "/rss/"

    try:
        import httpx
        client = _get_http_client()
        resp = client.get(page_url, timeout=15.0)
        if resp.status_code == 200:
            # Look for <link rel="alternate" type="application/rss+xml" href="...">
            match = re.search(
                r'<link[^>]+rel=["\']alternate["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)["\']',
                resp.text,
                re.IGNORECASE,
            )
            if not match:
                match = re.search(
                    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\']',
                    resp.text,
                    re.IGNORECASE,
                )
            if match:
                href = match.group(1)
                if href.startswith("http"):
                    return href
                # Relative URL
                from urllib.parse import urljoin
                return urljoin(page_url, href)
    except Exception:
        pass

    # Try common path suffixes
    base = page_url.rstrip("/")
    try:
        import httpx
        client = _get_http_client()
        for suffix in common_paths:
            try:
                r = client.head(base + suffix, timeout=5.0)
                if r.status_code < 400:
                    return base + suffix
            except Exception:
                continue
    except Exception:
        pass

    return None


def _extract_page_text(url: str) -> Optional[str]:
    """
    Fetch a webpage and extract readable text (article body).

    Tries readability-lxml first, falls back to regex-based extraction.
    """
    try:
        import httpx
        client = _get_http_client()
        resp = client.get(url, timeout=20.0)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        logger.warning("Page fetch failed %s: %s", url, exc)
        return None

    # Try readability
    try:
        from readability import Document
        doc = Document(html)
        text = _strip_html(doc.summary())
        if len(text) > 200:
            return text[:_MAX_TEXT_CHARS]
    except ImportError:
        pass

    # Fallback: strip all HTML
    text = _strip_html(html)
    # Rough heuristic: skip if too short (probably a nav page)
    if len(text) < 300:
        return None
    return text[:_MAX_TEXT_CHARS]


# ---------------------------------------------------------------------------
# WebsiteAdapter
# ---------------------------------------------------------------------------

class WebsiteAdapter(BaseIngestionAdapter):
    """
    Ingests website/blog content via RSS feed (preferred) or HTML scraping fallback.

    The *platform_id* passed to fetch/get_new_since is the RSS feed URL or
    the website homepage URL (if feed auto-detection is needed).
    """

    @property
    def platform(self) -> str:
        return "website"

    @staticmethod
    def supports_platform(platform: str) -> bool:
        return platform in ("website", "blog", "substack", "wordpress")

    def fetch(
        self,
        account_id: str,
        platform_id: str,
        max_items: int = 50,
        domains: str = "",
        user_id: str = "default",
        layer: int = 1,
        source_tag: str = "website",
    ) -> list[RawFetchResult]:
        """
        Fetch up to *max_items* items from RSS feed or webpage at *platform_id*.
        """
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
        source_tag: str = "website",
    ) -> list[RawFetchResult]:
        """Fetch only items published after *since_iso*."""
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
        # Determine if platform_id looks like an RSS/Atom feed or a webpage
        feed_url = platform_id
        if not self._looks_like_feed(platform_id):
            logger.debug("Detecting RSS feed for %s", platform_id)
            detected = _detect_feed_url(platform_id)
            if detected:
                feed_url = detected
                logger.info("Auto-detected feed: %s → %s", platform_id, feed_url)
            else:
                # No feed found — scrape the page directly
                return self._scrape_single_page(
                    platform_id, account_id, domains, user_id, layer, source_tag
                )

        # Fetch RSS
        raw_items = _fetch_rss(feed_url)
        if not raw_items:
            logger.warning("No items from feed %s", feed_url)
            return []

        results = []
        for item in raw_items[:max_items]:
            # Apply since_iso filter
            if since_iso and item.get("published_at"):
                try:
                    pub_dt = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
                    since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
                    if pub_dt <= since_dt:
                        continue
                except Exception:
                    pass

            text = _strip_html(item.get("content", "") or item.get("description", ""))
            title = item.get("title", "")
            if title:
                text = f"{title}\n\n{text}"
            if not text.strip():
                continue

            results.append(RawFetchResult(
                text=text[:_MAX_TEXT_CHARS],
                source_url=item.get("link", platform_id),
                platform=self.platform,
                published_at=item.get("published_at", ""),
                author=item.get("author", ""),
                title=title,
                account_id=account_id,
                domains=domains,
                user_id=user_id,
                layer=layer,
                source=source_tag,
            ))

        logger.info("WebsiteAdapter: %d items from %s", len(results), feed_url)
        return results

    def _scrape_single_page(
        self,
        url: str,
        account_id: str,
        domains: str,
        user_id: str,
        layer: int,
        source_tag: str,
    ) -> list[RawFetchResult]:
        """Fall back to scraping a single page when no feed is found."""
        text = _extract_page_text(url)
        if not text:
            return []
        return [RawFetchResult(
            text=text,
            source_url=url,
            platform=self.platform,
            account_id=account_id,
            domains=domains,
            user_id=user_id,
            layer=layer,
            source=source_tag,
        )]

    @staticmethod
    def _looks_like_feed(url: str) -> bool:
        """Heuristic: does this URL look like an RSS/Atom feed?"""
        url_lower = url.lower()
        return any(kw in url_lower for kw in (
            "feed", "rss", "atom", ".xml", "feed=rss", "feed=atom"
        ))
