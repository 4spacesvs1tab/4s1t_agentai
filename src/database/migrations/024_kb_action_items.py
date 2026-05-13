#!/usr/bin/env python3
"""
Database Migration 024: Action Item Inbox.

Phase KB-17: nightly urgency-keyword pre-filter + LLM batch extraction
writes actionable items extracted from ingested content into kb_action_items.
The web UI exposes them at /kb/inbox; a sidebar badge shows unread count.

New table:
  kb_action_items — one row per extracted action item; supports pending /
                    done / dismissed lifecycle managed from the inbox UI.

Design reference: KB_assistant_design_v2.md §12.6
"""
import sqlite3
import sys
from pathlib import Path

MIGRATION_ID = "024"
MIGRATION_NAME = "kb_action_items"
MIGRATION_DESCRIPTION = (
    "KB-17 Action Item Inbox: kb_action_items table with urgency and "
    "lifecycle status for the /kb/inbox web UI"
)


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kb_action_items (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                source_chunk_id TEXT,
                source_account  TEXT,
                domain          TEXT,
                action_text     TEXT NOT NULL,
                urgency         TEXT NOT NULL DEFAULT 'normal',
                    -- 'high' | 'normal' | 'low'
                context_snippet TEXT,
                extracted_at    TEXT NOT NULL,  -- ISO-8601 UTC
                status          TEXT NOT NULL DEFAULT 'pending',
                    -- 'pending' | 'done' | 'dismissed'
                updated_at      TEXT NOT NULL,
                FOREIGN KEY (source_account) REFERENCES kb_accounts(id)
            )
        """)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_action_items_user_status "
            "ON kb_action_items(user_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_action_items_extracted "
            "ON kb_action_items(extracted_at)"
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
