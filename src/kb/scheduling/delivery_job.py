"""
KB scheduling — brief/alert delivery job.

Runs brief file ingestion and NIP-17 dispatch for a given user via
brief_dispatcher.run_dispatch().

Phase KB-3/KB-4.
"""
from __future__ import annotations

import asyncio

from utils.logger import setup_logger

logger = setup_logger(__name__)


async def dispatch_delivery(user_id: str, db_path: str) -> None:
    """
    Run brief file ingestion and NIP-17 delivery for *user_id*.

    Called after each ingestion tick so that:
      - New brief files written by kb_monitor_agent are recorded in kb_briefs.
      - Pending briefs and alert matches are sent via NIP-17 DM.

    The 5-second sleep lets SQLAlchemy finish any pending WAL writes before
    brief_dispatcher opens its own raw sqlite3 connection.  Without this the
    INSERT in _insert_brief_row can hit a write lock (same fix applied to the
    /generate-briefs API endpoint; 2s was insufficient when audit_log writes
    overlap — increased to 5s).
    """
    try:
        from kb.brief_dispatcher import run_dispatch
        await asyncio.sleep(5)
        summary = await run_dispatch(user_id=user_id, db_path=db_path)
        if any(summary.values()):
            logger.info(
                "KB dispatch for user=%s: new_briefs=%d delivered_briefs=%d alerts=%d",
                user_id,
                summary.get("new_brief_files", 0),
                summary.get("delivered_briefs", 0),
                summary.get("delivered_alerts", 0),
            )
    except Exception as exc:
        logger.warning("Brief/alert dispatch failed for user=%s: %s", user_id, exc)
