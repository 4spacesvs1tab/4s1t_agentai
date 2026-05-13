"""
KB Brief Dispatcher — Phase KB-3.

Two delivery responsibilities:

1. Brief delivery — after kb_monitor_agent writes a brief file to
   data/briefs/{domain}_{date}.md, this module:
     a) Scans the briefs directory for files not yet recorded in kb_briefs.
     b) Inserts a row in kb_briefs (delivered=0).
     c) Sends the brief content as a NIP-17 DM via NostrCommunicationService.
     d) Marks the row delivered=1 in kb_briefs.

2. Alert delivery — reads pending rows from kb_alert_matches and sends
   each alert notification as a NIP-17 DM, then marks it delivered.

Both deliveries are triggered by the KBScheduler after each ingestion tick
(see kb/scheduler.py). If the NIP-17 service is unavailable, delivery is
skipped silently and retried on the next tick.

Design reference: KnowledgeBase_design.md §6.8 (brief delivery), §6.7 (alerts)
"""
from __future__ import annotations

import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from kb.alert_engine import get_pending_matches, mark_matches_delivered

from utils.logger import setup_logger
logger = setup_logger(__name__)

_DEFAULT_BRIEFS_DIR = str(Path(__file__).resolve().parent.parent.parent / "data" / "briefs")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _send_dm(text: str) -> bool:
    """
    Send *text* as a NIP-17 DM to the configured recipient.

    Delegates to NostrNIP17DeliveryAdapter (C1) for the actual transport.
    Kept as a module-level coroutine for backward compatibility with call
    sites inside this module and in maintenance_job.py (scheduled for removal
    once callers import the adapter directly).
    """
    from communication.nostr_nip17.delivery_adapter import get_delivery_port
    return await get_delivery_port().send(text)


def _brief_already_recorded(db_path: str, filename: str) -> bool:
    """Return True if a kb_briefs row for this file already exists."""
    domain, date_str, _ = _parse_brief_filename(filename)
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT 1 FROM kb_briefs WHERE domain = ? AND DATE(window_end) = ? LIMIT 1",
            (domain, date_str),
        )
        found = cur.fetchone() is not None
        conn.close()
        return found
    except Exception:
        return False


def _insert_brief_row(
    db_path: str,
    user_id: str,
    domain: str,
    frequency: str,
    content: str,
    window_start: str,
    window_end: str,
    extended_window: bool,
) -> str:
    """Insert a kb_briefs row and return the new brief_id."""
    brief_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
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
    conn.close()
    return brief_id


def _mark_brief_delivered(db_path: str, brief_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE kb_briefs SET delivered = 1, generated_at = ? WHERE id = ?",
            (now, brief_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("mark_brief_delivered failed: %s", exc)


def _parse_brief_filename(filename: str) -> tuple[str, str, str]:
    """
    Parse a brief filename like 'macroeconomics_2026-03-05.md'.

    Returns (domain, date_str, frequency).
    frequency defaults to 'daily' unless the filename contains 'weekly'.
    """
    stem = Path(filename).stem  # e.g. 'macroeconomics_2026-03-05'
    parts = stem.split("_")
    if len(parts) >= 2:
        domain = parts[0]
        date_str = parts[1]
    else:
        domain = stem
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    frequency = "weekly" if "weekly" in stem else "daily"
    return domain, date_str, frequency


def _get_undelivered_briefs(db_path: str, user_id: str) -> list[dict]:
    """Return kb_briefs rows with delivered=0 for *user_id*."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM kb_briefs WHERE user_id = ? AND delivered = 0 ORDER BY generated_at ASC",
            (user_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.warning("get_undelivered_briefs failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def ingest_brief_files(
    user_id: str = "default",
    briefs_dir: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """
    Scan the briefs directory for new .md files and record them in kb_briefs.

    Does NOT send NIP-17 yet — call `deliver_pending_briefs()` afterwards.
    Returns the number of new brief rows inserted.
    """
    db = db_path or str(get_db_path())
    bdir = Path(briefs_dir or _DEFAULT_BRIEFS_DIR)
    if not bdir.exists():
        return 0

    inserted = 0
    for brief_file in sorted(bdir.glob("*.md")):
        filename = brief_file.name
        if _brief_already_recorded(db, filename):
            continue

        try:
            content = brief_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Cannot read brief file %s: %s", brief_file, exc)
            continue

        domain, date_str, frequency = _parse_brief_filename(filename)
        extended = "extended window" in content.lower()

        # Approximate window bounds from the date_str
        try:
            window_end_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            window_end_dt = datetime.now(timezone.utc)
        window_end = window_end_dt.isoformat()
        window_start = window_end  # exact window unknown from file; use same

        # Retry up to 3 times on SQLite lock (WAL contention from SQLAlchemy pool).
        for attempt in range(3):
            try:
                _insert_brief_row(
                    db, user_id, domain, frequency, content,
                    window_start, window_end, extended,
                )
                inserted += 1
                logger.info("Recorded brief file: %s (domain=%s)", filename, domain)
                break
            except Exception as exc:
                if attempt < 2:
                    import time as _time
                    logger.warning(
                        "Brief insert attempt %d failed (domain=%s): %s — retrying in 3s",
                        attempt + 1, domain, exc,
                    )
                    _time.sleep(3)
                else:
                    logger.error(
                        "Brief insert failed after 3 attempts (domain=%s): %s",
                        domain, exc,
                    )

    return inserted


async def deliver_pending_briefs(
    user_id: str = "default",
    db_path: Optional[str] = None,
) -> int:
    """
    Send all undelivered briefs for *user_id* via NIP-17 DM.

    Returns the number of briefs successfully delivered.
    """
    db = db_path or str(get_db_path())
    briefs = _get_undelivered_briefs(db, user_id)
    delivered = 0

    try:
        from config.kb_config import get_nip17_send_domains
        nip17_domains = get_nip17_send_domains()
    except Exception:
        nip17_domains = None  # If config unavailable, send all

    for brief in briefs:
        domain = brief.get("domain", "unknown")
        content = brief.get("content", "")
        brief_id = brief["id"]

        # Skip NIP-17 send for domains configured with nip17_send: false.
        # Mark as delivered so the brief isn't retried on every tick.
        if nip17_domains is not None and domain not in nip17_domains:
            _mark_brief_delivered(db, brief_id)
            delivered += 1
            logger.info("Brief %s (domain=%s) skipped NIP-17 (nip17_send=false) — marked delivered", brief_id, domain)
            continue

        header = f"[4S1T KB Brief — {domain}]\n\n"
        message = header + content

        ok = await _send_dm(message)
        if ok:
            _mark_brief_delivered(db, brief_id)
            delivered += 1
            logger.info("Delivered brief %s (domain=%s) via NIP-17", brief_id, domain)
        else:
            logger.debug("Brief %s not delivered (NIP-17 unavailable)", brief_id)

    return delivered


async def deliver_pending_alerts(
    user_id: str = "default",
    db_path: Optional[str] = None,
) -> int:
    """
    Send undelivered alert matches for *user_id* via NIP-17 DM.

    Groups all pending matches into a single digest message.
    Returns the number of matches delivered.
    """
    db = db_path or str(get_db_path())
    matches = get_pending_matches(db, user_id)
    if not matches:
        return 0

    # Build digest
    lines = ["[4S1T KB Alert]\n"]
    for m in matches:
        query = m.get("query", "?")
        source_url = m.get("source_url", "")
        domain = m.get("domain", "")
        sim = m.get("similarity", 0.0)
        lines.append(
            f"• Alert: {query!r}\n"
            f"  Match: {source_url or '(no URL)'} [domain={domain}, sim={sim:.2f}]"
        )

    message = "\n".join(lines)
    ok = await _send_dm(message)
    if ok:
        match_ids = [m["id"] for m in matches]
        mark_matches_delivered(db, match_ids)
        logger.info("Delivered %d alert matches via NIP-17 for user=%s", len(matches), user_id)
        return len(matches)
    else:
        logger.debug("Alert delivery skipped (NIP-17 unavailable) for user=%s", user_id)
        return 0


async def run_dispatch(
    user_id: str = "default",
    db_path: Optional[str] = None,
    briefs_dir: Optional[str] = None,
) -> dict:
    """
    Full dispatch cycle: ingest new brief files, deliver briefs, deliver alerts.

    Called by the scheduler after each ingestion tick.
    Returns a summary dict.
    """
    db = db_path or str(get_db_path())
    new_briefs = await ingest_brief_files(user_id=user_id, briefs_dir=briefs_dir, db_path=db)
    delivered_briefs = await deliver_pending_briefs(user_id=user_id, db_path=db)
    delivered_alerts = await deliver_pending_alerts(user_id=user_id, db_path=db)

    return {
        "new_brief_files": new_briefs,
        "delivered_briefs": delivered_briefs,
        "delivered_alerts": delivered_alerts,
    }
