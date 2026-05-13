#!/usr/bin/env python3
"""
Database Migration 015: Conversation Memory + Chat Graph.

Phase KB-10: server-side conversation persistence and graph infrastructure.

New tables:
  conversations            — server-side registry of user conversations
                             (synced from client localStorage; no message content)
  conv_links               — directed graph edges between conversations
                             relation_type: continues | spawned_from | references |
                                            shares_context | contradicts
  kb_session_memory_consent — per-session memory opt-in (ephemeral consent)
  kb_user_facts            — persistent user facts (privacy layer, opt-in)
  kb_user_interests        — topic interest tracking with decay scoring

New columns on users:
  memory_scope             — 'off' | 'private' | 'family' | 'team'  (default: 'off')
  memory_retention_days    — integer, default 90
  default_language         — 'en' | 'pl' | ...  (default: 'en')

Design reference: KB_assistant_design_v2.md §11.1a, §4.4
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "015"
MIGRATION_NAME = "conversation_memory"
MIGRATION_DESCRIPTION = (
    "Conversation server-sync, conv_links graph, memory consent, "
    "user facts, user interests, memory_scope on users"
)


def get_db_path() -> str:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


_SQL = """
-- ── users: new columns for memory + language ─────────────────────────────────
-- Each ALTER is wrapped in a PRAGMA check so the migration is idempotent.

-- ── conversations: server-side registry ──────────────────────────────────────
-- Message content never stored here; stays in client localStorage.
-- Synced via POST /api/v1/conversations/sync on every chat save.
CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    -- client-generated format: 'conv_{timestamp}_{random}'
    user_id       TEXT NOT NULL,
    title         TEXT,
    -- first user message, ≤ 80 chars; set on first sync
    summary       TEXT,
    -- auto-generated 1-sentence summary (async, KB-12)
    model         TEXT,
    message_count INTEGER DEFAULT 0,
    memory_scope  TEXT    DEFAULT 'private',
    -- 'private' | 'family' | 'team' | 'off'
    domains       TEXT,
    -- JSON array e.g. ["macro","ba"]; set via add-to-KB action
    tags          TEXT,
    -- JSON array, user-defined
    is_root       INTEGER DEFAULT 1,
    -- 0 = spawned_from another via "Continue in new chat"
    in_kb         INTEGER DEFAULT 0,
    -- 1 = content added to KB collection
    started_at    TEXT    NOT NULL,
    last_active   TEXT    NOT NULL,
    expires_at    TEXT,
    -- NULL = follow user retention policy (memory_retention_days)
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_conv_user
    ON conversations(user_id, last_active DESC);
CREATE INDEX IF NOT EXISTS idx_conv_user_kb
    ON conversations(user_id, in_kb, last_active DESC);

-- ── conv_links: graph edges ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conv_links (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    target_id     TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    -- 'continues'      : direct follow-up, same topic, next session
    -- 'spawned_from'   : explicitly started via "Continue in new chat"
    -- 'references'     : cites ideas, data, or decisions from target
    -- 'shares_context' : same project/entity, not sequential
    -- 'contradicts'    : different conclusion on the same topic
    created_by    TEXT NOT NULL DEFAULT 'user',
    -- 'user' | 'system'
    created_at    TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES conversations(id) ON DELETE CASCADE,
    UNIQUE (source_id, target_id, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_conv_links_source ON conv_links(source_id);
CREATE INDEX IF NOT EXISTS idx_conv_links_target ON conv_links(target_id);

-- ── kb_session_memory_consent: per-session memory opt-in ─────────────────────
-- Ephemeral — rows expire when the session ends or expires_at passes.
CREATE TABLE IF NOT EXISTS kb_session_memory_consent (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    scope         TEXT NOT NULL,
    -- 'private' | 'family' | 'team'
    consented_at  TEXT NOT NULL,
    expires_at    TEXT,
    -- NULL = end of HTTP session
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_consent_user
    ON kb_session_memory_consent(user_id, expires_at);

-- ── kb_user_facts: persistent memory (opt-in per fact) ───────────────────────
-- Only populated after explicit user consent (KB-12).
-- PII scrub always runs before fact extraction — Tier 1 PII never stored.
CREATE TABLE IF NOT EXISTS kb_user_facts (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    fact_type     TEXT NOT NULL,
    -- 'preference' | 'professional' | 'contextual' | 'relationship'
    fact_key      TEXT NOT NULL,
    fact_value    TEXT NOT NULL,
    confidence    REAL    DEFAULT 1.0,
    source        TEXT    NOT NULL,
    -- 'explicit' | 'confirmed' | 'inferred'
    consent_level TEXT    NOT NULL,
    -- 'session' | 'private' | 'family' | 'team'
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    expires_at    TEXT,
    -- NULL = no expiry; set for session-scoped facts
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_user_facts_lookup
    ON kb_user_facts(user_id, fact_key);
CREATE INDEX IF NOT EXISTS idx_user_facts_expiry
    ON kb_user_facts(expires_at)
    WHERE expires_at IS NOT NULL;

-- ── kb_user_interests: topic interest with decay scoring ─────────────────────
-- Score computed on read via exponential decay (half-life 14 days).
-- See KB_assistant_design_v2.md §4.4 for compute_interest_score().
CREATE TABLE IF NOT EXISTS kb_user_interests (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    topic              TEXT NOT NULL,
    mention_timestamps TEXT NOT NULL DEFAULT '[]',
    -- JSON array of ISO timestamps
    last_mentioned     TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (user_id, topic)
);
CREATE INDEX IF NOT EXISTS idx_user_interests_score
    ON kb_user_interests(user_id, last_mentioned DESC);
"""


def _add_column_if_missing(cur: sqlite3.Cursor, table: str, column: str, definition: str) -> bool:
    """Add a column to a table if it does not already exist. Returns True if added."""
    cur.execute(f"PRAGMA table_info({table})")
    existing = [row[1] for row in cur.fetchall()]
    if column in existing:
        return False
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return True


def run_migration(db_path: Optional[str] = None) -> bool:
    path = db_path or get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {path}")

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    try:
        # ── users: new columns ────────────────────────────────────────────────
        added_cols = []
        for col, defn in [
            ("memory_scope",          "TEXT DEFAULT 'off'"),
            ("memory_retention_days", "INTEGER DEFAULT 90"),
            ("default_language",      "TEXT DEFAULT 'en'"),
        ]:
            if _add_column_if_missing(cur, "users", col, defn):
                added_cols.append(col)

        if added_cols:
            print(f"  Added columns to users: {', '.join(added_cols)}")
        else:
            print("  users columns already present — skipped.")

        # ── new tables ────────────────────────────────────────────────────────
        cur.executescript(_SQL)
        print("  Created tables: conversations, conv_links, "
              "kb_session_memory_consent, kb_user_facts, kb_user_interests")

        # ── migration history ─────────────────────────────────────────────────
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
