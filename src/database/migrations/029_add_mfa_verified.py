#!/usr/bin/env python3
"""
Database Migration 029: Add mfa_verified column to user_mfa.

The create_user() method inserts mfa_verified=0 for every new user, but this
column was added to the connection.py schema without a corresponding ALTER TABLE
migration. Existing deployments are missing the column, causing a
sqlite3.OperationalError on every registration attempt (surfaced as HTTP 503).

Also removes orphaned users rows (users with no matching user_mfa record) that
were created by failed registration attempts before this fix.
"""
import sqlite3
import sys
from pathlib import Path

MIGRATION_ID = "029"
MIGRATION_NAME = "add_mfa_verified"
MIGRATION_DESCRIPTION = (
    "Add user_mfa.mfa_verified column; remove orphaned users with no user_mfa record"
)


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(user_mfa)").fetchall()
        }

        if "mfa_verified" not in cols:
            conn.execute(
                "ALTER TABLE user_mfa ADD COLUMN mfa_verified BOOLEAN NOT NULL DEFAULT 0"
            )
            print("  + user_mfa.mfa_verified")
        else:
            print("  user_mfa.mfa_verified already exists, skipping")

        # Remove users that have no user_mfa record (orphaned by failed registrations)
        result = conn.execute(
            "DELETE FROM users WHERE id NOT IN (SELECT user_id FROM user_mfa)"
        )
        if result.rowcount:
            print(f"  removed {result.rowcount} orphaned user row(s) with no user_mfa record")

        conn.commit()
        print(f"Migration {MIGRATION_ID} ({MIGRATION_NAME}) applied.")

    except Exception as exc:
        conn.rollback()
        print(f"Migration {MIGRATION_ID} FAILED: {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


def get_db_path() -> str:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else get_db_path()
    run(db)
