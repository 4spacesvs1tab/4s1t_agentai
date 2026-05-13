"""
KB scheduling — ingestion job.

Sync wrappers for ingest_all_accounts and enrich_discovery_candidates,
plus async dispatch functions that run them in a thread-pool executor.

The run_in_executor pattern (KB-9 fix) is preserved verbatim here:
only one ChromaDB + adapter loads in memory at a time, which prevents
OOM spikes on memory-constrained hosts.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
from typing import Optional

from core.exceptions import IngestionError
from utils.logger import setup_logger

logger = setup_logger(__name__)


# ── Sync helpers (run in executor) ───────────────────────────────────────────

def _run_ingestion_sync(user_id: str, domain: Optional[str], db_path: str) -> int:
    """
    Synchronous wrapper calling ingest_all_accounts().
    Returns number of chunks stored.
    """
    from kb.ingestion.ingestion_runner import ingest_all_accounts
    api_key = os.environ.get("NANO_GPT_API_KEY", "")
    try:
        results = ingest_all_accounts(
            user_id=user_id,
            domain=domain,
            ingestion_type="scheduled",
            max_items_per_platform=5,
            db_path=db_path,
            api_key=api_key,
        )
        return sum(r.chunks_stored for r in results)
    except IngestionError:
        raise  # already typed — propagate to run_ingestion_for_domain
    except Exception as exc:
        raise IngestionError(
            f"Scheduled ingestion failed for user={user_id} domain={domain}: {exc}"
        ) from exc


def _run_enrichment_sync(user_id: str, db_path: str) -> int:
    """
    Synchronous wrapper for enrich_discovery_candidates().
    Runs in thread pool so it doesn't block the asyncio event loop.
    """
    from kb.web_research_discovery import enrich_discovery_candidates
    api_key = os.environ.get("NANO_GPT_API_KEY", "")
    try:
        return enrich_discovery_candidates(user_id=user_id, db_path=db_path, api_key=api_key)
    except Exception as exc:
        logger.error("Web research enrichment failed for user=%s: %s", user_id, exc)
        return 0


# ── Async dispatch functions ──────────────────────────────────────────────────

async def run_ingestion_for_domain(
    user_id: str,
    domain: Optional[str],
    db_path: str,
    executor: concurrent.futures.Executor,
) -> int:
    """
    Dispatch ingestion for (user_id, domain) in the thread-pool executor.

    Awaited (not fire-and-forget) so that:
    1. Only one ChromaDB + adapter loads in memory at a time (no OOM spikes).
    2. All ingestion is complete before delivery touches SQLite
       (avoids "database is locked" races that silently drop brief inserts).
    """
    loop = asyncio.get_event_loop()
    try:
        chunks = await loop.run_in_executor(
            executor,
            _run_ingestion_sync,
            user_id,
            domain,
            db_path,
        )
        return chunks
    except IngestionError as exc:
        logger.warning(
            "Domain ingestion failed — user=%s domain=%s: %s",
            user_id, domain or "all", exc,
        )
        return 0


async def run_enrichment_for_user(
    user_id: str,
    db_path: str,
    executor: concurrent.futures.Executor,
) -> None:
    """
    Run web-research handle enrichment for pending discovery candidates
    that have reached PROMOTE_THRESHOLD but have no platform handles yet.

    Phase KB-4 (G25).
    """
    try:
        loop = asyncio.get_event_loop()
        count = await loop.run_in_executor(
            executor,
            _run_enrichment_sync,
            user_id,
            db_path,
        )
        if count:
            logger.info(
                "Web-research enriched %d discovery candidates for user=%s", count, user_id
            )
    except Exception as exc:
        logger.warning("Discovery enrichment failed for user=%s: %s", user_id, exc)
