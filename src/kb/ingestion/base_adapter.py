"""
Base ingestion adapter interface.

All platform-specific adapters implement BaseIngestionAdapter.
The KB ingestion pipeline calls fetch() and get_new_since() uniformly,
regardless of the underlying platform.

Design reference: KnowledgeBase_design.md §6.2
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawFetchResult:
    """
    A single content item returned by an ingestion adapter.

    All fields are optional except text and source_url. Adapters
    fill in what they can; the preprocessor handles missing fields gracefully.
    """
    text: str                         # raw text content (cleaned by preprocessor)
    source_url: str                   # canonical URL of this item
    platform: str                     # 'website', 'twitter', 'nostr', 'youtube', 'podcast', 'rumble'
    published_at: str = ""            # ISO 8601 string; empty if unknown
    author: str = ""                  # display name of author/account
    title: str = ""                   # article/episode title (for logging / summaries)
    account_id: str = ""              # kb_accounts.id of the owner
    domains: str = ""                 # pipe-separated domain IDs (copied from account config)
    user_id: str = "default"
    layer: int = 1
    source: str = "website"           # source tag: 'babok', 'website', 'twitter', etc.
    ingestion_type: str = "scheduled"
    extra: dict = field(default_factory=dict)   # platform-specific metadata


class BaseIngestionAdapter(ABC):
    """
    Abstract base for all platform ingestion adapters.

    Each adapter is responsible for one platform. It fetches raw content
    from that platform and returns a list of RawFetchResult objects,
    ready for the preprocessor pipeline.
    """

    @property
    @abstractmethod
    def platform(self) -> str:
        """Platform identifier string: 'website', 'twitter', 'nostr', etc."""

    @abstractmethod
    def fetch(
        self,
        account_id: str,
        platform_id: str,
        max_items: int = 50,
    ) -> list[RawFetchResult]:
        """
        Fetch recent content for the given account.

        Args:
            account_id: Internal KB account ID (UUID).
            platform_id: Platform-specific identifier (URL, handle, feed URL, etc.)
            max_items: Maximum number of items to return.

        Returns:
            List of RawFetchResult. Empty list on error (errors should be logged).
        """

    @abstractmethod
    def get_new_since(
        self,
        account_id: str,
        platform_id: str,
        since_iso: str,
        max_items: int = 50,
    ) -> list[RawFetchResult]:
        """
        Fetch only items published after *since_iso* (ISO 8601 string).

        Args:
            account_id: Internal KB account ID.
            platform_id: Platform-specific identifier.
            since_iso: Return items newer than this timestamp.
            max_items: Maximum number of items to return.

        Returns:
            List of RawFetchResult, filtered to items newer than *since_iso*.
        """

    @staticmethod
    def supports_platform(platform: str) -> bool:
        """Return True if this adapter can handle *platform*."""
        return False
