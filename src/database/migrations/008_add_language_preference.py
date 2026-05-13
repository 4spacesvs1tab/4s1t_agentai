"""
Database Migration 008: Add language_preference column to users table.

Adds to users table:
  language_preference TEXT NOT NULL DEFAULT 'en'

Supported values: 'en', 'pl' (see src/i18n/LANGUAGES for the full list).
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "008"
MIGRATION_NAME = "add_language_preference"
MIGRATION_DESCRIPTION = "Add language_preference column to users table with default 'en'"


def get_db_path() -> str:
    project_root = Path(__file__).parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


def run_migration(db_path: Optional[str] = None) -> bool:
    """
    Run migration. Safe to re-run: uses IF NOT EXISTS / duplicate-column guard.
    """
    path = db_path or get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {path}")

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    try:
        # --- language_preference ---
        try:
            cur.execute(
                "ALTER TABLE users ADD COLUMN language_preference TEXT NOT NULL DEFAULT 'en'"
            )
            print("  ✓ Added column: language_preference (default: 'en')")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("  ~ Column language_preference already exists, skipping")
            else:
                raise

        # --- migration history ---
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
