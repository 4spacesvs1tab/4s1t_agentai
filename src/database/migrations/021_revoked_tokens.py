#!/usr/bin/env python3
"""
Database Migration 021: Persistent JWT Token Revocation.

Phase KB-26-F: replaces the in-memory JTI blocklist in security_dependencies.py
with a SQLite-backed table.  This closes the gap where service restarts caused
the in-memory blocklist to be lost, briefly allowing replayed tokens.

New table:
  revoked_tokens  — stores revoked JTIs until their expiry timestamp, after
                    which they are cleaned up by the KB scheduler daily tick.

Design reference: KB_assistant_design_v2.md §22.6 action KB-26-F
"""
import sqlite3
import sys
from pathlib import Path

MIGRATION_ID = "021"
MIGRATION_NAME = "revoked_tokens"
MIGRATION_DESCRIPTION = (
    "Add revoked_tokens table for persistent JWT JTI revocation (KB-26-F)"
)


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS revoked_tokens (
                jti        TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL   -- ISO-8601; row prunable after this
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires "
            "ON revoked_tokens(expires_at)"
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
