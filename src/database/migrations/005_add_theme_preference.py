"""
Database Migration 005: Add theme_preference column to users table.

Adds to users table:
  theme_preference TEXT NOT NULL DEFAULT 'terminal'

Design: User-selectable theme system with per-user persistence
  - Default theme: 'terminal' (green on black)
  - Available themes: 'terminal', 'dark_grey_technical', 'teal_modern', 'blue_professional'
  - Theme preference stored per user in database
  - Theme applied on login via data-theme attribute in base.html
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "005"
MIGRATION_NAME = "add_theme_preference"
MIGRATION_DESCRIPTION = "Add theme_preference column to users table with default 'terminal'"


def get_db_path() -> str:
    project_root = Path(__file__).parent.parent.parent.parent
    # Container volume: ./data:/app/data  → DB lives at data/agent.db
    return str(project_root / "data" / "agent.db")


def run_migration(db_path: Optional[str] = None) -> bool:
    """
    Run migration. Safe to re-run: uses ALTER TABLE … ADD COLUMN IF NOT EXISTS
    pattern via try/except (SQLite does not support IF NOT EXISTS on ALTER).
    """
    path = db_path or get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {path}")

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    try:
        # --- theme_preference ---
        try:
            cur.execute(
                "ALTER TABLE users ADD COLUMN theme_preference TEXT NOT NULL DEFAULT 'terminal'"
            )
            print("  ✓ Added column: theme_preference (default: 'terminal')")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("  — Column already exists: theme_preference (skipping)")
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
