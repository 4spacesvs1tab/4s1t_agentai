"""
KB Semantic Alert Engine — Phase KB-3 (G11).

After each content chunk is stored in ChromaDB, the alert engine compares
its embedding against all active `kb_alerts` for the content owner's user_id.

If the cosine similarity between a new chunk and an alert's query_embedding
exceeds `kb_alerts.similarity_threshold`, a row is recorded in
`kb_alert_matches` and the alert's `last_triggered_at` is updated.

NIP-17 delivery of alerts is handled by the scheduler's _deliver_alerts()
loop, which reads undelivered rows from `kb_alert_matches`.

Alert lifecycle:
  CREATE  → user registers an alert query + embedding
  MATCH   → engine finds a chunk that matches → writes kb_alert_matches row
  DELIVER → scheduler reads pending matches → sends NIP-17 DM → marks delivered

Design reference: KnowledgeBase_design.md §6.7
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core.db_path import get_db_path
from kb.ports.alert_repository import AlertRepository
from utils.logger import setup_logger

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Alert model
# ---------------------------------------------------------------------------

@dataclass
class KBAlert:
    id: str
    user_id: str
    query: str
    query_embedding: list[float]
    domain_filter: Optional[list[str]]    # None = all domains
    account_filter: Optional[list[str]]   # None = all accounts
    similarity_threshold: float
    active: bool


@dataclass
class AlertMatch:
    alert_id: str
    user_id: str
    chunk_id: str
    source_url: str
    account_id: str
    domain: str
    similarity: float


# ---------------------------------------------------------------------------
# Vector math
# ---------------------------------------------------------------------------

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length float vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Alert engine
# ---------------------------------------------------------------------------

class AlertEngine:
    """
    Checks newly ingested chunks against active user alerts.

    Instantiate once per ingestion run and call check_chunk() for each
    stored chunk embedding.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        alert_repo: AlertRepository,
    ) -> None:
        self._db_path = db_path or str(get_db_path())
        self._repo = alert_repo
        # Cache: user_id → list[KBAlert] (loaded lazily, cleared between runs)
        self._alert_cache: dict[str, list[KBAlert]] = {}

    def preload(self, user_id: str) -> None:
        """Pre-load alerts for *user_id* into the cache."""
        self._alert_cache[user_id] = self._repo.find_active_by_user(user_id)

    def check_chunk(
        self,
        chunk_id: str,
        embedding: list[float],
        user_id: str,
        source_url: str = "",
        account_id: str = "",
        domains: str = "",
    ) -> list[AlertMatch]:
        """
        Compare *embedding* against all active alerts for *user_id*.

        Records matches in the DB and returns the list of matched alerts.
        """
        if not embedding or all(v == 0.0 for v in embedding):
            return []

        if user_id not in self._alert_cache:
            self.preload(user_id)

        alerts = self._alert_cache.get(user_id, [])
        if not alerts:
            return []

        domain_set = set(d for d in domains.split("|") if d)
        matches: list[AlertMatch] = []

        for alert in alerts:
            # Domain filter
            if alert.domain_filter and not domain_set.intersection(alert.domain_filter):
                continue
            # Account filter
            if alert.account_filter and account_id and account_id not in alert.account_filter:
                continue

            sim = _cosine_similarity(embedding, alert.query_embedding)
            if sim >= alert.similarity_threshold:
                match = AlertMatch(
                    alert_id=alert.id,
                    user_id=user_id,
                    chunk_id=chunk_id,
                    source_url=source_url,
                    account_id=account_id,
                    domain=list(domain_set)[0] if domain_set else "",
                    similarity=sim,
                )
                self._repo.record_match(match)
                logger.info(
                    "Alert %s triggered by chunk %s (sim=%.3f) for user=%s",
                    match.alert_id, match.chunk_id, match.similarity, match.user_id,
                )
                matches.append(match)

        return matches

    def handle_content_ingested(self, event: "ContentIngested") -> None:
        """
        ContentIngested subscriber — check the ingested chunk against active alerts.

        Mirrors the logic previously at preprocessor step 10.
        Skips silently for backfill ingestion (reduces memory pressure on
        low-RAM machines) and when no chunks/embeddings were produced.
        """
        from kb.domain.events import ContentIngested  # noqa: F401 (type guard)

        if event.ingestion_type == "backfill":
            return
        if not event.embedding:
            return

        try:
            self.check_chunk(
                chunk_id=event.chunk_id,
                embedding=list(event.embedding),
                user_id=event.user_id,
                source_url=event.source_url,
                account_id=event.account_id,
                domains=event.domains,
            )
        except Exception as exc:
            logger.debug("Alert engine check failed in event handler: %s", exc)

    def clear_cache(self) -> None:
        """Clear the alert cache (call before each ingestion run)."""
        self._alert_cache.clear()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_engine: AlertEngine | None = None


def get_alert_engine(db_path: Optional[str] = None) -> AlertEngine:
    """Return the shared AlertEngine singleton.

    In production, the singleton is pre-wired in initializer.py with an explicit
    SqliteAlertRepository.  This fallback path handles out-of-process callers
    (e.g. scripts and tests that bypass the initializer).
    """
    global _engine
    if _engine is None:
        from infrastructure.sqlite.sqlite_alert_repository import SqliteAlertRepository
        db = db_path or str(get_db_path())
        _engine = AlertEngine(db_path=db, alert_repo=SqliteAlertRepository(db))
    return _engine


# ---------------------------------------------------------------------------
# Alert CRUD (create / list / delete alerts)
# ---------------------------------------------------------------------------

def create_alert(
    db_path: str,
    user_id: str,
    query: str,
    query_embedding: list[float],
    domain_filter: Optional[list[str]] = None,
    account_filter: Optional[list[str]] = None,
    similarity_threshold: float = 0.85,
) -> str:
    """
    Create a new alert subscription.

    Returns the new alert ID (UUID).
    """
    import uuid as _uuid
    alert_id = str(_uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    alert = KBAlert(
        id=alert_id,
        user_id=user_id,
        query=query,
        query_embedding=query_embedding,
        domain_filter=domain_filter,
        account_filter=account_filter,
        similarity_threshold=similarity_threshold,
        active=True,
    )

    from infrastructure.sqlite.sqlite_alert_repository import SqliteAlertRepository
    repo = SqliteAlertRepository(db_path)
    repo.save(alert, created_at=now)

    # Invalidate cache so the new alert is picked up
    engine = get_alert_engine(db_path)
    engine._alert_cache.pop(user_id, None)

    logger.info("Created alert %s for user=%s query=%r", alert_id, user_id, query[:60])
    return alert_id


def get_pending_matches(db_path: str, user_id: str) -> list[dict]:
    """
    Return undelivered alert matches for *user_id*.

    Used by the brief dispatcher / scheduler to build NIP-17 notifications.
    """
    from infrastructure.sqlite.sqlite_alert_repository import SqliteAlertRepository
    return SqliteAlertRepository(db_path).find_pending_matches(user_id)


def mark_matches_delivered(db_path: str, match_ids: list[int]) -> None:
    """Mark a list of kb_alert_matches rows as delivered."""
    from infrastructure.sqlite.sqlite_alert_repository import SqliteAlertRepository
    SqliteAlertRepository(db_path).mark_matches_delivered(match_ids)
