"""
Podcast RSS ingestion adapter — Phase KB-2.

Fetches podcast episodes from a standard RSS 2.0 feed with iTunes extensions.

Content extracted per episode (priority order):
  1. Whisper STT transcript — if PODCAST_STT_ENABLED=true and audio URL available
  2. Embedded transcript in feed — some feeds include full <podcast:transcript>
  3. Show notes / description from RSS — always available, used as fallback

Set PODCAST_STT_ENABLED=true in docker-compose environment to activate transcription.
Uses nano-gpt Whisper v3 endpoint (same API key as embeddings/chat).
Audio files > 24 MB are skipped (Whisper API limit) — show notes used instead.

Platform identifier (`platform_id`): RSS feed URL, e.g.:
  "https://feeds.transistor.fm/making-sense"
  "https://feeds.captivate.fm/geopolitical-cousins/"

Optional episode-title filter (pipe-separated with |||):
  "https://feeds.fireside.fm/mitpolska/rss|||limity ai"
  Only episodes whose title contains the filter string (case-insensitive) are ingested.

Privacy: All HTTP requests go through Tor SOCKS5 proxy when configured.

Design reference: KnowledgeBase_design.md §6.2
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kb.ingestion.base_adapter import BaseIngestionAdapter, RawFetchResult
from kb.ingestion.website_adapter import _get_http_client, _strip_html, _parse_rss_date

from utils.logger import setup_logger
logger = setup_logger(__name__)

_MAX_TEXT_CHARS = 50_000

# Whisper API hard limit (bytes). Files larger than this are skipped.
_WHISPER_MAX_BYTES = 24 * 1024 * 1024  # 24 MB


def _stt_enabled() -> bool:
    """Return True when Whisper transcription is explicitly enabled."""
    return os.environ.get("PODCAST_STT_ENABLED", "false").lower() in ("1", "true", "yes")


def _get_stt_model() -> str:
    """
    Return the STT model ID from the active provider config (providers.yaml `stt` slot).
    Falls back to 'Whisper-Large-V3' (nano-gpt; $0.0005/min) if not configured.
    """
    try:
        from config.provider_config import get_active_provider
        provider = get_active_provider()
        prefs = getattr(provider, "agent_preferences", {}) or {}
        model = (prefs.get("stt") or [""])[0]
        if model:
            return model
    except Exception:
        pass
    return "Whisper-Large-V3"


def _transcribe_audio(audio_url: str, api_key: str) -> Optional[str]:
    """
    Download audio from *audio_url* and transcribe via nano-gpt Whisper v3.

    Uses the same openai SDK + API key + Tor proxy as the rest of the stack
    (same source as ApiClient in core/api_client.py, sync variant).

    Returns the transcript string, or None if transcription fails or is skipped.

    Failure reasons (all logged, fall back to show notes):
      - File size > 24 MB (Whisper API limit)
      - Download error (network, Tor, 4xx/5xx)
      - API error (quota, wrong format, server error)
    """
    import httpx
    import openai

    base_url = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")

    # Stream-download to a temp file using the existing Tor-aware HTTP client
    client = _get_http_client()
    suffix = Path(audio_url.split("?")[0]).suffix or ".mp3"
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            with client.stream("GET", audio_url, timeout=120.0) as r:
                r.raise_for_status()
                size = 0
                for chunk in r.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > _WHISPER_MAX_BYTES:
                        logger.info(
                            "Audio > 24 MB — skipping STT for %s (show notes used instead)",
                            audio_url,
                        )
                        return None
                    tmp.write(chunk)
    except Exception as exc:
        logger.warning("Audio download failed %s: %s", audio_url, exc)
        return None

    # Submit to Whisper via openai SDK (same client infrastructure as ApiClient)
    # Route through Tor if configured — same TOR_SOCKS_PROXY env var used everywhere
    try:
        tor_proxy = os.environ.get("TOR_SOCKS_PROXY", "")
        oai_kwargs: dict = {"api_key": api_key, "base_url": base_url}
        if tor_proxy:
            oai_kwargs["http_client"] = httpx.Client(
                proxies={"all://": f"socks5://{tor_proxy}"},
                timeout=300.0,
            )
        oai = openai.OpenAI(**oai_kwargs)

        stt_model = _get_stt_model()
        logger.debug("STT model: %s", stt_model)
        with open(tmp_path, "rb") as audio_file:
            result = oai.audio.transcriptions.create(
                model=stt_model,
                file=audio_file,
                response_format="text",
            )
        # response_format="text" returns a str directly
        transcript = result if isinstance(result, str) else getattr(result, "text", "")
        transcript = transcript.strip()
        logger.info("STT transcript: %d chars from %s", len(transcript), audio_url)
        return transcript if transcript else None
    except Exception as exc:
        logger.warning("Whisper STT failed for %s: %s", audio_url, exc)
        return None
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


def _resolve_to_rss(url: str) -> str:
    """
    Convert a non-RSS podcast page URL to an RSS feed URL.

    Supported:
      • Apple Podcasts  — https://podcasts.apple.com/*/id<podcast_id>
        → itunes.apple.com/lookup?id=<id>&entity=podcast  →  feedUrl
      • Fountain.fm     — https://fountain.fm/show/<id>
        → RSS is embedded as <link rel="alternate" type="application/rss+xml">
      • Everything else — returned unchanged (assumed to already be RSS)

    Never raises; returns original url on any failure.
    """
    import re as _re
    import httpx as _httpx

    # Apple Podcasts
    apple_m = _re.search(r"podcasts\.apple\.com/.*/id(\d+)", url, _re.I)
    if apple_m:
        podcast_id = apple_m.group(1)
        try:
            resp = _httpx.get(
                f"https://itunes.apple.com/lookup?id={podcast_id}&entity=podcast",
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            for r in results:
                feed_url = r.get("feedUrl", "")
                if feed_url:
                    logger.info("Apple Podcasts %s → RSS %s", url, feed_url)
                    return feed_url
        except Exception as exc:
            logger.warning("Apple Podcasts lookup failed for %s: %s", url, exc)
        return url  # lookup failed, return original

    # Fountain.fm — fetch the page and extract RSS link.
    # Fountain.fm is a Next.js app; the RSS URL is embedded in the JS payload
    # as a JSON property ("rss":"https://..."), not as a <link> HTML tag.
    # We try the JSON property first, then fall back to the <link> tag pattern
    # for any future layout changes.
    if "fountain.fm/show" in url.lower():
        try:
            client = _get_http_client()
            resp = client.get(url, timeout=15.0)
            resp.raise_for_status()
            # Primary: JSON property embedded in Next.js SSR payload.
            # Fountain.fm escapes the JSON inside JS strings, so quotes appear
            # as \" in the raw HTML: \"rss\":\"https://...\".
            # Try backslash-escaped form first, then plain JSON form.
            rss_m = _re.search(r'\\"rss\\":\\"(https?://[^\\"]+)\\"', resp.text)
            if not rss_m:
                rss_m = _re.search(r'"rss"\s*:\s*"(https?://[^"]+)"', resp.text)
            if not rss_m:
                # Fallback: standard <link rel="alternate" type="application/rss+xml">
                rss_m = _re.search(
                    r'<link[^>]+type=["\']application/rss\+xml["\'][^>]+href=["\']([^"\']+)["\']',
                    resp.text, _re.I,
                )
            if not rss_m:
                rss_m = _re.search(
                    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/rss\+xml["\']',
                    resp.text, _re.I,
                )
            if rss_m:
                feed_url = rss_m.group(1)
                logger.info("Fountain.fm %s → RSS %s", url, feed_url)
                return feed_url
        except Exception as exc:
            logger.warning("Fountain.fm RSS lookup failed for %s: %s", url, exc)
        return url

    return url  # already an RSS feed URL


def _parse_platform_id(platform_id: str) -> tuple[str, str]:
    """
    Split platform_id into (feed_url, title_filter), resolving non-RSS URLs.

    Supports optional episode title filter encoded as:
      https://example.com/feed|||limity ai
    Returns (rss_feed_url, title_filter).
    """
    if "|||" in platform_id:
        raw_url, _, title_filter = platform_id.partition("|||")
        return _resolve_to_rss(raw_url.strip()), title_filter.strip().lower()
    return _resolve_to_rss(platform_id), ""


def _fetch_podcast_feed(feed_url: str) -> list[dict]:
    """
    Fetch and parse a podcast RSS 2.0 feed.

    Uses the Tor-aware httpx client (30 s timeout) to fetch the XML, then
    passes the content string to feedparser so that:
      - All requests go through Tor (privacy parity with other adapters).
      - A hard 30 s network timeout prevents hanging on unresponsive feeds.

    Returns a list of episode dicts:
      {title, link, description, published_at, author, duration, enclosure_url}
    """
    try:
        import feedparser
        client = _get_http_client()
        resp = client.get(feed_url, timeout=30.0)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        return _parse_with_feedparser(feed)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Podcast feed fetch failed %s: %s", feed_url, exc)
    # Fallback: raw HTTP + regex (already uses _get_http_client with timeout)
    return _parse_raw_podcast(feed_url)


def _parse_with_feedparser(feed) -> list[dict]:
    """Parse a feedparser feed object into episode dicts."""
    episodes = []
    # Feed-level author as fallback
    feed_author = ""
    if hasattr(feed.feed, "author"):
        feed_author = feed.feed.author
    elif hasattr(feed.feed, "title"):
        feed_author = feed.feed.title

    for entry in feed.entries:
        # Get the best available text content
        description = ""
        # iTunes summary is usually the longest version
        if hasattr(entry, "itunes_summary"):
            description = entry.itunes_summary
        if not description and hasattr(entry, "content") and entry.content:
            description = entry.content[0].get("value", "")
        if not description and hasattr(entry, "summary"):
            description = entry.summary

        description = _strip_html(description)

        published = ""
        if hasattr(entry, "published"):
            published = _parse_rss_date(entry.published)
        elif hasattr(entry, "updated"):
            published = _parse_rss_date(entry.updated)

        author = getattr(entry, "author", "") or feed_author

        duration = ""
        if hasattr(entry, "itunes_duration"):
            duration = entry.itunes_duration

        enclosure_url = ""
        if hasattr(entry, "enclosures") and entry.enclosures:
            enclosure_url = entry.enclosures[0].get("href", "")

        # Check for transcript in content
        transcript = ""
        if hasattr(entry, "content"):
            for c in entry.content:
                if "transcript" in c.get("type", "").lower() or len(c.get("value", "")) > 5000:
                    transcript = _strip_html(c.get("value", ""))
                    break

        episodes.append({
            "title": getattr(entry, "title", ""),
            "link": getattr(entry, "link", ""),
            "description": transcript or description,
            "published_at": published,
            "author": author,
            "duration": duration,
            "enclosure_url": enclosure_url,
        })
    return episodes


def _parse_raw_podcast(feed_url: str) -> list[dict]:
    """
    Fallback podcast RSS parser using httpx + regex (no feedparser).
    """
    try:
        client = _get_http_client()
        resp = client.get(feed_url, timeout=30.0)
        resp.raise_for_status()
        xml = resp.text
    except Exception as exc:
        logger.warning("Podcast feed fetch failed %s: %s", feed_url, exc)
        return []

    # Feed-level author
    feed_author_m = re.search(r"<itunes:author[^>]*>(.*?)</itunes:author>", xml, re.DOTALL)
    feed_author = _strip_html(feed_author_m.group(1)) if feed_author_m else ""

    episodes = []
    item_re = re.compile(r"<item>(.*?)</item>", re.DOTALL | re.IGNORECASE)

    for block in item_re.findall(xml):
        def _tag(name: str, default: str = "") -> str:
            m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.DOTALL | re.IGNORECASE)
            return _strip_html(m.group(1).strip()) if m else default

        title = _tag("title")
        link_m = re.search(r"<link>(.*?)</link>", block, re.DOTALL)
        link = _strip_html(link_m.group(1)) if link_m else ""

        # Description hierarchy: itunes:summary > content:encoded > description
        description = _tag("itunes:summary") or _tag("content:encoded") or _tag("description")

        pubdate = _tag("pubDate") or _tag("dc:date")
        published = _parse_rss_date(pubdate)

        author = _tag("itunes:author") or _tag("author") or feed_author

        enclosure_m = re.search(r'<enclosure[^>]+url=["\']([^"\']+)["\']', block, re.IGNORECASE)
        enclosure_url = enclosure_m.group(1) if enclosure_m else ""

        duration = _tag("itunes:duration")

        episodes.append({
            "title": title,
            "link": link,
            "description": description,
            "published_at": published,
            "author": author,
            "duration": duration,
            "enclosure_url": enclosure_url,
        })

    logger.info("Podcast raw parser: %d episodes from %s", len(episodes), feed_url)
    return episodes


class PodcastAdapter(BaseIngestionAdapter):
    """
    Ingests podcast episodes from an RSS 2.0 feed.

    The *platform_id* is the RSS feed URL.
    """

    @property
    def platform(self) -> str:
        return "podcast"

    @staticmethod
    def supports_platform(platform: str) -> bool:
        return platform == "podcast"

    def fetch(
        self,
        account_id: str,
        platform_id: str,
        max_items: int = 20,
        domains: str = "",
        user_id: str = "default",
        layer: int = 1,
        source_tag: str = "podcast",
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
        max_items: int = 20,
        domains: str = "",
        user_id: str = "default",
        layer: int = 1,
        source_tag: str = "podcast",
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
        feed_url, title_filter = _parse_platform_id(platform_id)
        raw_items = _fetch_podcast_feed(feed_url)
        if not raw_items:
            return []

        # Apply optional episode title filter
        if title_filter:
            raw_items = [e for e in raw_items if title_filter in e.get("title", "").lower()]
            logger.info("PodcastAdapter: %d episodes match title filter %r", len(raw_items), title_filter)

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
            enclosure_url = item.get("enclosure_url", "")

            # --- Transcript priority: STT > feed transcript > show notes ---
            api_key = os.environ.get("NANO_GPT_API_KEY", "")
            transcript: Optional[str] = None
            transcript_source = "show_notes"

            if _stt_enabled() and enclosure_url and api_key:
                transcript = _transcribe_audio(enclosure_url, api_key)
                if transcript:
                    transcript_source = "whisper_stt"

            body = transcript or description
            text = f"{title}\n\n{body}" if title else body
            text = text.strip()[:_MAX_TEXT_CHARS]
            if not text:
                continue

            extra = {"transcript_source": transcript_source}
            if item.get("duration"):
                extra["duration"] = item["duration"]
            if enclosure_url:
                extra["enclosure_url"] = enclosure_url

            results.append(RawFetchResult(
                text=text,
                source_url=item.get("link", platform_id),
                platform=self.platform,
                published_at=pub_at,
                author=item.get("author", ""),
                title=title,
                account_id=account_id,
                domains=domains,
                user_id=user_id,
                layer=layer,
                source=source_tag,
                extra=extra,
            ))

        logger.info("PodcastAdapter: %d episodes from %s", len(results), feed_url)
        return results
