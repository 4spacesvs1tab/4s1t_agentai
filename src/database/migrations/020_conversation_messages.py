#!/usr/bin/env python3
"""
Database Migration 020: Conversation Message Sync.

Phase KB-25: makes the server the authoritative store for conversation
message content. localStorage becomes a write-through cache.

New table:
  conversation_messages  — full message content per conversation, with a
                           token-stripped content_ctx column for LLM context.

New columns on conversations:
  fork_parent_id  — parent conv_id when this conversation was forked
  fork_seq        — the parent message seq at which the fork was made (inclusive)

Design reference: KB_assistant_design_v2.md §21.3
"""
import sqlite3
import sys
from pathlib import Path

MIGRATION_ID = "020"
MIGRATION_NAME = "conversation_messages"
MIGRATION_DESCRIPTION = (
    "Add conversation_messages table and fork columns on conversations "
    "(KB-25 cross-device message sync)"
)


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # ── conversation_messages ─────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id          TEXT PRIMARY KEY,
                conv_id     TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content     TEXT NOT NULL,
                content_ctx TEXT,
                model       TEXT,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                FOREIGN KEY (conv_id) REFERENCES conversations(id) ON DELETE CASCADE,
                UNIQUE (conv_id, seq)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cmsg_conv_seq "
            "ON conversation_messages(conv_id, seq)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cmsg_expires "
            "ON conversation_messages(expires_at)"
        )

        # ── fork columns on conversations ─────────────────────────────────────
        # ALTER TABLE IF NOT EXISTS is not supported in older SQLite; check first.
        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "fork_parent_id" not in existing_cols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN "
                "fork_parent_id TEXT REFERENCES conversations(id)"
            )
        if "fork_seq" not in existing_cols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN fork_seq INTEGER"
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
