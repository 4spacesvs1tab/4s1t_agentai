"""
SqliteBriefRepository — SQLite implementation of BriefRepository.

All SQL is moved verbatim from kb/brief_dispatcher.py.
No domain logic lives here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from kb.ports.brief_repository import BriefRepository
from infrastructure.sqlite._connection import get_db_connection


class SqliteBriefRepository(BriefRepository):
    """Reads and writes the kb_briefs table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def exists(self, domain: str, date_str: str) -> bool:
        """Return True if a brief for (domain, date_str) already exists."""
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT 1 FROM kb_briefs WHERE domain = ? AND DATE(window_end) = ? LIMIT 1",
                    (domain, date_str),
                )
                return cur.fetchone() is not None
        except Exception:
            return False

    def save(
        self,
        user_id: str,
        domain: str,
        frequency: str,
        content: str,
        window_start: str,
        window_end: str,
        extended_window: bool = False,
    ) -> str:
        """Insert a kb_briefs row and return the new brief_id."""
        brief_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with get_db_connection(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO kb_briefs
                    (id, user_id, domain, frequency, content,
                     window_start, window_end, generated_at, delivered, extended_window)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    brief_id, user_id, domain, frequency, content,
                    window_start, window_end, now, int(extended_window),
                ),
            )
            conn.commit()
        return brief_id

    def mark_delivered(self, brief_id: str) -> None:
        """Set delivered=1 on the given brief."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            with get_db_connection(self._db_path) as conn:
                conn.execute(
                    "UPDATE kb_briefs SET delivered = 1, generated_at = ? WHERE id = ?",
                    (now, brief_id),
                )
                conn.commit()
        except Exception:
            pass

    def find_undelivered(self, user_id: str) -> list[dict]:
        """Return undelivered briefs for *user_id*, oldest first."""
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT * FROM kb_briefs WHERE user_id = ? AND delivered = 0 ORDER BY generated_at ASC",
                    (user_id,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []
