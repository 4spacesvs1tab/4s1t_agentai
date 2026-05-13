"""
KB Brief Configuration Service — Phase KB-2 (G22, G26).

Reads and writes per-user, per-domain brief preferences from kb_user_config.
Provides default values when no configuration row exists.

G22: Users can configure which domains generate briefs, how often,
     and at what time of day.

G26: brief_min_items and brief_extend_factor control the empty-state
     window extension behaviour in kb_monitor_agent.

Usage::

    from kb.brief_config import BriefConfigService, BriefConfig
    svc = BriefConfigService()

    # Read config for one domain
    cfg = svc.get(user_id="default", domain="macroeconomics")
    print(cfg.brief_frequency)  # "daily"

    # Update frequency
    svc.update(user_id="default", domain="macroeconomics", brief_frequency="weekly")

    # Initialise default rows for all domains defined in kb_domains.yaml
    svc.init_defaults_for_user("default")
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)


# Default brief settings applied when no config row exists (G26)
_DEFAULT_BRIEF_CONFIG = {
    "brief_enabled": 1,
    "brief_frequency": "daily",
    "brief_time": "08:00",
    "brief_min_items": 3,
    "brief_extend_factor": 2,
}

def _get_all_domains() -> list[str]:
    """Return domain IDs from kb_domains.yaml via kb_config (no hardcoded list)."""
    try:
        from config.kb_config import get_domain_ids
        return get_domain_ids()
    except Exception as exc:
        logger.warning("Could not load domain list from kb_config: %s", exc)
        return []


@dataclass
class BriefConfig:
    user_id: str
    domain: str
    brief_enabled: bool
    brief_frequency: str   # "daily" | "weekly" | "custom" | "disabled"
    brief_time: str        # "HH:MM" UTC
    brief_min_items: int   # G26: minimum results before generating brief
    brief_extend_factor: int  # G26: multiply window by this if below min
    brief_days: list = None  # JSON-decoded list of day abbrevs, e.g. ["mon","wed","fri"]; None = all days

    def __post_init__(self):
        if self.brief_days is None:
            self.brief_days = []


class BriefConfigService:
    """CRUD service for kb_user_config (brief preferences)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or str(get_db_path())

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, user_id: str, domain: str) -> BriefConfig:
        """
        Return the BriefConfig for (user_id, domain).

        If no row exists, returns a BriefConfig with default values.
        Does NOT create the row — call ensure_exists() or init_defaults_for_user()
        to persist defaults.
        """
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT brief_enabled, brief_frequency, brief_time,
                       brief_min_items, brief_extend_factor, brief_days
                FROM kb_user_config
                WHERE user_id = ? AND domain = ?
                """,
                (user_id, domain),
            )
            row = cur.fetchone()
            conn.close()
            if row:
                import json as _json
                raw_days = row["brief_days"]
                brief_days = _json.loads(raw_days) if raw_days else []
                return BriefConfig(
                    user_id=user_id,
                    domain=domain,
                    brief_enabled=bool(row["brief_enabled"]),
                    brief_frequency=row["brief_frequency"],
                    brief_time=row["brief_time"],
                    brief_min_items=row["brief_min_items"],
                    brief_extend_factor=row["brief_extend_factor"],
                    brief_days=brief_days,
                )
        except Exception as exc:
            logger.debug("Could not read kb_user_config for %s/%s: %s", user_id, domain, exc)

        # Return defaults
        d = _DEFAULT_BRIEF_CONFIG
        return BriefConfig(
            user_id=user_id,
            domain=domain,
            brief_enabled=bool(d["brief_enabled"]),
            brief_frequency=d["brief_frequency"],
            brief_time=d["brief_time"],
            brief_min_items=d["brief_min_items"],
            brief_extend_factor=d["brief_extend_factor"],
        )

    def get_all(self, user_id: str) -> list[BriefConfig]:
        """Return BriefConfig for all domains for *user_id*."""
        return [self.get(user_id, domain) for domain in _get_all_domains()]

    def get_enabled(self, user_id: str) -> list[BriefConfig]:
        """Return only enabled (brief_enabled=True, frequency!='disabled') configs."""
        return [
            c for c in self.get_all(user_id)
            if c.brief_enabled and c.brief_frequency != "disabled"
        ]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def update(self, user_id: str, domain: str, **kwargs) -> None:
        """
        Create or update a kb_user_config row for (user_id, domain).

        Accepted kwargs: brief_enabled, brief_frequency, brief_time,
                         brief_min_items, brief_extend_factor.
        Unknown keys are ignored.
        """
        allowed = {"brief_enabled", "brief_frequency", "brief_time", "brief_days",
                   "brief_min_items", "brief_extend_factor"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        # Build upsert
        columns = list(updates.keys())
        values = list(updates.values())

        try:
            conn = sqlite3.connect(self._db_path)
            # Ensure row exists with defaults first
            conn.execute(
                """
                INSERT OR IGNORE INTO kb_user_config
                    (user_id, domain, brief_enabled, brief_frequency, brief_time,
                     brief_min_items, brief_extend_factor)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, domain,
                    _DEFAULT_BRIEF_CONFIG["brief_enabled"],
                    _DEFAULT_BRIEF_CONFIG["brief_frequency"],
                    _DEFAULT_BRIEF_CONFIG["brief_time"],
                    _DEFAULT_BRIEF_CONFIG["brief_min_items"],
                    _DEFAULT_BRIEF_CONFIG["brief_extend_factor"],
                ),
            )
            # Apply requested updates
            set_clause = ", ".join(f"{col} = ?" for col in columns)
            conn.execute(
                f"UPDATE kb_user_config SET {set_clause} WHERE user_id = ? AND domain = ?",
                values + [user_id, domain],
            )
            conn.commit()
            conn.close()
            logger.info("Updated brief config for user=%s domain=%s: %s", user_id, domain, updates)
        except Exception as exc:
            logger.error("Failed to update brief config for %s/%s: %s", user_id, domain, exc)

    def init_defaults_for_user(self, user_id: str) -> None:
        """
        Ensure all known domains have a kb_user_config row for *user_id*.

        Uses INSERT OR IGNORE so existing customisations are preserved.
        """
        try:
            conn = sqlite3.connect(self._db_path)
            all_domains = _get_all_domains()
            for domain in all_domains:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO kb_user_config
                        (user_id, domain, brief_enabled, brief_frequency, brief_time,
                         brief_min_items, brief_extend_factor)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id, domain,
                        _DEFAULT_BRIEF_CONFIG["brief_enabled"],
                        _DEFAULT_BRIEF_CONFIG["brief_frequency"],
                        _DEFAULT_BRIEF_CONFIG["brief_time"],
                        _DEFAULT_BRIEF_CONFIG["brief_min_items"],
                        _DEFAULT_BRIEF_CONFIG["brief_extend_factor"],
                    ),
                )
            conn.commit()
            conn.close()
            logger.info("Initialised default brief config for user=%s (%d domains)", user_id, len(all_domains))
        except Exception as exc:
            logger.error("Failed to init brief config for user=%s: %s", user_id, exc)

    # ------------------------------------------------------------------
    # Helpers used by kb_monitor_agent prompt / scheduler
    # ------------------------------------------------------------------

    def get_window_extension(self, user_id: str, domain: str) -> tuple[int, int]:
        """
        Return (brief_min_items, brief_extend_factor) for (user_id, domain).

        Used by kb_monitor_agent to determine when to extend the search window.
        """
        cfg = self.get(user_id, domain)
        return cfg.brief_min_items, cfg.brief_extend_factor


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: BriefConfigService | None = None


def get_brief_config_service(db_path: Optional[str] = None) -> BriefConfigService:
    """Return the shared BriefConfigService singleton."""
    global _service
    if _service is None:
        _service = BriefConfigService(db_path)
    return _service
