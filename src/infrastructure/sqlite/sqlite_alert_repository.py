"""
SqliteAlertRepository — SQLite implementation of AlertRepository.

All SQL is moved verbatim from kb/alert_engine.py.  No domain logic lives here.
"""
import json
from datetime import datetime, timezone

from kb.alert_engine import AlertMatch, KBAlert
from kb.ports.alert_repository import AlertRepository

from infrastructure.sqlite._connection import get_db_connection


class SqliteAlertRepository(AlertRepository):
    """Reads and writes kb_alerts and kb_alert_matches."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # find_active_by_user
    # ------------------------------------------------------------------

    def find_active_by_user(self, user_id: str) -> list[KBAlert]:
        """Return all active alerts with parsed embeddings for *user_id*.

        SQL origin: alert_engine._load_active_alerts().
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT * FROM kb_alerts WHERE user_id = ? AND active = 1",
                    (user_id,),
                )
                rows = cur.fetchall()
                alerts = []
                for row in rows:
                    raw_emb = row["query_embedding"]
                    if raw_emb is None:
                        continue
                    try:
                        if isinstance(raw_emb, (bytes, bytearray)):
                            emb = json.loads(raw_emb.decode("utf-8"))
                        else:
                            emb = json.loads(raw_emb)
                    except Exception:
                        continue
                    if not emb:
                        continue
                    domain_filter = json.loads(row["domain_filter"]) if row["domain_filter"] else None
                    account_filter = json.loads(row["account_filter"]) if row["account_filter"] else None
                    alerts.append(KBAlert(
                        id=row["id"],
                        user_id=row["user_id"],
                        query=row["query"],
                        query_embedding=emb,
                        domain_filter=domain_filter,
                        account_filter=account_filter,
                        similarity_threshold=float(row["similarity_threshold"]),
                        active=bool(row["active"]),
                    ))
                return alerts
        except Exception:
            return []

    # ------------------------------------------------------------------
    # record_match
    # ------------------------------------------------------------------

    def record_match(self, match: AlertMatch) -> None:
        """Insert a match row and update alert.last_triggered_at.

        SQL origin: alert_engine._record_match().
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            with get_db_connection(self._db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO kb_alert_matches
                        (alert_id, user_id, chunk_id, source_url, account_id, domain, similarity, matched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        match.alert_id, match.user_id, match.chunk_id,
                        match.source_url, match.account_id, match.domain,
                        match.similarity, now,
                    ),
                )
                conn.execute(
                    "UPDATE kb_alerts SET last_triggered_at = ? WHERE id = ?",
                    (now, match.alert_id),
                )
                conn.commit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # save
    # ------------------------------------------------------------------

    def save(self, alert: KBAlert, created_at: str) -> None:
        """Insert a new alert row.

        SQL origin: alert_engine.create_alert().
        """
        with get_db_connection(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO kb_alerts
                    (id, user_id, query, query_embedding, domain_filter, account_filter,
                     similarity_threshold, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    alert.id,
                    alert.user_id,
                    alert.query,
                    json.dumps(alert.query_embedding),
                    json.dumps(alert.domain_filter) if alert.domain_filter else None,
                    json.dumps(alert.account_filter) if alert.account_filter else None,
                    alert.similarity_threshold,
                    created_at,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------

    def delete(self, alert_id: str) -> None:
        """Deactivate an alert.

        SQL origin: alert management routes (not yet migrated; interface defined for completeness).
        """
        with get_db_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE kb_alerts SET active = 0 WHERE id = ?",
                (alert_id,),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # find_pending_matches
    # ------------------------------------------------------------------

    def find_pending_matches(self, user_id: str) -> list[dict]:
        """Return undelivered match rows for *user_id*.

        SQL origin: alert_engine.get_pending_matches().
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT m.id, m.alert_id, a.query, m.chunk_id, m.source_url,
                           m.account_id, m.domain, m.similarity, m.matched_at
                    FROM kb_alert_matches m
                    JOIN kb_alerts a ON m.alert_id = a.id
                    WHERE m.user_id = ? AND m.delivered = 0
                    ORDER BY m.matched_at ASC
                    """,
                    (user_id,),
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # mark_matches_delivered
    # ------------------------------------------------------------------

    def mark_matches_delivered(self, match_ids: list[int]) -> None:
        """Mark kb_alert_matches rows as delivered.

        SQL origin: alert_engine.mark_matches_delivered().
        """
        if not match_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" * len(match_ids))
        try:
            with get_db_connection(self._db_path) as conn:
                conn.execute(
                    f"UPDATE kb_alert_matches SET delivered = 1, delivered_at = ? WHERE id IN ({placeholders})",
                    [now] + match_ids,
                )
                conn.commit()
        except Exception:
            pass
