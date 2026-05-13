"""
SqliteAccountRepository — SQLite implementation of AccountRepository.

All SQL is moved verbatim from kb/social_graph.py and kb API routes.
No domain logic lives here.
"""
from typing import Any, Optional

from kb.ports.account_repository import AccountRepository
from kb.social_graph import KBAccount

from infrastructure.sqlite._connection import get_db_connection


class SqliteAccountRepository(AccountRepository):
    """Reads and writes kb_accounts, kb_account_aliases, and kb_relations."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # find_active_by_user
    # ------------------------------------------------------------------

    def find_active_by_user(self, user_id: str) -> list[KBAccount]:
        """Return all active accounts with aliases for *user_id*.

        SQL origin: social_graph.load() — kb_accounts SELECT + kb_account_aliases SELECT.
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, user_id, display_name, layer, domains, active, added_by
                    FROM kb_accounts
                    WHERE user_id = ? AND active = 1
                    """,
                    (user_id,),
                )
                rows = cur.fetchall()

                accounts: dict[str, KBAccount] = {}
                for row in rows:
                    acc = KBAccount(
                        id=row["id"],
                        user_id=row["user_id"],
                        display_name=row["display_name"],
                        layer=row["layer"],
                        domains=row["domains"],
                        active=bool(row["active"]),
                        added_by=row["added_by"] or "user",
                    )
                    accounts[acc.id] = acc

                if accounts:
                    placeholders = ",".join("?" * len(accounts))
                    cur.execute(
                        f"""
                        SELECT account_id, platform, platform_id
                        FROM kb_account_aliases
                        WHERE account_id IN ({placeholders})
                        """,
                        list(accounts.keys()),
                    )
                    for alias_row in cur.fetchall():
                        acc_id = alias_row["account_id"]
                        if acc_id in accounts:
                            accounts[acc_id].aliases[alias_row["platform"]] = alias_row["platform_id"]

                return list(accounts.values())
        except Exception:
            return []

    # ------------------------------------------------------------------
    # find_relations_by_account_ids
    # ------------------------------------------------------------------

    def find_relations_by_account_ids(
        self, account_ids: list[str]
    ) -> list[tuple[str, str, str, float]]:
        """Return (from_id, to_id, relation_type, weight) for edges involving *account_ids*.

        SQL origin: social_graph.load() — kb_relations SELECT.
        """
        if not account_ids:
            return []
        placeholders = ",".join("?" * len(account_ids))
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT from_account_id, to_account_id, relation_type, weight
                    FROM kb_relations
                    WHERE from_account_id IN ({p}) OR to_account_id IN ({p})
                    """.format(p=placeholders),
                    account_ids * 2,
                )
                rows = cur.fetchall()
                return [
                    (r["from_account_id"], r["to_account_id"], r["relation_type"], r["weight"])
                    for r in rows
                ]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # save
    # ------------------------------------------------------------------

    def save(self, account: KBAccount) -> None:
        """Insert account and aliases into the DB.

        SQL origin: social_graph.add_account() — kb_accounts INSERT + kb_account_aliases INSERT.
        """
        with get_db_connection(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO kb_accounts (id, user_id, display_name, layer, domains, active, added_by)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (account.id, account.user_id, account.display_name,
                 account.layer, account.domains, account.added_by),
            )
            for platform, platform_id in account.aliases.items():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO kb_account_aliases
                        (account_id, platform, platform_id, confidence, verified)
                    VALUES (?, ?, ?, 1.0, 0)
                    """,
                    (account.id, platform, platform_id),
                )
            conn.commit()

    # ------------------------------------------------------------------
    # upsert_relation
    # ------------------------------------------------------------------

    def upsert_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: str,
        weight: float,
    ) -> None:
        """Insert or strengthen a kb_relations edge.

        SQL origin: social_graph.add_relation() — SELECT + UPDATE/INSERT.
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    """
                    SELECT id, evidence_count FROM kb_relations
                    WHERE from_account_id = ? AND to_account_id = ? AND relation_type = ?
                    LIMIT 1
                    """,
                    (from_id, to_id, relation_type),
                )
                row = cur.fetchone()
                if row:
                    conn.execute(
                        """
                        UPDATE kb_relations
                        SET evidence_count = evidence_count + 1,
                            weight = MIN(1.0, weight + 0.1),
                            last_seen = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (row[0],),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO kb_relations
                            (from_account_id, to_account_id, relation_type, weight, evidence_count)
                        VALUES (?, ?, ?, ?, 1)
                        """,
                        (from_id, to_id, relation_type, weight),
                    )
                conn.commit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # find_filtered  (E3 — account_service.list_accounts)
    # ------------------------------------------------------------------

    def find_filtered(
        self,
        user_id: str,
        layer: Optional[int] = None,
        domain: Optional[str] = None,
    ) -> list[dict]:
        """Return active accounts with parsed aliases dict and domains_list.

        SQL origin: account_routes.list_kb_accounts — SELECT a.* with aliases join.
        """
        try:
            with get_db_connection(self._db_path) as conn:
                query = (
                    "SELECT a.*, GROUP_CONCAT(al.platform || ':' || al.platform_id) AS aliases_raw "
                    "FROM kb_accounts a "
                    "LEFT JOIN kb_account_aliases al ON a.id = al.account_id "
                    "WHERE a.user_id = ? AND a.active = 1"
                )
                params: list[Any] = [user_id]

                if layer is not None:
                    query += " AND a.layer = ?"
                    params.append(layer)
                if domain:
                    query += " AND a.domains LIKE ?"
                    params.append(f"%{domain}%")

                query += " GROUP BY a.id ORDER BY a.layer, a.display_name"

                cur = conn.execute(query, params)
                rows = [dict(r) for r in cur.fetchall()]

            for row in rows:
                raw = row.pop("aliases_raw", None) or ""
                aliases: dict[str, str] = {}
                for pair in raw.split(","):
                    if ":" in pair:
                        plat, pid = pair.split(":", 1)
                        aliases[plat] = pid
                row["aliases"] = aliases
                row["domains_list"] = [d for d in (row.get("domains") or "").split("|") if d]

            return rows
        except Exception:
            return []

    # ------------------------------------------------------------------
    # deactivate  (E3 — account_service.remove_account)
    # ------------------------------------------------------------------

    def deactivate(self, account_id: str, user_id: str) -> bool:
        """Soft-delete: sets active=0.  Returns True if a row was changed.

        SQL origin: account_routes.deactivate_kb_account.
        """
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    "UPDATE kb_accounts SET active = 0 WHERE id = ? AND user_id = ?",
                    (account_id, user_id),
                )
                conn.commit()
                return cur.rowcount > 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # update_fields  (E3 — account_service.update_account)
    # ------------------------------------------------------------------

    def update_fields(
        self,
        account_id: str,
        user_id: str,
        fields: dict[str, Any],
    ) -> bool:
        """Update arbitrary kb_accounts columns.  Returns True if a row was changed.

        SQL origin: account_routes.update_kb_account.
        """
        if not fields:
            return True
        try:
            with get_db_connection(self._db_path) as conn:
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                values = list(fields.values()) + [account_id, user_id]
                cur = conn.execute(
                    f"UPDATE kb_accounts SET {set_clause} WHERE id = ? AND user_id = ?",
                    values,
                )
                conn.commit()
                return cur.rowcount > 0
        except Exception:
            return False
