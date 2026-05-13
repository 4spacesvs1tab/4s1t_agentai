"""
KB scheduling — domain config helpers.

Module-level constants and plain functions for loading user domain
configuration from the database.  No class state; each function
receives db_path as an explicit argument.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from config.agent_config import get_agent_config as _get_agent_config
from utils.logger import setup_logger

logger = setup_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_STARTUP_DELAY_S: int = _get_agent_config().kb.scheduler.startup_delay_seconds

# How often the scheduler wakes to check if any domain is due
_CHECK_INTERVAL_S = 3600  # 1 hour

# How often to re-read kb_user_config from DB (to pick up changes without restart)
_CONFIG_REFRESH_INTERVAL_S = 1800  # 30 minutes

# How often to ingest content from each domain (independent of brief frequency)
_INGEST_INTERVAL_S = 3600  # 1 hour

# Frequency string → seconds (used only for brief generation scheduling)
_FREQ_TO_SECONDS = {
    "daily": 86_400,
    "weekly": 604_800,
    "disabled": 0,
}


# ── Helper functions ──────────────────────────────────────────────────────────

def _brief_send_hour_utc() -> int:
    """Return the UTC hour at which daily briefs are generated and delivered."""
    from config.kb_config import get_brief_hour_utc
    return get_brief_hour_utc()


def _load_user_domains(db_path: str) -> list[dict]:
    """
    Load distinct (user_id, domain, brief_frequency, brief_enabled) rows
    from kb_user_config.

    Returns empty list if table does not exist yet.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT user_id, domain, brief_frequency, brief_enabled
            FROM kb_user_config
            WHERE brief_enabled = 1
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:
        logger.debug("kb_user_config not available yet: %s", exc)
        return []


def _load_active_users_from_accounts(db_path: str) -> list[str]:
    """
    Fallback: if kb_user_config has no rows, find user_ids with active accounts.
    """
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT DISTINCT user_id FROM kb_accounts WHERE active = 1"
        )
        users = [r[0] for r in cur.fetchall()]
        conn.close()
        return users
    except Exception:
        return []


def _get_last_run(db_path: str, user_id: str, domain: str) -> Optional[datetime]:
    """
    Return the last time ingestion ran for (user_id, domain).

    Queries kb_ingestion_cursors, but only considers accounts whose *sole*
    domain matches exactly — i.e. accounts whose `domains` column equals the
    target domain.  Accounts that span multiple domains (e.g. "domain_a|domain_b")
    are excluded so that a cross-domain account being ingested during one
    domain's tick does not falsely mark the other domain as "just run".

    NOTE: this function is kept for use outside the scheduler (e.g. API stats).
    The scheduler itself uses KBScheduler._last_domain_run (in-memory dict) so
    that per-domain timing is completely independent of the cursors table.
    """
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            """
            SELECT MAX(c.last_ingested_at)
            FROM kb_ingestion_cursors c
            JOIN kb_accounts a ON c.account_id = a.id
            WHERE c.user_id = ? AND a.domains = ?
            """,
            (user_id, domain),
        )
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    except Exception:
        pass
    return None
