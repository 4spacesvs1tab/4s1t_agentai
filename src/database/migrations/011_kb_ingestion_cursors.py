#!/usr/bin/env python3
"""
Database Migration 011: KB Ingestion Cursors.

Adds the kb_ingestion_cursors table used by the ingestion runner (Phase KB-2)
to track the last successful ingestion timestamp per (user_id, account_id, platform).

This enables incremental / cursor-based ingestion:
  - On each scheduled run, adapters call get_new_since(cursor) instead of
    fetching the full backlog.
  - After a successful run, the cursor is advanced to the current UTC time.
  - On first run (no cursor), a full fetch is performed.

Design reference: KnowledgeBase_design.md §6.2 (G18 adapter coverage)
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "011"
MIGRATION_NAME = "kb_ingestion_cursors"
MIGRATION_DESCRIPTION = "Add kb_ingestion_cursors table for incremental adapter fetch"


def get_db_path() -> str:
    project_root = Path(__file__).parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


_SQL = """
-- Ingestion cursor: tracks last successful fetch time per account+platform
-- Used by the ingestion runner to call get_new_since() on subsequent runs.
CREATE TABLE IF NOT EXISTS kb_ingestion_cursors (
    user_id          TEXT NOT NULL DEFAULT 'default',
    account_id       TEXT NOT NULL,
    platform         TEXT NOT NULL,
    last_ingested_at TEXT NOT NULL,   -- ISO 8601 UTC timestamp
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, account_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_kb_cursors_user ON kb_ingestion_cursors(user_id);
"""


def run_migration(db_path: Optional[str] = None) -> bool:
    """
    Run migration. Safe to re-run: all tables use CREATE TABLE IF NOT EXISTS.
    """
    path = db_path or get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {path}")

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    try:
        cur.executescript(_SQL)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS migration_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_id   TEXT NOT NULL UNIQUE,
                migration_name TEXT NOT NULL,
                executed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description    TEXT
            )
        """)
        cur.execute(
            """
            INSERT OR REPLACE INTO migration_history
                (migration_id, migration_name, description, executed_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (MIGRATION_ID, MIGRATION_NAME, MIGRATION_DESCRIPTION),
        )

        conn.commit()
        print(f"\n✓ Migration {MIGRATION_ID} completed successfully.")
        print("  Tables created: kb_ingestion_cursors")
        return True

    except Exception as exc:
        conn.rollback()
        print(f"\n✗ Migration failed: {exc}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    db_arg = sys.argv[1] if len(sys.argv) > 1 else None
    success = run_migration(db_path=db_arg)
    sys.exit(0 if success else 1)
