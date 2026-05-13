"""
IngestionService — application service for KB manual ingestion.

Orchestrates the manual ingestion trigger.
Route handlers call this service; they never call ingest_all_accounts directly.

DDD Rule 3: No FastAPI / HTTPException / Request imports here.
No sqlite3.connect() — db_path injected at construction.
No os.environ reads — api_key injected at construction.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger(__name__)


class IngestionService:
    """Application service for triggering KB ingestion runs.

    Constructor receives db_path and api_key so that wiring (os.environ reads)
    happens in the FastAPI dependency, not inside the service.
    """

    def __init__(self, db_path: str, api_key: str) -> None:
        self._db_path = db_path
        self._api_key = api_key

    # ------------------------------------------------------------------
    # trigger_ingestion
    # ------------------------------------------------------------------

    async def trigger_ingestion(
        self,
        user_id: str,
        domain: Optional[str] = None,
        max_items_per_platform: int = 50,
    ) -> dict:
        """Schedule a background manual ingestion run.

        Returns {"status": "accepted", "message": ...} immediately (fire-and-forget).
        Results are stored in ChromaDB and kb_ingestion_log.

        Mirrors ingest_routes.trigger_ingestion verbatim.
        """
        db = self._db_path
        api_key = self._api_key

        async def _run() -> None:
            from kb.ingestion.ingestion_runner import ingest_all_accounts
            try:
                results = ingest_all_accounts(
                    user_id=user_id,
                    domain=domain,
                    ingestion_type="manual",
                    max_items_per_platform=max_items_per_platform,
                    db_path=db,
                    api_key=api_key,
                )
                total = sum(r.chunks_stored for r in results)
                logger.info(
                    "Manual ingestion complete user=%s domain=%s: %d chunks stored across %d accounts",
                    user_id, domain or "all", total, len(results),
                )
            except Exception as exc:
                logger.error("Manual ingestion failed for user=%s: %s", user_id, exc)

        asyncio.create_task(_run())
        return {
            "status": "accepted",
            "message": f"Ingestion started for user={user_id!r} domain={domain or 'all'}.",
        }
