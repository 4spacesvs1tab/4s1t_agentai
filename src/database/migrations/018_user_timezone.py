#!/usr/bin/env python3
"""
Database Migration 018: users.user_timezone.

Adds a user_timezone column so agents always know the user's local IANA
timezone without having to ask.  Used by:
  - UserProfile.to_system_prompt_snippet() — injected into every agent prompt
  - schedule_reminder skill — passed as user_timezone so dateparser interprets
    bare times ("tomorrow at 7:00") in the correct local timezone

Default: 'Europe/Warsaw' (the known deployment timezone).

Design reference: KB_assistant_design_v2.md §7.2 (UserProfile)
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "018"
MIGRATION_NAME = "user_timezone"
MIGRATION_DESCRIPTION = "Add users.user_timezone (IANA TZ string, default Europe/Warsaw)"


def get_db_path() -> str:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


def run_migration(db_path: Optional[str] = None) -> bool:
    path = db_path or get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {path}")

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    try:
        cur.execute("PRAGMA table_info(users)")
        existing = [row[1] for row in cur.fetchall()]
        if "user_timezone" not in existing:
            cur.execute(
                "ALTER TABLE users ADD COLUMN user_timezone TEXT DEFAULT 'Europe/Warsaw'"
            )
            print("  Added column users.user_timezone (default: Europe/Warsaw)")
        else:
            print("  users.user_timezone already present — skipped.")

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
            "INSERT OR REPLACE INTO migration_history "
            "(migration_id, migration_name, description, executed_at) "
            "VALUES (?, ?, ?, datetime('now'))",
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
