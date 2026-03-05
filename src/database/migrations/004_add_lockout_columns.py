"""
Database Migration 004: Add per-user login lockout columns (P3-3).

Adds to the users table:
  failed_login_count INTEGER NOT NULL DEFAULT 0
  locked_until       TEXT    (ISO 8601 UTC timestamp; NULL = not locked)

Design: Design_aiAgentOrchestrationOfMany.md §2.3 FR-21
  - Lock after 10 consecutive failed logins
  - Lockout duration: 15 minutes
  - Same HTTP 401 message for "user not found" and "wrong password" (anti-enumeration)
  - HTTP 423 + Retry-After header when account is locked
  - Auto-reset on successful login
"""
import sqlite3
from pathlib import Path

MIGRATION_ID = "004"
MIGRATION_NAME = "add_lockout_columns"
MIGRATION_DESCRIPTION = "Add failed_login_count and locked_until to users table (P3-3)"


def get_db_path() -> str:
    project_root = Path(__file__).parent.parent.parent.parent
    # Container volume: ./data:/app/data  →  DB lives at data/agent.db
    return str(project_root / "data" / "agent.db")


def run_migration(db_path: str | None = None) -> bool:
    """
    Run the migration. Safe to re-run: uses ALTER TABLE … ADD COLUMN IF NOT EXISTS
    pattern via try/except (SQLite does not support IF NOT EXISTS on ALTER).
    """
    path = db_path or get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {path}")

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    try:
        # --- failed_login_count ---
        try:
            cur.execute(
                "ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0"
            )
            print("  ✓ Added column: failed_login_count")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("  — Column already exists: failed_login_count (skipping)")
            else:
                raise

        # --- locked_until ---
        try:
            cur.execute("ALTER TABLE users ADD COLUMN locked_until TEXT")
            print("  ✓ Added column: locked_until")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print("  — Column already exists: locked_until (skipping)")
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
