"""
ChunkRepository — domain port for KB ingestion-log persistence.

Abstracts the kb_ingestion_log table used for exact-hash deduplication
and ingestion status tracking.

Rule: this file must never import sqlite3, httpx, os.environ, or any I/O
library.  Only standard-library ABCs are allowed here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class ChunkRepository(ABC):
    """Abstract persistence interface for chunk ingestion records.

    Implementations live in src/infrastructure/sqlite/.
    Wire a concrete adapter at the composition root.
    """

    @abstractmethod
    def hash_exists(self, item_hash: str) -> bool:
        """Return True if *item_hash* is already in kb_ingestion_log.

        Used for exact-content deduplication before embedding.
        """
        ...

    @abstractmethod
    def log_ingestion(
        self,
        user_id: str,
        account_id: Optional[str],
        platform: str,
        item_url: Optional[str],
        item_hash: str,
        chunk_count: int,
        ingestion_type: str,
        status: str = "ok",
    ) -> None:
        """Insert a row into kb_ingestion_log.

        Called after each content item is processed.
        status: 'ok' | 'error' | 'skipped' | 'dedup_skipped' | 'embedding_failed'
        """
        ...
