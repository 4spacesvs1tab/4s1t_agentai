#!/usr/bin/env python3
"""
Database Migration 023: Source Reliability + Citation Log.

Phase KB-16: data layer for source reliability scoring and citation tracking.

New tables:
  kb_source_reliability — per-account reliability scores (contradiction_rate,
                          activity_score, citation_rate, overall_score)
  kb_citation_log       — append-only log of account citations from
                          knowledge_base_search results; feeds citation_rate

Design reference: KB_assistant_design_v2.md §12.2
"""
import sqlite3
import sys
from pathlib import Path

MIGRATION_ID = "023"
MIGRATION_NAME = "kb_source_reliability"
MIGRATION_DESCRIPTION = (
    "KB-16 Source Reliability: kb_source_reliability and kb_citation_log tables"
)


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kb_source_reliability (
                account_id          TEXT PRIMARY KEY,
                contradiction_rate  REAL DEFAULT 0.5,   -- fraction chunks contradicted; lower is better
                activity_score      REAL DEFAULT 0.5,   -- relative ingestion activity last 30d
                citation_rate       REAL DEFAULT 0.5,   -- relative citation frequency in search results
                prediction_accuracy REAL,               -- NULL until KB-15 predictions accumulate
                overall_score       REAL DEFAULT 0.5,   -- mean of available signals
                last_updated        TEXT NOT NULL,       -- ISO-8601 UTC
                sample_size         INTEGER DEFAULT 0,  -- chunks used to compute contradiction_rate
                FOREIGN KEY (account_id) REFERENCES kb_accounts(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS kb_citation_log (
                id          TEXT PRIMARY KEY,
                account_id  TEXT NOT NULL,
                user_id     TEXT NOT NULL,
                cited_at    TEXT NOT NULL,   -- ISO-8601 UTC
                query_text  TEXT DEFAULT ''  -- first 200 chars of the query that produced the citation
            )
        """)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_citation_log_account "
            "ON kb_citation_log(account_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_citation_log_user "
            "ON kb_citation_log(user_id, cited_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_reliability_score "
            "ON kb_source_reliability(overall_score)"
        )

        conn.commit()
        print(f"[{MIGRATION_ID}] {MIGRATION_NAME}: OK")
    except Exception as exc:
        conn.rollback()
        print(f"[{MIGRATION_ID}] {MIGRATION_NAME}: FAILED — {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        default = Path(__file__).resolve().parent.parent.parent.parent / "data" / "agent.db"
        db_path = str(default)
    else:
        db_path = sys.argv[1]
    run(db_path)
