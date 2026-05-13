#!/usr/bin/env python3
"""
get_kb_status skill handler.

Returns KB ingestion status: last ingestion timestamps per account/domain,
and counts of chunks ingested recently.

Input:  {"parameters": {"user_id": "...", "domain": "..."}}
Output: {"success": true, "result": {"last_ingested_at": "...", "accounts": [...], ...}}
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone

from core.db_path import get_db_path


def execute(params: dict) -> dict:
    user_id: str = params.get("user_id", "")
    domain_filter: str = params.get("domain", "")

    db_path = str(get_db_path())

    # If user_id not supplied by the agent, resolve from the DB (solo deployment).
    if not user_id:
        conn_tmp = sqlite3.connect(db_path)
        row = conn_tmp.execute("SELECT id FROM users LIMIT 1").fetchone()
        conn_tmp.close()
        if row:
            user_id = row[0]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Overall latest ingestion across all accounts for this user
        overall_row = conn.execute(
            """
            SELECT MAX(c.last_ingested_at) AS latest
            FROM kb_ingestion_cursors c
            JOIN kb_accounts a ON c.account_id = a.id
            WHERE a.user_id = ?
            """,
            (user_id,),
        ).fetchone()
        last_ingested_at = overall_row["latest"] if overall_row else None

        # Per-account breakdown (optionally filtered by domain)
        query = """
            SELECT
                a.account_id,
                a.display_name,
                a.platform,
                a.domains,
                c.platform  AS cursor_platform,
                c.last_ingested_at
            FROM kb_ingestion_cursors c
            JOIN kb_accounts a ON c.account_id = a.id
            WHERE a.user_id = ?
        """
        args: list = [user_id]
        if domain_filter:
            query += " AND (a.domains = ? OR a.domains LIKE ? OR a.domains LIKE ? OR a.domains LIKE ?)"
            args += [
                domain_filter,
                f"{domain_filter}|%",
                f"%|{domain_filter}",
                f"%|{domain_filter}|%",
            ]
        query += " ORDER BY c.last_ingested_at DESC LIMIT 50"
        rows = conn.execute(query, args).fetchall()

        accounts = [
            {
                "account_id": r["account_id"],
                "display_name": r["display_name"] or r["account_id"],
                "platform": r["platform"],
                "domains": r["domains"],
                "last_ingested_at": r["last_ingested_at"],
            }
            for r in rows
        ]

        # Recent chunk count (last 7 days) from kb_ingestion_log
        recent_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM kb_ingestion_log l
            JOIN kb_accounts a ON l.account_id = a.id
            WHERE a.user_id = ?
              AND l.ingested_at >= datetime('now', '-7 days')
            """,
            (user_id,),
        ).fetchone()
        chunks_last_7d = recent_row["cnt"] if recent_row else 0

    finally:
        conn.close()

    now_iso = datetime.now(timezone.utc).isoformat()

    return {
        "checked_at": now_iso,
        "last_ingested_at": last_ingested_at,
        "chunks_ingested_last_7d": chunks_last_7d,
        "accounts": accounts,
        "domain_filter": domain_filter or None,
    }


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: handler.py input.json output.json", file=sys.stderr)
        sys.exit(1)
    input_path, output_path = sys.argv[1], sys.argv[2]
    try:
        data = json.loads(open(input_path).read())
        params = data.get("parameters", {})
        result = execute(params)
        output = {"success": True, "result": result, "error": None, "logs": []}
    except Exception as exc:
        output = {"success": False, "result": None, "error": str(exc), "logs": []}
    with open(output_path, "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()
