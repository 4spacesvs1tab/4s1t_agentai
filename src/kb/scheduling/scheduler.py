"""
KB Ingestion Scheduler — coordinator module.

KBScheduler owns the asyncio event loop integration, the per-(user_id, domain)
in-memory last-run dict, and the tick/sleep cycle.  It delegates all job logic
to the job modules in this package:

  ingestion_job  → run_ingestion_for_domain, run_enrichment_for_user
  brief_job      → generate_briefs_for_user
  delivery_job   → dispatch_delivery
  maintenance_job → run_proactive, run_action_item_extraction,
                    run_prediction_verification, run_cross_domain_insights,
                    run_knowledge_diff, cleanup_expired_messages,
                    cleanup_expired_revoked_tokens
  domain_config  → _load_user_domains, _load_active_users_from_accounts,
                   _brief_send_hour_utc, constants

Usage (from main.py startup)::

    from kb.scheduler import KBScheduler
    scheduler = KBScheduler(brief_port=_brief_port)
    asyncio.create_task(scheduler.run_forever())

Shutdown: call scheduler.stop() — sets the stop event and the task exits.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import datetime, timezone
from typing import Optional

from core.db_path import get_db_path
from utils.logger import setup_logger

from kb.scheduling.domain_config import (
    _STARTUP_DELAY_S,
    _CONFIG_REFRESH_INTERVAL_S,
    _INGEST_INTERVAL_S,
    _FREQ_TO_SECONDS,
    _brief_send_hour_utc,
    _load_user_domains,
    _load_active_users_from_accounts,
)
from kb.scheduling.ingestion_job import run_ingestion_for_domain, run_enrichment_for_user
from kb.scheduling.brief_job import generate_briefs_for_user
from kb.scheduling.delivery_job import dispatch_delivery
from kb.scheduling.maintenance_job import (
    run_proactive,
    run_action_item_extraction,
    run_prediction_verification,
    run_cross_domain_insights,
    run_knowledge_diff,
    cleanup_expired_messages,
    cleanup_expired_revoked_tokens,
)

logger = setup_logger(__name__)


class KBScheduler:
    """
    Asyncio-compatible KB ingestion scheduler.

    Checks every hour whether any (user_id, domain) pair is due for
    ingestion, and if so, runs the full KB pipeline tick:
      ingestion → web research enrichment → brief generation → NIP-17 delivery.

    Parameters
    ----------
    db_path :
        Path to the SQLite database. Defaults to data/agent.db.
    brief_port :
        BriefGenerationPort implementation (E5).  The scheduler calls
        brief_port.generate_domain_brief() per domain after each tick.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        brief_port=None,
        executor: Optional[concurrent.futures.ThreadPoolExecutor] = None,
    ) -> None:
        self._db_path = db_path or str(get_db_path())
        self._brief_port = brief_port
        self._stop_event = asyncio.Event()
        # If executor is None, create one internally (backward-compatible default).
        # In tests, inject a controlled executor (e.g. a single-thread pool or mock)
        # to avoid spawning real threads.  Production always relies on the default.
        self._executor = executor or concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="kb-ingest"
        )
        self._config_cache: list[dict] = []
        self._config_loaded_at: Optional[datetime] = None
        # Per-(user_id, domain) last ingestion time tracked in memory.
        # Using in-memory tracking (rather than querying kb_ingestion_cursors)
        # prevents cross-domain accounts from blocking domain ingestion:
        # e.g. an account spanning domains="domain_a|domain_b" being ingested
        # during domain_a's tick would otherwise advance domain_b's apparent
        # last-run time via the cursor JOIN query, causing domain_b to be
        # skipped on every subsequent tick.
        # On container restart all domains are immediately due — correct
        # behaviour, and dedup in ChromaDB prevents duplicate chunks.
        self._last_domain_run: dict[tuple[str, str], datetime] = {}

    def stop(self) -> None:
        """Signal the scheduler to stop after the current sleep cycle."""
        self._stop_event.set()

    async def run_forever(self) -> None:
        """
        Main scheduler loop. Run as an asyncio background task.

        On each tick:
          1. (Re-)load user domain config from kb_user_config.
          2. For each (user_id, domain) with brief_frequency != 'disabled',
             check if the next run is due.
          3. If due, dispatch ingestion in the thread pool.
        """
        logger.info("KB scheduler started — ticking at the top of each UTC hour")

        # Wait for the rest of the application (Nostr service, etc.) to finish
        # starting up before the first tick. Without this delay the scheduler's
        # first delivery attempt may fire before the Nostr service is ready,
        # causing NIP-17 delivery to fail silently on startup.
        await asyncio.sleep(_STARTUP_DELAY_S)

        while not self._stop_event.is_set():
            await self._tick()
            # Sleep until the top of the next UTC hour so that the 08:00 UTC
            # brief-generation tick always fires at 08:00 regardless of when
            # the container was last restarted.
            now = datetime.now(timezone.utc)
            seconds_to_next_hour = 3600 - (now.minute * 60 + now.second)
            logger.debug(
                "KB scheduler sleeping %ds until next top-of-hour tick",
                seconds_to_next_hour,
            )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=seconds_to_next_hour,
                )
            except asyncio.TimeoutError:
                pass  # Normal — woke up at the top of the next hour

        logger.info("KB scheduler stopped")
        self._executor.shutdown(wait=False)

    async def _tick(self) -> None:
        """Single scheduler tick: evaluate and dispatch due jobs."""
        now = datetime.now(timezone.utc)

        # Refresh config periodically.
        # _config_loaded_at is only set when we successfully obtain a non-empty
        # config.  On failure (e.g., DB locked at startup) the cache stays empty
        # and we retry on the very next tick instead of waiting 30 minutes.
        if (
            self._config_loaded_at is None
            or (now - self._config_loaded_at).total_seconds() > _CONFIG_REFRESH_INTERVAL_S
        ):
            self._config_cache = _load_user_domains(self._db_path)

            # If kb_user_config is empty, fall back to running daily for all active users
            if not self._config_cache:
                users = _load_active_users_from_accounts(self._db_path)
                self._config_cache = [
                    {
                        "user_id": u,
                        "domain": None,
                        "brief_frequency": "daily",
                        "brief_enabled": 1,
                    }
                    for u in users
                ]

            # Only stamp the load time when we actually got usable config so
            # that a startup DB-lock failure retries next tick, not in 30 min.
            if self._config_cache:
                self._config_loaded_at = now

        dispatched_users: set[str] = set()
        all_active_users: set[str] = set()

        for cfg in self._config_cache:
            user_id = cfg["user_id"]
            all_active_users.add(user_id)
            domain = cfg.get("domain")  # None means "all domains"
            freq = cfg.get("brief_frequency", "daily")
            if _FREQ_TO_SECONDS.get(freq, 0) == 0:
                continue  # disabled — skip ingestion too

            # Ingestion runs on its own hourly cadence, independent of brief frequency.
            # Use in-memory last-run time (not the cursors table) so that
            # cross-domain accounts cannot block a domain from being dispatched.
            domain_key = (user_id, domain or "")
            last_run = self._last_domain_run.get(domain_key)
            if last_run is None or (now - last_run).total_seconds() >= _INGEST_INTERVAL_S:
                logger.info(
                    "KB scheduler: ingesting user=%s domain=%s freq=%s (sequential)",
                    user_id, domain or "all", freq,
                )
                # Record before dispatch so a crash/exception doesn't cause an
                # immediate retry loop; the domain will be retried next hour.
                self._last_domain_run[domain_key] = now
                # Run sequentially (awaited) — not fire-and-forget — so that:
                # 1. Only one ChromaDB + adapter loads in memory at a time (no OOM spikes).
                # 2. All ingestion is complete before delivery touches SQLite
                #    (avoids "database is locked" races that silently drop brief inserts).
                chunks = await run_ingestion_for_domain(
                    user_id, domain, self._db_path, self._executor
                )
                logger.info(
                    "KB scheduler: ingestion done user=%s domain=%s chunks=%d",
                    user_id, domain or "all", chunks,
                )
                dispatched_users.add(user_id)

        # Phase KB-4: after ingestion, run web-research enrichment for each user
        for user_id in dispatched_users:
            await run_enrichment_for_user(user_id, self._db_path, self._executor)

        # Phase KB-4: generate domain briefs via kb_monitor_agent — only after brief hour UTC.
        # Uses all_active_users (not dispatched_users) so that brief generation is
        # independent of whether ingestion happened this tick.  Ingestion runs hourly
        # but _brief_send_hour_utc() may not align with the ingestion tick, so
        # dispatched_users would be empty on the intended generation tick.
        # generate_briefs_for_user skips domains whose brief files already exist.
        if now.hour >= _brief_send_hour_utc():
            for user_id in all_active_users:
                await generate_briefs_for_user(user_id, self._db_path, self._brief_port)

        # Phase KB-3/KB-4: deliver pending briefs and alerts via NIP-17
        # Run for ALL active users on every tick (not just when ingestion ran),
        # so manually written brief files are picked up and delivered promptly.
        for user_id in all_active_users:
            await dispatch_delivery(user_id, self._db_path)

        # Phase KB-15: weekly prediction verification (runs on Mondays)
        if now.weekday() == 0:  # Monday = 0
            for user_id in all_active_users:
                await run_prediction_verification(user_id, self._db_path, self._executor)

        # Phase KB-20: weekly cross-domain insights + knowledge diff (Mondays)
        if now.weekday() == 0:
            for user_id in all_active_users:
                await run_cross_domain_insights(user_id, self._db_path, self._executor)
                await run_knowledge_diff(user_id, self._db_path, self._executor)

        # Phase KB-13: proactive assistant — deliver due reminders + task nudges
        for user_id in all_active_users:
            await run_proactive(user_id, self._db_path)

        # Phase KB-17: nightly action item extraction (runs once per day after brief hour)
        if now.hour >= _brief_send_hour_utc():
            for user_id in all_active_users:
                await run_action_item_extraction(user_id, self._db_path, self._executor)

        # Phase KB-25-H: daily cleanup of expired conversation messages
        await cleanup_expired_messages(self._db_path, self._executor)

        # Phase KB-26-F: daily cleanup of expired revoked JWT tokens
        await cleanup_expired_revoked_tokens(self._db_path, self._executor)
