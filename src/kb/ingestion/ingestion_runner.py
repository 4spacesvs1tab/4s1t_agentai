"""
KB Ingestion Runner — Phase KB-2.

Central dispatch for KB content ingestion. Selects the correct adapter
based on account platform aliases and drives the full pipeline:

  SocialGraph → aliases → adapter → RawFetchResult[] → KBPreprocessor → ChromaDB

Two ingestion modes (G21):
  - Scheduled: called by the scheduler on a recurring basis; uses cursor
    (last ingestion time) to fetch only new content.
  - Manual: called ad-hoc for a specific account or domain; fetches full
    backlog up to *max_items*.

Cursor tracking (G18):
  - Last successful ingestion timestamp is stored per (user_id, account_id,
    platform) in kb_ingestion_cursors (migration 011).
  - On each run, get_new_since() is called with the stored cursor.
  - On first run (no cursor), full fetch is performed.

Design reference: KnowledgeBase_design.md §6.2 (adapter dispatch), §6.3 (pipeline)
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional, Union

from kb.ingestion.base_adapter import BaseIngestionAdapter, RawFetchResult
from kb.ingestion.website_adapter import WebsiteAdapter
from kb.ingestion.nitter_adapter import NitterAdapter
from kb.ingestion.youtube_adapter import YouTubeAdapter
from kb.ingestion.podcast_adapter import PodcastAdapter
from kb.ingestion.nostr_adapter import NostrAdapter
from kb.ingestion.rumble_adapter import RumbleAdapter
from kb.preprocessor import KBPreprocessor, RawContent
from kb.social_graph import get_social_graph, KBAccount

from utils.logger import setup_logger
logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Global ingestion lock
# ---------------------------------------------------------------------------
# Ensures scheduler and on-demand backfill never run concurrently.
# Both ingest_all_accounts() and backfill_account() acquire this lock before
# touching ChromaDB/adapters.  Since both are called from thread-pool workers
# (via run_in_executor), a threading.Lock is the right primitive.
_ingestion_lock = threading.Lock()

# On first run (no cursor), fetch content published within this many days.
# Prevents initial fetch from depending on the feed's own item count.
# Set to 2 for initial smoke-test. After confirming the pipeline works:
#   1. DELETE FROM kb_ingestion_cursors;
#   2. Set KB_INITIAL_LOOKBACK_DAYS=180 in docker-compose env (or edit this value)
#   3. Restart container — first run re-ingests with full history
_DEFAULT_INITIAL_LOOKBACK_DAYS = 2


def _initial_lookback_iso() -> str:
    """Return ISO 8601 UTC timestamp for the default initial lookback cutoff."""
    days = int(os.environ.get("KB_INITIAL_LOOKBACK_DAYS", _DEFAULT_INITIAL_LOOKBACK_DAYS))
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

class AdapterRegistry:
    """
    Registry mapping platform names to their ingestion adapter instances.

    Use AdapterRegistry.default() to obtain the standard registry containing
    all built-in adapters.  Inject a custom instance in tests to provide mock
    adapters without loading the real adapter classes or making network calls.

    Lazy construction: AdapterRegistry.default() is NOT called at module
    import time — only on first use.  This prevents loading all six adapter
    classes (and their transitive dependencies) when a test imports
    ingestion_runner before a real DB or network is available.

    When constructed from YAML (from_yaml()), all adapter classes are imported
    and instantiated eagerly — startup fails fast if adapters.yaml contains a
    bad class path, rather than surfacing the error mid-ingestion.
    """

    # Internal storage: values are already-instantiated adapters.
    # Union[..., str] is accepted by __init__ for backward-compat (e.g. tests
    # that pass strings directly); get() resolves strings lazily via
    # _resolve_class_path if they ever appear.
    def __init__(self, adapters: dict[str, Union[BaseIngestionAdapter, str]]) -> None:
        self._adapters: dict[str, Union[BaseIngestionAdapter, str]] = adapters

    def get(self, platform: str) -> Optional[BaseIngestionAdapter]:
        """Return the adapter for *platform* (case-insensitive), or None.

        Handles string values (dotted class paths) for backward-compat —
        resolves them on first call and caches the result.  Registries built
        via from_yaml() or _builtin_defaults() always contain live instances.
        """
        key = platform.lower()
        val = self._adapters.get(key)
        if val is None:
            return None
        if isinstance(val, str):
            val = self._resolve_class_path(val)
            self._adapters[key] = val
        return val  # type: ignore[return-value]

    @staticmethod
    def _resolve_class_path(class_path: str) -> BaseIngestionAdapter:
        """Import *class_path* (dotted module.ClassName), instantiate with no args.

        Raises ConfigError on any import or instantiation failure so that a
        misconfigured adapters.yaml fails loudly rather than silently falling
        back to a broken state.
        """
        from core.exceptions import ConfigError
        try:
            module_path, class_name = class_path.rsplit(".", 1)
            mod = importlib.import_module(module_path)
            cls_obj = getattr(mod, class_name)
            return cls_obj()
        except Exception as exc:
            raise ConfigError(f"Cannot load adapter class '{class_path}': {exc}") from exc

    @classmethod
    def from_yaml(cls, path: Path) -> "AdapterRegistry":
        """Load adapter registry from a YAML file.

        Each value must be a dotted class path: "module.ClassName".
        All adapter classes are imported and instantiated eagerly so that a
        misconfigured adapters.yaml fails loudly at startup rather than
        silently mid-ingestion.
        Raises ConfigError if the file cannot be parsed or any class path
        cannot be imported.
        """
        from config.loader import load_yaml
        from core.exceptions import ConfigError

        raw = load_yaml(path)
        adapters: dict[str, BaseIngestionAdapter] = {}
        for alias, class_path in raw.get("adapters", {}).items():
            try:
                module_path, class_name = class_path.rsplit(".", 1)
                mod = importlib.import_module(module_path)
                adapter_cls = getattr(mod, class_name)
                adapters[alias.lower()] = adapter_cls()
            except Exception as exc:
                raise ConfigError(
                    f"adapters.yaml: cannot load '{class_path}': {exc}"
                ) from exc
        return cls(adapters)

    @classmethod
    def _builtin_defaults(cls) -> "AdapterRegistry":
        """Return the built-in registry with all adapter classes pre-instantiated.

        This is the safety-net fallback used when src/config/adapters.yaml is
        missing or fails to load.  Never delete this method — it guarantees
        that all built-in platforms are always available regardless of operator
        configuration.
        """
        return cls({
            "website": WebsiteAdapter(),
            "blog": WebsiteAdapter(),
            "substack": WebsiteAdapter(),
            "wordpress": WebsiteAdapter(),
            "twitter": NitterAdapter(),
            "twitter2": NitterAdapter(),
            "nitter": NitterAdapter(),
            "youtube": YouTubeAdapter(),
            "podcast": PodcastAdapter(),
            "nostr": NostrAdapter(),
            "rumble": RumbleAdapter(),
        })

    @classmethod
    def default(cls) -> "AdapterRegistry":
        """Return the standard registry.

        Loads from src/config/adapters.yaml when the file exists (operator-
        configurable, eager class loading).  Falls back to _builtin_defaults()
        when the file is absent or fails to load — the application starts
        cleanly even without adapters.yaml.
        """
        yaml_path = Path(__file__).parents[2] / "config" / "adapters.yaml"
        if yaml_path.exists():
            try:
                logger.debug("AdapterRegistry: loading from %s", yaml_path)
                return cls.from_yaml(yaml_path)
            except Exception as exc:
                logger.warning(
                    "adapters.yaml failed to load (%s) — using built-in defaults", exc
                )
        else:
            logger.debug("AdapterRegistry: adapters.yaml not found — using built-in defaults")
        return cls._builtin_defaults()


# Lazy module-level default — only created on first call to get_adapter().
# Kept for backward-compat with callers that use the module-level function
# rather than injecting a registry.
_default_registry: Optional[AdapterRegistry] = None


def get_adapter(platform: str) -> Optional[BaseIngestionAdapter]:
    """Return the adapter instance for *platform*, or None if unsupported.

    Uses a lazily-initialised default AdapterRegistry so that importing
    this module does not instantiate all six adapter classes up front.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = AdapterRegistry.default()
    return _default_registry.get(platform)


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------

def _get_cursor(db_path: str, user_id: str, account_id: str, platform: str) -> Optional[str]:
    """
    Return the ISO 8601 timestamp of the last successful ingestion for this
    (user_id, account_id, platform) triple, or None if no cursor exists.
    """
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            """
            SELECT last_ingested_at FROM kb_ingestion_cursors
            WHERE user_id = ? AND account_id = ? AND platform = ?
            LIMIT 1
            """,
            (user_id, account_id, platform),
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _set_cursor(
    db_path: str,
    user_id: str,
    account_id: str,
    platform: str,
    ts: Optional[str] = None,
) -> None:
    """
    Update (or insert) the ingestion cursor.

    *ts* should be the ISO 8601 published_at timestamp of the latest item
    ingested.  If omitted (e.g. 0-item run), falls back to the current UTC
    time so the scheduler respects the configured frequency.

    Using the latest published_at rather than "now" prevents the cursor from
    advancing past content that was published before ingestion ran (e.g.
    episodes published at 07:00 UTC when ingestion runs at 12:00 UTC on the
    same day would otherwise be permanently skipped on the next run).
    """
    value = ts or datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO kb_ingestion_cursors (user_id, account_id, platform, last_ingested_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, account_id, platform)
            DO UPDATE SET last_ingested_at = excluded.last_ingested_at
            """,
            (user_id, account_id, platform, value),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to set cursor for %s/%s/%s: %s", user_id, account_id, platform, exc)


# ---------------------------------------------------------------------------
# Per-account ingestion
# ---------------------------------------------------------------------------

@dataclass
class IngestionResult:
    account_id: str
    platform: str
    items_fetched: int
    chunks_stored: int
    skipped: int
    errors: int
    ingestion_type: str  # "scheduled" | "manual" | "backfill"


def ingest_account(
    account: KBAccount,
    platform: str,
    platform_id: str,
    preprocessor: KBPreprocessor,
    db_path: str,
    ingestion_type: str = "scheduled",
    max_items: int = 50,
    registry: Optional[AdapterRegistry] = None,
) -> IngestionResult:
    """
    Ingest content for one (account, platform) pair.

    Uses cursor-based incremental fetch for 'scheduled' mode.
    Uses full fetch (no cursor) for 'manual' mode.

    registry: AdapterRegistry to use for adapter lookup.  Defaults to
        AdapterRegistry.default() when None (backward-compatible).
        Inject a custom registry in tests to avoid real network calls.
    """
    _registry = registry if registry is not None else AdapterRegistry.default()
    adapter = _registry.get(platform)
    if adapter is None:
        logger.warning("No adapter for platform %r — skipping account %s", platform, account.id)
        return IngestionResult(
            account_id=account.id, platform=platform,
            items_fetched=0, chunks_stored=0, skipped=0, errors=1,
            ingestion_type=ingestion_type,
        )

    # Fetch
    try:
        if ingestion_type == "scheduled":
            cursor = _get_cursor(db_path, account.user_id, account.id, platform)
            # First run: apply default lookback so we don't ingest arbitrarily
            # old items depending on how many the feed happens to serve.
            since_iso = cursor if cursor else _initial_lookback_iso()
            raw_items = adapter.get_new_since(
                account_id=account.id,
                platform_id=platform_id,
                since_iso=since_iso,
                max_items=max_items,
                domains=account.domains,
                user_id=account.user_id,
                layer=account.layer,
            )
            if not cursor:
                logger.info(
                    "First run for %s/%s — lookback %s days (since %s)",
                    account.display_name, platform,
                    os.environ.get("KB_INITIAL_LOOKBACK_DAYS", _DEFAULT_INITIAL_LOOKBACK_DAYS),
                    since_iso[:10],
                )
        else:
            # Manual: full fetch with no date filter (user wants everything)
            raw_items = adapter.fetch(
                account_id=account.id,
                platform_id=platform_id,
                max_items=max_items,
                domains=account.domains,
                user_id=account.user_id,
                layer=account.layer,
            )
    except Exception as exc:
        logger.error(
            "Adapter fetch failed for %s/%s (%s): %s",
            account.display_name, platform, platform_id, exc,
        )
        return IngestionResult(
            account_id=account.id, platform=platform,
            items_fetched=0, chunks_stored=0, skipped=0, errors=1,
            ingestion_type=ingestion_type,
        )

    if not raw_items:
        logger.info("No new items for %s/%s", account.display_name, platform)
        # Write cursor even for 0-item runs so the scheduler respects the
        # configured frequency (daily/weekly). Without this, adapters that
        # return 0 items never advance the cursor and the scheduler
        # re-dispatches on every 5-minute tick instead of once per day.
        if ingestion_type == "scheduled":
            _set_cursor(db_path, account.user_id, account.id, platform)
        return IngestionResult(
            account_id=account.id, platform=platform,
            items_fetched=0, chunks_stored=0, skipped=0, errors=0,
            ingestion_type=ingestion_type,
        )

    # Process through preprocessor
    total_chunks = 0
    total_skipped = 0
    total_errors = 0
    # Collect successfully-stored items for post-batch prediction extraction (KB-15).
    # Skipped for backfill — reduces LLM cost on historical loads.
    _stored_items_for_extraction: list[dict] = []

    for raw in raw_items:
        try:
            content = RawContent(
                text=raw.text,
                source_url=raw.source_url,
                author=raw.author,
                published_at=raw.published_at,
                platform=raw.platform,
                account_id=raw.account_id,
                domains=raw.domains,
                user_id=raw.user_id,
                layer=raw.layer,
                source=raw.source,
                ingestion_type=ingestion_type,
            )
            result = preprocessor.process(content)
            if result["status"] == "ok":
                total_chunks += result.get("stored_chunks", 0)
                if ingestion_type != "backfill":
                    import hashlib
                    item_id = hashlib.sha256(raw.text.encode()).hexdigest()[:16]
                    _stored_items_for_extraction.append({
                        "id": f"{item_id}_0000",
                        "text": raw.text,
                    })
            elif result["status"] in ("dedup_skipped", "skipped"):
                total_skipped += 1
        except Exception as exc:
            logger.error("Preprocessor failed for %s: %s", raw.source_url, exc)
            total_errors += 1

    # Phase KB-15: post-ingestion batch prediction extraction.
    # Runs after all items are processed so we can batch up to 10 per LLM call.
    # Only for non-backfill runs to keep LLM cost predictable.
    if _stored_items_for_extraction and account.user_id:
        try:
            from kb.prediction_extractor import PredictionExtractor
            extractor = PredictionExtractor(
                api_key=preprocessor._api_key,
                db_path=db_path,
            )
            extractor.extract_from_batch(
                chunks=_stored_items_for_extraction,
                user_id=account.user_id,
                account_id=account.id,
            )
        except Exception as exc:
            logger.debug("Prediction extraction failed for account=%s: %s", account.id, exc)

    # Advance cursor after successful batch (scheduled mode).
    # Use the latest published_at from fetched items so the next run starts
    # from the last known content timestamp, not wall-clock "now".  This
    # prevents episodes published before ingestion runs (e.g. 07:00 UTC when
    # ingestion runs at 12:00 UTC) from being permanently skipped.
    if ingestion_type == "scheduled" and total_errors == 0:
        pub_timestamps = [r.published_at for r in raw_items if r.published_at]
        latest_pub = max(pub_timestamps, default=None)
        _set_cursor(db_path, account.user_id, account.id, platform, ts=latest_pub)

    logger.info(
        "Ingested %s/%s: %d items, %d chunks, %d skipped, %d errors",
        account.display_name, platform, len(raw_items), total_chunks, total_skipped, total_errors,
    )
    return IngestionResult(
        account_id=account.id,
        platform=platform,
        items_fetched=len(raw_items),
        chunks_stored=total_chunks,
        skipped=total_skipped,
        errors=total_errors,
        ingestion_type=ingestion_type,
    )


# ---------------------------------------------------------------------------
# Single-account full backfill
# ---------------------------------------------------------------------------

def _get_processed_item_urls(db_path: str, user_id: str, account_id: str, platform: str) -> set[str]:
    """
    Return the set of item_url values already recorded in kb_ingestion_log
    for this (user_id, account_id, platform), regardless of status.

    Used by backfill to compute the delta between feed and DB.
    """
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            """
            SELECT item_url FROM kb_ingestion_log
            WHERE user_id = ? AND account_id = ? AND platform = ?
            AND item_url IS NOT NULL
            """,
            (user_id, account_id, platform),
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception as exc:
        logger.warning("_get_processed_item_urls failed: %s", exc)
        return set()


def backfill_account(
    account_id: str,
    user_id: str,
    platforms: Optional[list[str]] = None,
    max_items: int = 500,
    db_path: Optional[str] = None,
    api_key: Optional[str] = None,
    registry: Optional[AdapterRegistry] = None,
) -> list[IngestionResult]:
    """
    Load ALL available historical content for a single account.

    Algorithm:
      1. Wait for any running scheduler tick to finish (shared lock).
      2. Fetch all available items from the feed (up to max_items).
      3. Compute delta: exclude any item whose URL is already in kb_ingestion_log.
      4. Process each missing item one by one, with semantic dedup disabled
         (episodes from the same source are always distinct; only exact hash
         dedup applies to avoid re-processing content that is already in DB).

    Never modifies the scheduled cursor, so incremental ingestion is unaffected.
    Never runs concurrently with the scheduler — waits for the lock if needed.

    Use case: user adds a new podcast/YouTube account and wants full history loaded.
    """
    import gc
    import resource as _resource

    def _rss_mb() -> float:
        return _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss / 1024

    db = db_path or str(get_db_path())

    logger.info("MEM[backfill_start] %.0f MB", _rss_mb())

    from infrastructure.embedding.nano_gpt_embedding_adapter import NanoGptEmbeddingAdapter
    from infrastructure.sqlite.sqlite_chunk_repository import SqliteChunkRepository
    from components.events.event_bus import get_event_bus
    _nano_gpt_base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    preprocessor = KBPreprocessor(
        api_key=api_key,
        db_path=db,
        embedding_port=NanoGptEmbeddingAdapter(api_key=api_key or "", base_url=_nano_gpt_base),
        event_bus=get_event_bus(),
        chunk_repo=SqliteChunkRepository(db),
    )
    graph = get_social_graph(db)

    account = graph.get_account(account_id, user_id=user_id)
    if account is None:
        logger.error("backfill_account: account %s not found for user %s", account_id, user_id)
        return []

    if not account.aliases:
        logger.warning("backfill_account: account %s has no aliases", account_id)
        return []

    # Acquire ingestion lock — wait for scheduler if it is currently running.
    if not _ingestion_lock.acquire(blocking=False):
        logger.info(
            "backfill_account: ingestion lock is held (scheduler or another backfill running) — "
            "waiting before starting backfill for '%s'",
            account.display_name,
        )
        _ingestion_lock.acquire(blocking=True)

    logger.info("backfill_account: starting for '%s'", account.display_name)
    _registry = registry if registry is not None else AdapterRegistry.default()
    try:
        results = []
        for platform, platform_id in account.aliases.items():
            if not platform_id:
                continue
            if platforms and platform not in platforms:
                logger.debug("backfill_account: skipping platform %s (not in filter)", platform)
                continue

            adapter = _registry.get(platform)
            if adapter is None:
                logger.warning("backfill_account: no adapter for platform %r", platform)
                continue

            # Step 1: fetch all available items from the feed
            logger.info(
                "backfill_account: fetching feed %s/%s (max_items=%d)",
                account.display_name, platform, max_items,
            )
            try:
                all_items = adapter.fetch(
                    account_id=account.id,
                    platform_id=platform_id,
                    max_items=max_items,
                    domains=account.domains,
                    user_id=account.user_id,
                    layer=account.layer,
                )
            except Exception as exc:
                logger.error("backfill_account: fetch failed %s/%s: %s", account.display_name, platform, exc)
                results.append(IngestionResult(
                    account_id=account.id, platform=platform,
                    items_fetched=0, chunks_stored=0, skipped=0, errors=1,
                    ingestion_type="backfill",
                ))
                continue

            if not all_items:
                logger.info("backfill_account: feed returned 0 items for %s/%s", account.display_name, platform)
                results.append(IngestionResult(
                    account_id=account.id, platform=platform,
                    items_fetched=0, chunks_stored=0, skipped=0, errors=0,
                    ingestion_type="backfill",
                ))
                continue

            # Step 2: delta — skip items already recorded in kb_ingestion_log
            processed_urls = _get_processed_item_urls(db, account.user_id, account.id, platform)
            new_items = [item for item in all_items if item.source_url not in processed_urls]

            logger.info(
                "backfill_account: %s/%s — feed=%d already_in_db=%d to_process=%d",
                account.display_name, platform,
                len(all_items), len(all_items) - len(new_items), len(new_items),
            )

            if not new_items:
                results.append(IngestionResult(
                    account_id=account.id, platform=platform,
                    items_fetched=len(all_items), chunks_stored=0,
                    skipped=len(all_items), errors=0,
                    ingestion_type="backfill",
                ))
                continue

            # Step 3: process each new item one by one
            # ingestion_type="backfill" disables semantic near-dedup in preprocessor
            total_chunks = 0
            total_skipped = 0
            total_errors = 0
            for ep_idx, raw in enumerate(new_items):
                logger.info(
                    "MEM[ep %d/%d before process] %.0f MB — %s",
                    ep_idx + 1, len(new_items), _rss_mb(), raw.source_url,
                )
                try:
                    content = RawContent(
                        text=raw.text,
                        source_url=raw.source_url,
                        author=raw.author,
                        published_at=raw.published_at,
                        platform=raw.platform,
                        account_id=raw.account_id,
                        domains=raw.domains,
                        user_id=raw.user_id,
                        layer=raw.layer,
                        source=raw.source,
                        ingestion_type="backfill",
                    )
                    result = preprocessor.process(content)
                    if result["status"] == "ok":
                        total_chunks += result.get("stored_chunks", 0)
                        logger.info(
                            "backfill_account: stored %d chunks — %s",
                            result.get("stored_chunks", 0), raw.source_url,
                        )
                    elif result["status"] in ("dedup_skipped", "skipped"):
                        total_skipped += 1
                    logger.info(
                        "MEM[ep %d/%d after process] %.0f MB — status=%s",
                        ep_idx + 1, len(new_items), _rss_mb(), result.get("status"),
                    )
                except Exception as exc:
                    logger.error("backfill_account: preprocessor failed for %s: %s", raw.source_url, exc)
                    total_errors += 1
                finally:
                    gc.collect()
                    logger.debug("MEM[ep %d/%d after gc] %.0f MB", ep_idx + 1, len(new_items), _rss_mb())

            logger.info(
                "backfill_account done: %s/%s — feed=%d new=%d chunks=%d skipped=%d errors=%d",
                account.display_name, platform,
                len(all_items), len(new_items), total_chunks, total_skipped, total_errors,
            )
            results.append(IngestionResult(
                account_id=account.id, platform=platform,
                items_fetched=len(new_items),
                chunks_stored=total_chunks,
                skipped=total_skipped,
                errors=total_errors,
                ingestion_type="backfill",
            ))

        return results
    finally:
        _ingestion_lock.release()


# ---------------------------------------------------------------------------
# Domain / user-level ingestion
# ---------------------------------------------------------------------------

def ingest_all_accounts(
    user_id: str = "default",
    domain: Optional[str] = None,
    ingestion_type: str = "scheduled",
    max_items_per_platform: int = 50,
    db_path: Optional[str] = None,
    api_key: Optional[str] = None,
    registry: Optional[AdapterRegistry] = None,
) -> list[IngestionResult]:
    """
    Ingest all active accounts for *user_id* (optionally filtered by *domain*).

    Each account may have multiple platform aliases; each alias is ingested
    independently. Results are collected and returned.

    This is the main entry point called by:
      - The scheduler (ingestion_type="scheduled")
      - The manual API trigger (ingestion_type="manual")

    Acquires _ingestion_lock so it never runs concurrently with backfill_account().

    registry: AdapterRegistry to use for adapter lookup.  Defaults to
        AdapterRegistry.default() when None (backward-compatible).
        Inject a custom registry in tests to avoid real network calls.
    """
    with _ingestion_lock:
        return _ingest_all_accounts_locked(
            user_id=user_id, domain=domain, ingestion_type=ingestion_type,
            max_items_per_platform=max_items_per_platform, db_path=db_path, api_key=api_key,
            registry=registry,
        )


def _ingest_all_accounts_locked(
    user_id: str = "default",
    domain: Optional[str] = None,
    ingestion_type: str = "scheduled",
    max_items_per_platform: int = 50,
    db_path: Optional[str] = None,
    api_key: Optional[str] = None,
    registry: Optional[AdapterRegistry] = None,
) -> list[IngestionResult]:
    db = db_path or str(get_db_path())
    from infrastructure.embedding.nano_gpt_embedding_adapter import NanoGptEmbeddingAdapter
    from infrastructure.sqlite.sqlite_chunk_repository import SqliteChunkRepository
    from components.events.event_bus import get_event_bus
    _nano_gpt_base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    preprocessor = KBPreprocessor(
        api_key=api_key,
        db_path=db,
        embedding_port=NanoGptEmbeddingAdapter(api_key=api_key or "", base_url=_nano_gpt_base),
        event_bus=get_event_bus(),
        chunk_repo=SqliteChunkRepository(db),
    )
    graph = get_social_graph(db)

    if domain:
        accounts = graph.accounts_for_domain(domain, user_id=user_id)
    else:
        accounts = graph.all_accounts(user_id=user_id)

    if not accounts:
        logger.info("No active accounts for user=%s domain=%s", user_id, domain)
        return []

    results = []
    for account in accounts:
        if not account.aliases:
            logger.debug("Account %s has no aliases — skipping", account.display_name)
            continue
        for platform, platform_id in account.aliases.items():
            if not platform_id:
                continue
            result = ingest_account(
                account=account,
                platform=platform,
                platform_id=platform_id,
                preprocessor=preprocessor,
                db_path=db,
                ingestion_type=ingestion_type,
                max_items=max_items_per_platform,
                registry=registry,
            )
            results.append(result)

    total_chunks = sum(r.chunks_stored for r in results)
    total_errors = sum(r.errors for r in results)
    logger.info(
        "Ingestion run complete: user=%s domain=%s accounts=%d runs=%d chunks=%d errors=%d",
        user_id, domain or "all", len(accounts), len(results), total_chunks, total_errors,
    )
    return results
