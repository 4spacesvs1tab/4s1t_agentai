#!/usr/bin/env python3
"""
Database Migration 012: Phase KB-3 tables.

Adds:
  kb_alert_matches  — per-chunk alert trigger log and NIP-17 delivery tracking

The kb_discovery_queue (migration 010) already stores L2 candidates with
mention_count; KB-3 entity extraction upserts directly into that table.

Design reference: KnowledgeBase_design.md §6.7 (alert engine), §6.5 (L2 discovery)
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "012"
MIGRATION_NAME = "kb_phase3"
MIGRATION_DESCRIPTION = "Phase KB-3: alert match log for semantic alert delivery"


def get_db_path() -> str:
    project_root = Path(__file__).parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


_SQL = """
-- Per-chunk alert trigger log (G11: semantic alert delivery)
-- One row per (alert, chunk) pair that exceeded similarity_threshold.
-- 'delivered' tracks NIP-17 dispatch status.
CREATE TABLE IF NOT EXISTS kb_alert_matches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id     TEXT NOT NULL REFERENCES kb_alerts(id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL DEFAULT 'default',
    chunk_id     TEXT NOT NULL,               -- ChromaDB chunk ID
    source_url   TEXT,
    account_id   TEXT,
    domain       TEXT,
    similarity   REAL NOT NULL,
    matched_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    delivered    INTEGER DEFAULT 0,           -- 0=pending, 1=sent via NIP-17
    delivered_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_kb_alert_matches_alert ON kb_alert_matches(alert_id);
CREATE INDEX IF NOT EXISTS idx_kb_alert_matches_user_delivered
    ON kb_alert_matches(user_id, delivered);
"""


def run_migration(db_path: Optional[str] = None) -> bool:
    """Run migration. Safe to re-run: uses CREATE TABLE IF NOT EXISTS."""
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
        print("  Tables created: kb_alert_matches")
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
