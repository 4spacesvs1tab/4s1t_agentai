"""
KB Source Reliability Service — Phase KB-16.

Computes and persists per-account reliability scores to kb_source_reliability.

Phase 1 signals (rule-based, no LLM):
  contradiction_rate  — fraction of this account's chunks that are flagged
                        with contradicts_chunk_id (lower is better)
  activity_score      — items ingested for this account in last 30 days /
                        mean ingestion across all accounts; normalised to [0,1]
  citation_rate       — citation count for this account in last 30 days /
                        mean citation count across all accounts; normalised
                        to [0,1]

overall_score = mean(available signals)  — no arbitrary weights yet; will
tune once data accumulates (Phase 2 adds prediction_accuracy).

Design reference: KB_assistant_design_v2.md §12.2
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)

_ACTIVITY_WINDOW_DAYS = 30
_CITATION_WINDOW_DAYS = 30


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Citation log helpers
# ---------------------------------------------------------------------------

def log_citations(
    account_ids: list[str],
    user_id: str,
    query_text: str,
    db_path: str | None = None,
) -> None:
    """
    Append one citation row per account returned by a knowledge_base_search call.

    Non-blocking: errors are logged and swallowed so search never fails due
    to citation logging.
    """
    if not account_ids:
        return
    path = db_path or str(get_db_path())
    now = _utcnow()
    truncated_query = (query_text or "")[:200]
    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executemany(
            """
            INSERT INTO kb_citation_log (id, account_id, user_id, cited_at, query_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (str(uuid.uuid4()), acc_id, user_id, now, truncated_query)
                for acc_id in account_ids
                if acc_id  # skip empty strings
            ],
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("Citation log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Reliability computation
# ---------------------------------------------------------------------------

def _compute_contradiction_rate(conn: sqlite3.Connection, account_id: str, user_id: str) -> tuple[float, int]:
    """
    Return (contradiction_rate, sample_size) for *account_id*.

    Reads kb_ingestion_log to count total stored chunks for this account,
    then queries ChromaDB metadata for contradiction flags.

    Falls back to SQLite-only estimate: uses kb_ingestion_log chunk_count
    as the denominator and reads kb_citation_log as a proxy.

    ChromaDB is queried directly when available (via vector_store singleton).
    """
    # Total chunks ingested (denominator)
    row = conn.execute(
        """
        SELECT SUM(chunk_count) FROM kb_ingestion_log
        WHERE account_id = ? AND user_id = ? AND status = 'ok'
        """,
        (account_id, user_id),
    ).fetchone()
    total_chunks = (row[0] or 0) if row else 0
    if total_chunks == 0:
        return 0.5, 0

    # Contradicted chunks (numerator) — query ChromaDB
    contradicted = 0
    try:
        from kb.vector_store import get_kb_vector_store, KB_COLLECTION_CONTENT
        store = get_kb_vector_store()
        client = store._get_client()
        col = client.get_collection(KB_COLLECTION_CONTENT)
        # ChromaDB does not support $ne on string for empty check; use a workaround:
        # get all chunks for this account, then filter in Python
        raw = col.get(
            where={
                "$and": [
                    {"user_id": {"$eq": user_id}},
                    {"account_id": {"$eq": account_id}},
                ]
            },
            include=["metadatas"],
            limit=5000,
        )
        metas = raw.get("metadatas") or []
        contradicted = sum(
            1 for m in metas
            if m and m.get("contradicts_chunk_id", "")
        )
        total_chunks = max(total_chunks, len(metas))  # prefer ChromaDB count
    except Exception as exc:
        logger.debug("ChromaDB contradiction count failed for %s: %s", account_id, exc)

    rate = min(1.0, contradicted / total_chunks) if total_chunks > 0 else 0.5
    return round(rate, 4), total_chunks


def _compute_activity_score(conn: sqlite3.Connection, account_id: str, user_id: str) -> float:
    """
    Return activity_score in [0, 1].

    Ratio = account_items_last_30d / mean_items_per_account.
    Capped at 1.0 (above-average activity maps to 1.0, not above).
    """
    window_clause = f"datetime('now', '-{_ACTIVITY_WINDOW_DAYS} days')"

    # This account's ingestion count in window
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM kb_ingestion_log
        WHERE account_id = ? AND user_id = ? AND status = 'ok'
          AND created_at >= {window_clause}
        """,
        (account_id, user_id),
    ).fetchone()
    account_count = (row[0] or 0) if row else 0

    # Mean across all accounts for this user in the same window
    row2 = conn.execute(
        f"""
        SELECT AVG(cnt) FROM (
            SELECT COUNT(*) as cnt FROM kb_ingestion_log
            WHERE user_id = ? AND status = 'ok'
              AND created_at >= {window_clause}
            GROUP BY account_id
        )
        """,
        (user_id,),
    ).fetchone()
    mean_count = (row2[0] or 0.0) if row2 else 0.0

    if mean_count <= 0:
        return 0.5
    return min(1.0, round(account_count / mean_count, 4))


def _compute_citation_rate(conn: sqlite3.Connection, account_id: str, user_id: str) -> float:
    """
    Return citation_rate in [0, 1].

    Ratio = account_citations_last_30d / mean_citations_per_account.
    """
    window_clause = f"datetime('now', '-{_CITATION_WINDOW_DAYS} days')"

    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM kb_citation_log
        WHERE account_id = ? AND user_id = ? AND cited_at >= {window_clause}
        """,
        (account_id, user_id),
    ).fetchone()
    account_citations = (row[0] or 0) if row else 0

    row2 = conn.execute(
        f"""
        SELECT AVG(cnt) FROM (
            SELECT COUNT(*) as cnt FROM kb_citation_log
            WHERE user_id = ? AND cited_at >= {window_clause}
            GROUP BY account_id
        )
        """,
        (user_id,),
    ).fetchone()
    mean_citations = (row2[0] or 0.0) if row2 else 0.0

    if mean_citations <= 0:
        return 0.5
    return min(1.0, round(account_citations / mean_citations, 4))


def _compute_overall(
    contradiction_rate: float,
    activity_score: float,
    citation_rate: float,
    prediction_accuracy: Optional[float],
) -> float:
    """Mean of available signals.  contradiction_rate inverted (lower = better)."""
    signals = [
        1.0 - contradiction_rate,  # invert: lower contradiction → higher score
        activity_score,
        citation_rate,
    ]
    if prediction_accuracy is not None:
        signals.append(prediction_accuracy)
    return round(sum(signals) / len(signals), 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SourceReliabilityService:
    """
    Compute and persist reliability scores for KB accounts.

    Usage::

        svc = SourceReliabilityService(db_path="...")
        svc.update_account(account_id="jeff_snider", user_id="<uuid>")
        score = svc.get_score("jeff_snider")
        svc.recompute_all(user_id="<uuid>")
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or str(get_db_path())

    def update_account(self, account_id: str, user_id: str) -> dict:
        """
        Recompute reliability scores for *account_id* and persist to DB.

        Returns the updated score dict.
        """
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            contradiction_rate, sample_size = _compute_contradiction_rate(conn, account_id, user_id)
            activity_score = _compute_activity_score(conn, account_id, user_id)
            citation_rate = _compute_citation_rate(conn, account_id, user_id)

            # prediction_accuracy: read from existing row if present
            existing = conn.execute(
                "SELECT prediction_accuracy FROM kb_source_reliability WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            prediction_accuracy = existing[0] if existing else None

            overall = _compute_overall(
                contradiction_rate, activity_score, citation_rate, prediction_accuracy
            )
            now = _utcnow()

            conn.execute(
                """
                INSERT INTO kb_source_reliability
                    (account_id, contradiction_rate, activity_score, citation_rate,
                     prediction_accuracy, overall_score, last_updated, sample_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    contradiction_rate = excluded.contradiction_rate,
                    activity_score     = excluded.activity_score,
                    citation_rate      = excluded.citation_rate,
                    overall_score      = excluded.overall_score,
                    last_updated       = excluded.last_updated,
                    sample_size        = excluded.sample_size
                """,
                (
                    account_id,
                    contradiction_rate,
                    activity_score,
                    citation_rate,
                    prediction_accuracy,
                    overall,
                    now,
                    sample_size,
                ),
            )
            conn.commit()
            result = {
                "account_id": account_id,
                "contradiction_rate": contradiction_rate,
                "activity_score": activity_score,
                "citation_rate": citation_rate,
                "prediction_accuracy": prediction_accuracy,
                "overall_score": overall,
                "last_updated": now,
                "sample_size": sample_size,
            }
            logger.info(
                "Reliability updated: %s overall=%.3f (contra=%.3f act=%.3f cite=%.3f)",
                account_id, overall, contradiction_rate, activity_score, citation_rate,
            )
            return result
        except Exception as exc:
            conn.rollback()
            logger.warning("Reliability update failed for %s: %s", account_id, exc)
            raise
        finally:
            conn.close()

    def get_score(self, account_id: str) -> Optional[dict]:
        """Return the latest reliability score row for *account_id*, or None."""
        try:
            conn = sqlite3.connect(self._db_path)
            row = conn.execute(
                """
                SELECT account_id, contradiction_rate, activity_score, citation_rate,
                       prediction_accuracy, overall_score, last_updated, sample_size
                FROM kb_source_reliability WHERE account_id = ?
                """,
                (account_id,),
            ).fetchone()
            conn.close()
            if not row:
                return None
            keys = [
                "account_id", "contradiction_rate", "activity_score", "citation_rate",
                "prediction_accuracy", "overall_score", "last_updated", "sample_size",
            ]
            return dict(zip(keys, row))
        except Exception as exc:
            logger.debug("get_score failed for %s: %s", account_id, exc)
            return None

    def get_all_scores(self, user_id: str | None = None) -> list[dict]:
        """
        Return all reliability score rows, ordered by overall_score DESC.

        *user_id* is accepted for API consistency but kb_source_reliability is
        keyed by account_id only (accounts are currently global).
        """
        try:
            conn = sqlite3.connect(self._db_path)
            rows = conn.execute(
                """
                SELECT r.account_id, r.contradiction_rate, r.activity_score, r.citation_rate,
                       r.prediction_accuracy, r.overall_score, r.last_updated, r.sample_size,
                       a.display_name
                FROM kb_source_reliability r
                LEFT JOIN kb_accounts a ON a.id = r.account_id
                ORDER BY r.overall_score DESC
                """
            ).fetchall()
            conn.close()
            keys = [
                "account_id", "contradiction_rate", "activity_score", "citation_rate",
                "prediction_accuracy", "overall_score", "last_updated", "sample_size",
                "display_name",
            ]
            return [dict(zip(keys, row)) for row in rows]
        except Exception as exc:
            logger.debug("get_all_scores failed: %s", exc)
            return []

    def recompute_all(self, user_id: str) -> int:
        """
        Recompute reliability for every account that has ingestion history
        for *user_id*.  Returns the number of accounts updated.
        """
        try:
            conn = sqlite3.connect(self._db_path)
            rows = conn.execute(
                "SELECT DISTINCT account_id FROM kb_ingestion_log WHERE user_id = ? AND account_id IS NOT NULL",
                (user_id,),
            ).fetchall()
            conn.close()
        except Exception as exc:
            logger.warning("recompute_all: failed to list accounts: %s", exc)
            return 0

        updated = 0
        for (account_id,) in rows:
            try:
                self.update_account(account_id, user_id)
                updated += 1
            except Exception as exc:
                logger.warning("recompute_all: failed for %s: %s", account_id, exc)
        logger.info("Reliability recomputed for %d accounts (user=%s)", updated, user_id)
        return updated


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_svc: SourceReliabilityService | None = None


def get_reliability_service(db_path: str | None = None) -> SourceReliabilityService:
    global _svc
    if _svc is None:
        _svc = SourceReliabilityService(db_path)
    return _svc
