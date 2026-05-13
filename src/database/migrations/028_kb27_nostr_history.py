#!/usr/bin/env python3
"""
Database Migration 028: NIP-17 Global History (KB-27).

Adds server-side persistence for Nostr NIP-17 conversation sessions.

New columns on conversations:
  source       — 'webui' | 'nip17' | 'api'  (default: 'webui')
  nostr_npub   — sender npub for NIP-17-originated conversations

New column on nostr_contacts:
  user_id      — FK to users.id (for multi-user deployments)

All NIP-17 exchanges are now stored in conversation_messages with
source='nip17'. Session boundaries use a 4-hour gap heuristic.
The in-memory _nip17_histories dict in main.py is kept as a write-through
cache; the DB is authoritative.

Design reference: KB_assistant_design_v2.md §17 (KB-27)
"""
import sqlite3
import sys
from pathlib import Path

MIGRATION_ID = "028"
MIGRATION_NAME = "kb27_nostr_history"
MIGRATION_DESCRIPTION = (
    "Add source + nostr_npub to conversations; user_id to nostr_contacts "
    "(KB-27 NIP-17 global history)"
)


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # ── conversations: source column ──────────────────────────────────────
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
        }

        if "source" not in cols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN source TEXT DEFAULT 'webui'"
            )
            print("  + conversations.source")

        if "nostr_npub" not in cols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN nostr_npub TEXT"
            )
            print("  + conversations.nostr_npub")

        # ── nostr_contacts: user_id column ────────────────────────────────────
        nc_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(nostr_contacts)").fetchall()
        }
        if "user_id" not in nc_cols:
            conn.execute(
                "ALTER TABLE nostr_contacts ADD COLUMN user_id TEXT REFERENCES users(id)"
            )
            print("  + nostr_contacts.user_id")

        # ── index: find NIP-17 conversations quickly ──────────────────────────
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_nip17 "
            "ON conversations(nostr_npub, last_active DESC) "
            "WHERE source = 'nip17'"
        )

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
