"""
SqliteChunkRepository — SQLite implementation of ChunkRepository.

Abstracts the kb_ingestion_log table for exact-hash deduplication
and ingestion tracking. SQL moved verbatim from kb/preprocessor.py.
No domain logic lives here.
"""
from __future__ import annotations

from typing import Optional

from kb.ports.chunk_repository import ChunkRepository
from infrastructure.sqlite._connection import get_db_connection


class SqliteChunkRepository(ChunkRepository):
    """Reads and writes the kb_ingestion_log table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def hash_exists(self, item_hash: str) -> bool:
        """Return True if *item_hash* is already in kb_ingestion_log."""
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT 1 FROM kb_ingestion_log WHERE item_hash = ? LIMIT 1",
                    (item_hash,),
                )
                return cur.fetchone() is not None
        except Exception:
            return False

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
        """Insert a row into kb_ingestion_log."""
        try:
            with get_db_connection(self._db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO kb_ingestion_log
                        (user_id, account_id, platform, item_url, item_hash,
                         chunk_count, ingestion_type, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        account_id or None,
                        platform,
                        item_url,
                        item_hash,
                        chunk_count,
                        ingestion_type,
                        status,
                    ),
                )
                conn.commit()
        except Exception:
            pass
