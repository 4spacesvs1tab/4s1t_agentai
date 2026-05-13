"""
KB Ingestion adapters — per-platform content fetchers.

Phase KB-1: WebsiteAdapter (RSS + HTML fallback)
Phase KB-2: NitterAdapter, YouTubeAdapter, PodcastAdapter, NostrAdapter
            IngestionRunner (unified dispatch, cursor-based incremental fetch)
"""
from kb.ingestion.website_adapter import WebsiteAdapter
from kb.ingestion.nitter_adapter import NitterAdapter
from kb.ingestion.youtube_adapter import YouTubeAdapter
from kb.ingestion.podcast_adapter import PodcastAdapter
from kb.ingestion.nostr_adapter import NostrAdapter
from kb.ingestion.ingestion_runner import (
    AdapterRegistry,
    get_adapter,
    ingest_account,
    ingest_all_accounts,
    IngestionResult,
)

__all__ = [
    "WebsiteAdapter",
    "NitterAdapter",
    "YouTubeAdapter",
    "PodcastAdapter",
    "NostrAdapter",
    "AdapterRegistry",
    "get_adapter",
    "ingest_account",
    "ingest_all_accounts",
    "IngestionResult",
]
