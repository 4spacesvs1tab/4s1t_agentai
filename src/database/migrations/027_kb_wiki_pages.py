#!/usr/bin/env python3
"""
Database Migration 027: Personal Wiki Pages.

Phase KB-23: kb_wiki_pages table — persistent, on-demand topic reference pages
synthesised from KB content via a single LLM call.

New tables:
  kb_wiki_pages  — one row per (user_id, topic slug); versioned markdown content

Design reference: KB_assistant_design_v2.md §17 KB-23
"""
import sqlite3
import sys
from pathlib import Path

MIGRATION_ID = "027"
MIGRATION_NAME = "kb_wiki_pages"
MIGRATION_DESCRIPTION = (
    "KB-23 Personal Wiki Pages: kb_wiki_pages table for on-demand topic "
    "reference pages synthesised from KB content"
)


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kb_wiki_pages (
                id             TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL,
                topic          TEXT NOT NULL,   -- normalised slug, e.g. 'fed-rate-policy'
                title          TEXT NOT NULL,   -- human-readable title
                content        TEXT NOT NULL,   -- markdown body
                source_chunks  TEXT NOT NULL DEFAULT '[]',  -- JSON list of chunk IDs used
                version        INTEGER NOT NULL DEFAULT 1,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                UNIQUE(user_id, topic)
            );

            CREATE INDEX IF NOT EXISTS idx_wiki_pages_user
                ON kb_wiki_pages(user_id);

            CREATE INDEX IF NOT EXISTS idx_wiki_pages_updated
                ON kb_wiki_pages(user_id, updated_at DESC);
        """)
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
