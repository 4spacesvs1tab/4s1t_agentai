#!/usr/bin/env python3
"""
Database Migration 013: KB Schedule Days.

Adds brief_days column to kb_user_config for per-domain day-of-week scheduling.
brief_days stores a JSON array of day abbreviations, e.g. ["mon","wed","fri"].
NULL means "all days" (respects brief_frequency for daily/weekly logic).

Design: Phase KB-WebUI (G22 extension).
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "013"
MIGRATION_NAME = "kb_schedule_days"
MIGRATION_DESCRIPTION = "Add brief_days to kb_user_config for day-of-week schedule control"


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
        # Check if column already exists (ALTER TABLE fails if it does)
        cur.execute("PRAGMA table_info(kb_user_config)")
        cols = [row[1] for row in cur.fetchall()]
        if "brief_days" in cols:
            print("  brief_days column already exists — skipping ALTER TABLE.")
        else:
            cur.execute("ALTER TABLE kb_user_config ADD COLUMN brief_days TEXT DEFAULT NULL")

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
        print("  Added: kb_user_config.brief_days (TEXT, nullable JSON array)")
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
