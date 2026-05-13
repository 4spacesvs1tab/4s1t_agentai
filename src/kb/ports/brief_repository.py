"""
BriefRepository — domain port for KB brief persistence.

Rule: this file must never import sqlite3, httpx, os.environ, or any I/O
library.  Only standard-library ABCs are allowed here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BriefRepository(ABC):
    """Abstract persistence interface for KB briefs.

    Implementations live in src/infrastructure/sqlite/.
    Wire a concrete adapter at the composition root.
    """

    @abstractmethod
    def exists(self, domain: str, date_str: str) -> bool:
        """Return True if a brief for (domain, date_str) is already recorded.

        date_str format: YYYY-MM-DD. Checked against DATE(window_end).
        """
        ...

    @abstractmethod
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
        """Insert a kb_briefs row and return the new brief_id (UUID)."""
        ...

    @abstractmethod
    def mark_delivered(self, brief_id: str) -> None:
        """Set delivered=1 and update generated_at timestamp for *brief_id*."""
        ...

    @abstractmethod
    def find_undelivered(self, user_id: str) -> list[dict]:
        """Return all kb_briefs rows with delivered=0 for *user_id*.

        Ordered by generated_at ASC (oldest first).
        """
        ...
