"""
AlertService — application service for KB alert subscription management.

Orchestrates alert CRUD via AlertRepository.
Route handlers call this service; they never touch sqlite3 or alert_engine directly.

DDD Rule 3: No FastAPI / HTTPException / Request imports here.
Uses get_db_connection() from infrastructure for queries not covered by AlertRepository.
No os.environ reads — api_key injected at construction.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from kb.ports.alert_repository import AlertRepository
from infrastructure.sqlite._connection import get_db_connection
from utils.logger import setup_logger

logger = setup_logger(__name__)


class AlertService:
    """Application service for KB alert subscriptions.

    Constructor receives AlertRepository (E2 pattern), db_path (for queries
    that need columns not exposed by the repository), and api_key (for
    embedding computation).
    """

    def __init__(
        self,
        alert_repo: AlertRepository,
        db_path: str,
        api_key: str,
    ) -> None:
        self._alert_repo = alert_repo
        self._db_path = db_path
        self._api_key = api_key

    # ------------------------------------------------------------------
    # list_alerts
    # ------------------------------------------------------------------

    async def list_alerts(self, user_id: str) -> list[dict]:
        """Return all active alert subscriptions for *user_id*.

        Mirrors alert_routes.list_kb_alerts verbatim, including created_at
        and last_triggered_at fields not exposed by AlertRepository.
        Raises RuntimeError on DB failure (route maps to 500).
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT id, user_id, query, domain_filter, account_filter,
                           similarity_threshold, active, created_at, last_triggered_at
                    FROM kb_alerts WHERE user_id = ? AND active = 1
                    ORDER BY created_at DESC
                    """,
                    (user_id,),
                )
                rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["domain_filter"] = json.loads(r["domain_filter"]) if r["domain_filter"] else None
                r["account_filter"] = json.loads(r["account_filter"]) if r["account_filter"] else None
            return rows
        except Exception as exc:
            logger.error("list_alerts failed: %s", exc)
            raise RuntimeError("Failed to load alerts") from exc

    # ------------------------------------------------------------------
    # create_alert
    # ------------------------------------------------------------------

    async def create_alert(
        self,
        user_id: str,
        query: str,
        domain_filter: Optional[list[str]] = None,
        account_filter: Optional[list[str]] = None,
        similarity_threshold: float = 0.85,
    ) -> str:
        """Create a new semantic alert subscription.

        Embeds the query text using bge-m3 via api_key.
        Raises ValueError if embedding is unavailable.
        Returns the new alert UUID.

        Mirrors alert_routes.create_kb_alert verbatim.
        """
        from core.exceptions import EmbeddingError
        from kb.alert_engine import KBAlert, get_alert_engine

        embedding: list[float] = []
        if self._api_key:
            try:
                from kb.preprocessor import _embed_single
                embedding = _embed_single(query, self._api_key)
            except EmbeddingError as exc:
                logger.warning("Failed to embed alert query: %s", exc)

        if not embedding:
            raise ValueError(
                "Embedding service unavailable — cannot create alert without a query embedding."
            )

        alert_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        alert = KBAlert(
            id=alert_id,
            user_id=user_id,
            query=query,
            query_embedding=embedding,
            domain_filter=domain_filter,
            account_filter=account_filter,
            similarity_threshold=similarity_threshold,
            active=True,
        )
        self._alert_repo.save(alert, created_at=now)

        # Invalidate engine cache so the new alert is picked up immediately
        get_alert_engine(db_path=self._db_path)._alert_cache.pop(user_id, None)
        logger.info("Created alert %s for user=%s query=%r", alert_id, user_id, query[:60])
        return alert_id

    # ------------------------------------------------------------------
    # delete_alert
    # ------------------------------------------------------------------

    async def delete_alert(self, alert_id: str, user_id: str) -> None:
        """Deactivate an alert (soft delete — sets active=0).

        Raises LookupError if the alert does not exist or does not belong
        to user_id (route maps both cases to 404).

        Mirrors alert_routes.delete_kb_alert verbatim.
        """
        from kb.alert_engine import get_alert_engine

        # Ownership check: alert must be active and owned by user_id
        active = self._alert_repo.find_active_by_user(user_id)
        if not any(a.id == alert_id for a in active):
            raise LookupError(f"Alert not found: {alert_id}")

        self._alert_repo.delete(alert_id)

        # Invalidate engine cache
        get_alert_engine(db_path=self._db_path)._alert_cache.pop(user_id, None)
