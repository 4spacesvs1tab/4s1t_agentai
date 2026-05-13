#!/usr/bin/env python3
"""
Database Migration 016: KB Account Consolidation.

Adds consolidated_into TEXT column to kb_accounts so that when duplicate
entities are merged, the secondary accounts can reference the primary account
they were consolidated into. This enables audit trail and prevents re-discovery.
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "016"
MIGRATION_NAME = "kb_consolidation"
MIGRATION_DESCRIPTION = "Add consolidated_into column to kb_accounts for entity consolidation audit trail"


def get_db_path() -> str:
    project_root = Path(__file__).parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


def run_migration(db_path: Optional[str] = None) -> bool:
    path = db_path or get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {path}")

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    try:
        cur.execute("PRAGMA table_info(kb_accounts)")
        cols = [row[1] for row in cur.fetchall()]
        if "consolidated_into" in cols:
            print("  consolidated_into column already exists — skipping ALTER TABLE.")
        else:
            cur.execute(
                "ALTER TABLE kb_accounts ADD COLUMN consolidated_into TEXT DEFAULT NULL"
            )
            print("  Added consolidated_into column to kb_accounts.")

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
