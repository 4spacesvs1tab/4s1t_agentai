"""
SqliteDiscoveryRepository — SQLite implementation of DiscoveryRepository.

All SQL is moved verbatim from kb/discovery.py.  No domain logic lives here.
"""
import json

from kb.discovery import DiscoveryCandidate
from kb.ports.discovery_repository import DiscoveryRepository

from infrastructure.sqlite._connection import get_db_connection


def _row_to_candidate(row) -> DiscoveryCandidate:
    """Map a sqlite3.Row from kb_discovery_queue to DiscoveryCandidate."""
    return DiscoveryCandidate(
        id=row["id"],
        user_id=row["user_id"],
        candidate_name=row["candidate_name"],
        candidate_handles=json.loads(row["candidate_handles"] or "{}"),
        discovered_via=row["discovered_via"] or "",
        evidence=json.loads(row["evidence"] or "[]"),
        mention_count=row["mention_count"],
        discovery_source=row["discovery_source"] or "ingestion",
        rationale=row["rationale"] or "",
        status=row["status"],
        created_at=row["created_at"],
        reviewed_at=row["reviewed_at"],
    )


class SqliteDiscoveryRepository(DiscoveryRepository):
    """Reads and writes kb_discovery_queue, kb_accounts, kb_account_aliases, kb_relations."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # find_pending
    # ------------------------------------------------------------------

    def find_pending(self, user_id: str, min_mentions: int = 3) -> list[DiscoveryCandidate]:
        """SQL origin: discovery.get_pending()."""
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT * FROM kb_discovery_queue
                    WHERE user_id = ? AND status = 'pending' AND mention_count >= ?
                    ORDER BY mention_count DESC, created_at DESC
                    """,
                    (user_id, min_mentions),
                )
                rows = cur.fetchall()
                return [_row_to_candidate(r) for r in rows]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # find_all
    # ------------------------------------------------------------------

    def find_all(self, user_id: str, status: str | None = None) -> list[DiscoveryCandidate]:
        """SQL origin: discovery.get_all()."""
        try:
            with get_db_connection(self._db_path) as conn:
                if status:
                    cur = conn.execute(
                        "SELECT * FROM kb_discovery_queue WHERE user_id = ? AND status = ? ORDER BY created_at DESC",
                        (user_id, status),
                    )
                else:
                    cur = conn.execute(
                        "SELECT * FROM kb_discovery_queue WHERE user_id = ? ORDER BY created_at DESC",
                        (user_id,),
                    )
                rows = cur.fetchall()
                return [_row_to_candidate(r) for r in rows]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # find_by_id
    # ------------------------------------------------------------------

    def find_by_id(self, candidate_id: int, user_id: str) -> DiscoveryCandidate | None:
        """SQL origin: discovery.approve_candidate() — initial SELECT."""
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT * FROM kb_discovery_queue WHERE id = ? AND user_id = ?",
                    (candidate_id, user_id),
                )
                row = cur.fetchone()
                return _row_to_candidate(row) if row else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # find_active_account_by_handle
    # ------------------------------------------------------------------

    def find_active_account_by_handle(
        self, user_id: str, platform: str, handle: str
    ) -> str | None:
        """Return account_id for an active account owning this handle, or None.

        SQL origin: discovery.upsert_candidate() + discovery.approve_candidate()
        — kb_account_aliases JOIN kb_accounts.
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT a.id FROM kb_account_aliases al
                    JOIN kb_accounts a ON a.id = al.account_id
                    WHERE a.user_id = ? AND al.platform = ? AND al.platform_id = ? AND a.active = 1
                    """,
                    (user_id, platform, handle),
                )
                row = cur.fetchone()
                return row["id"] if row else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # find_active_account_by_name
    # ------------------------------------------------------------------

    def find_active_account_by_name(self, user_id: str, name: str) -> str | None:
        """Return account_id for an active account with this display_name, or None.

        SQL origin: discovery.upsert_candidate() — kb_accounts SELECT by display_name.
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT id FROM kb_accounts
                    WHERE user_id = ? AND lower(display_name) = lower(?) AND active = 1
                    """,
                    (user_id, name),
                )
                row = cur.fetchone()
                return row["id"] if row else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # find_candidate_by_handle
    # ------------------------------------------------------------------

    def find_candidate_by_handle(
        self, user_id: str, handle_fragment: str
    ) -> DiscoveryCandidate | None:
        """Return most-recent non-decided queue row matching handle, or None.

        SQL origin: discovery.upsert_candidate() — candidate_handles LIKE.
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT id, mention_count, evidence, candidate_handles, status, rationale,
                           user_id, candidate_name, discovered_via, discovery_source,
                           created_at, reviewed_at
                    FROM kb_discovery_queue
                    WHERE user_id = ? AND candidate_handles LIKE ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (user_id, f'%"{handle_fragment}"%'),
                )
                row = cur.fetchone()
                if row and row["status"] not in ("approved", "blacklisted"):
                    return _row_to_candidate(row)
                return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # find_candidate_by_name
    # ------------------------------------------------------------------

    def find_candidate_by_name(self, user_id: str, name: str) -> DiscoveryCandidate | None:
        """Return most-recent queue row matching name (case-insensitive), or None.

        SQL origin: discovery.upsert_candidate() — lower(candidate_name) = lower(?).
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT id, mention_count, evidence, candidate_handles, status, rationale,
                           user_id, candidate_name, discovered_via, discovery_source,
                           created_at, reviewed_at
                    FROM kb_discovery_queue
                    WHERE user_id = ? AND lower(candidate_name) = lower(?)
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (user_id, name),
                )
                row = cur.fetchone()
                return _row_to_candidate(row) if row else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # insert_candidate
    # ------------------------------------------------------------------

    def insert_candidate(
        self,
        user_id: str,
        name: str,
        handles: dict,
        discovered_via: str | None,
        evidence: list[str],
        rationale: str,
    ) -> None:
        """Insert a new pending discovery candidate.

        SQL origin: discovery.upsert_candidate() — kb_discovery_queue INSERT.
        """
        with get_db_connection(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO kb_discovery_queue
                    (user_id, candidate_name, candidate_handles,
                     discovered_via, evidence, mention_count,
                     discovery_source, rationale, status)
                VALUES (?, ?, ?, ?, ?, 1, 'ingestion', ?, 'pending')
                """,
                (
                    user_id,
                    name,
                    json.dumps(handles),
                    discovered_via or None,
                    json.dumps(evidence),
                    rationale,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # update_candidate
    # ------------------------------------------------------------------

    def update_candidate(
        self,
        candidate_id: int,
        mention_count: int,
        evidence: list[str],
        handles: dict,
        rationale: str | None,
    ) -> None:
        """Update an existing queue row.

        SQL origin: discovery.upsert_candidate() — UPDATE with/without rationale.
        """
        with get_db_connection(self._db_path) as conn:
            if rationale is not None:
                conn.execute(
                    """
                    UPDATE kb_discovery_queue
                    SET mention_count = ?,
                        evidence = ?,
                        candidate_handles = ?,
                        rationale = ?
                    WHERE id = ?
                    """,
                    (mention_count, json.dumps(evidence), json.dumps(handles), rationale, candidate_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE kb_discovery_queue
                    SET mention_count = ?,
                        evidence = ?,
                        candidate_handles = ?
                    WHERE id = ?
                    """,
                    (mention_count, json.dumps(evidence), json.dumps(handles), candidate_id),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # set_status
    # ------------------------------------------------------------------

    def set_status(
        self,
        candidate_id: int,
        user_id: str,
        status: str,
        reviewed_at: str,
    ) -> None:
        """Set queue row status and reviewed_at.

        SQL origin: discovery.reject_candidate(), discovery.set_status(),
        and the approval UPDATE in discovery.approve_candidate().
        """
        with get_db_connection(self._db_path) as conn:
            conn.execute(
                """
                UPDATE kb_discovery_queue
                SET status = ?, reviewed_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (status, reviewed_at, candidate_id, user_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # merge_aliases_into_account
    # ------------------------------------------------------------------

    def merge_aliases_into_account(
        self, account_id: str, aliases: dict[str, str]
    ) -> None:
        """INSERT OR IGNORE aliases into an existing account.

        SQL origin: discovery.approve_candidate() — handle-conflict merge branch.
        """
        with get_db_connection(self._db_path) as conn:
            for plat, handle in aliases.items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO kb_account_aliases
                        (account_id, platform, platform_id, confidence, verified)
                    VALUES (?, ?, ?, 0.8, 0)
                    """,
                    (account_id, plat, handle),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # create_account_with_aliases
    # ------------------------------------------------------------------

    def create_account_with_aliases(
        self,
        account_id: str,
        user_id: str,
        display_name: str,
        domains: str,
        aliases: dict[str, str],
    ) -> None:
        """INSERT L2 account and its aliases.

        SQL origin: discovery.approve_candidate() — new account creation branch.
        """
        with get_db_connection(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO kb_accounts
                    (id, user_id, display_name, layer, domains, active, added_by)
                VALUES (?, ?, ?, 2, ?, 1, 'agent')
                """,
                (account_id, user_id, display_name, domains),
            )
            for platform, platform_id in aliases.items():
                if platform_id:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO kb_account_aliases
                            (account_id, platform, platform_id, confidence, verified)
                        VALUES (?, ?, ?, 0.8, 0)
                        """,
                        (account_id, platform, platform_id),
                    )
            conn.commit()

    # ------------------------------------------------------------------
    # insert_discovered_via_relation
    # ------------------------------------------------------------------

    def insert_discovered_via_relation(self, from_id: str, to_id: str) -> None:
        """INSERT OR IGNORE a 'discovered_via' edge in kb_relations.

        SQL origin: discovery.approve_candidate() — relation wiring.
        """
        try:
            with get_db_connection(self._db_path) as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO kb_relations
                        (from_account_id, to_account_id, relation_type, weight, evidence_count)
                    VALUES (?, ?, 'discovered_via', 0.8, 1)
                    """,
                    (from_id, to_id),
                )
                conn.commit()
        except Exception:
            pass
