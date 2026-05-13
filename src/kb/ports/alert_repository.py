"""
AlertRepository — domain port for KB alert persistence.

Rule: this file must never import sqlite3, httpx, os.environ, or any I/O
library.  Only standard-library ABCs and TYPE_CHECKING imports are allowed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kb.alert_engine import AlertMatch, KBAlert


class AlertRepository(ABC):
    """Abstract persistence interface for KBAlert and AlertMatch.

    Implementations live in src/infrastructure/sqlite/.
    Wire a concrete implementation at the composition root (initializer).

    SQL origin:
      find_active_by_user   ← alert_engine._load_active_alerts()  (kb_alerts)
      record_match          ← alert_engine._record_match()        (kb_alert_matches + kb_alerts)
      save                  ← alert_engine.create_alert()         (kb_alerts INSERT)
      delete                ← alert management routes             (kb_alerts SET active=0)
      find_pending_matches  ← alert_engine.get_pending_matches()  (kb_alert_matches JOIN kb_alerts)
      mark_matches_delivered← alert_engine.mark_matches_delivered() (kb_alert_matches UPDATE)
    """

    @abstractmethod
    def find_active_by_user(self, user_id: str) -> list[KBAlert]:
        """Return all active alerts for *user_id* with parsed embeddings."""
        ...

    @abstractmethod
    def record_match(self, match: AlertMatch) -> None:
        """Insert an alert match row and update alert.last_triggered_at.

        Both writes happen in a single transaction.
        """
        ...

    @abstractmethod
    def save(self, alert: KBAlert, created_at: str) -> None:
        """Insert a new alert row into kb_alerts."""
        ...

    @abstractmethod
    def delete(self, alert_id: str) -> None:
        """Deactivate an alert (SET active = 0)."""
        ...

    @abstractmethod
    def find_pending_matches(self, user_id: str) -> list[dict]:
        """Return undelivered alert match rows for *user_id*.

        Each dict has keys: id, alert_id, query, chunk_id, source_url,
        account_id, domain, similarity, matched_at.
        """
        ...

    @abstractmethod
    def mark_matches_delivered(self, match_ids: list[int]) -> None:
        """Mark kb_alert_matches rows as delivered."""
        ...
