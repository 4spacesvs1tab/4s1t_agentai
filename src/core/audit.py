"""
Unified append-only AuditLog for 4S1T Agent AI.

Writes structured events to a dedicated SQLite table (audit_log) via an
async queue + background writer, so callers never block waiting for disk I/O.

Lifecycle::

    log = AuditLog("sqlite:///4s1t_agent.db")
    await log.start()      # called from lifespan startup
    await log.log("SKILL_CALL", actor="ba_agent", target="web_search")
    await log.stop()       # called from lifespan shutdown
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

class AuditEventType:
    AGENT_SPAWN    = "AGENT_SPAWN"
    SKILL_CALL     = "SKILL_CALL"
    AGENT_ERROR    = "AGENT_ERROR"
    WORKFLOW_START = "WORKFLOW_START"
    WAVE_COMPLETE  = "WAVE_COMPLETE"
    WORKFLOW_END   = "WORKFLOW_END"
    AUTH_SUCCESS   = "AUTH_SUCCESS"
    AUTH_FAILURE   = "AUTH_FAILURE"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT    NOT NULL,
    actor      TEXT,
    target     TEXT,
    metadata   TEXT,
    created_at TEXT    NOT NULL
)
"""

_INSERT_ROW = """
INSERT INTO audit_log (event_type, actor, target, metadata, created_at)
VALUES (?, ?, ?, ?, ?)
"""

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DRAIN_BATCH  = 50       # rows written per drain cycle
_DRAIN_SLEEP  = 0.5      # seconds between drain cycles when idle


def _open_db(db_url: str) -> sqlite3.Connection:
    """Open a raw SQLite connection (WAL mode) from a sqlite:/// URL."""
    if not db_url.startswith("sqlite:///"):
        raise ValueError(f"audit_log only supports SQLite, got: {db_url!r}")
    path = db_url[len("sqlite:///"):]
    if ":memory:" not in path.lower():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

class AuditLog:
    """
    Append-only structured event log backed by an SQLite table.

    The background writer drains the async queue in batches to avoid one
    slow disk write blocking multiple concurrent agent operations.
    """

    def __init__(self, db_url: str | None = None) -> None:
        if db_url is None:
            from config.settings import settings
            db_url = settings.DATABASE_URL
        self._db_url = db_url
        self._queue: asyncio.Queue[tuple | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the DB, create the table, and start the background writer."""
        self._conn = _open_db(self._db_url)
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()
        self._task = asyncio.create_task(self._writer_loop(), name="audit-log-writer")
        logger.info(f"AuditLog started → {self._db_url}")

    async def stop(self) -> None:
        """Flush the remaining queue entries and stop the background writer."""
        if self._task is None:
            return
        await self._queue.put(None)          # sentinel: stop the loop
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("AuditLog writer did not stop cleanly within 10s")
        finally:
            if self._conn:
                self._conn.close()
                self._conn = None
        logger.info("AuditLog stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def log(
        self,
        event_type: str,
        actor: str | None = None,
        target: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Enqueue an audit event for asynchronous write.

        Never blocks: drops events only if the internal queue is full (unlikely
        at the 0-bounded asyncio.Queue default).
        """
        ts = datetime.now(timezone.utc).isoformat()
        meta_str = json.dumps(metadata, default=str) if metadata else None
        row = (event_type, actor, target, meta_str, ts)
        await self._queue.put(row)

    # ------------------------------------------------------------------
    # Background writer
    # ------------------------------------------------------------------

    async def _writer_loop(self) -> None:
        """Drain the queue in batches until the sentinel None is received."""
        while True:
            batch: list[tuple] = []
            try:
                # Block until at least one item is available
                first = await self._queue.get()
                if first is None:
                    # Drain remaining items before exiting
                    while not self._queue.empty():
                        item = self._queue.get_nowait()
                        if item is not None:
                            batch.append(item)
                    if batch:
                        self._write_batch(batch)
                    return

                batch.append(first)

                # Collect up to _DRAIN_BATCH - 1 more items without blocking
                while len(batch) < _DRAIN_BATCH and not self._queue.empty():
                    item = self._queue.get_nowait()
                    if item is None:
                        self._write_batch(batch)
                        return
                    batch.append(item)

                self._write_batch(batch)

            except Exception as exc:
                logger.error(f"AuditLog writer error: {exc}", exc_info=True)

            # Brief idle sleep to avoid spinning when queue is empty
            await asyncio.sleep(_DRAIN_SLEEP)

    def _write_batch(self, rows: list[tuple]) -> None:
        """Write a batch of rows to SQLite synchronously."""
        if not self._conn or not rows:
            return
        try:
            self._conn.executemany(_INSERT_ROW, rows)
            self._conn.commit()
            logger.debug(f"AuditLog: wrote {len(rows)} row(s)")
        except Exception as exc:
            logger.error(f"AuditLog: failed to write batch: {exc}", exc_info=True)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_audit_log: AuditLog | None = None


def get_audit_log(db_url: str | None = None) -> AuditLog:
    """Return the shared AuditLog singleton (must call start() before use)."""
    global _audit_log
    if _audit_log is None:
        _audit_log = AuditLog(db_url)
    return _audit_log
