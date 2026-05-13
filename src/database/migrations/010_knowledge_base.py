#!/usr/bin/env python3
"""
Database Migration 010: Knowledge Base tables.

Creates all KB tables for Phase KB-1:
  kb_accounts          — tracked content sources (social graph nodes, L1/L2)
  kb_account_aliases   — cross-platform identity mapping per account
  kb_relations         — social graph edges with type and weight
  kb_ingestion_log     — per-item ingestion tracking and dedup
  kb_discovery_queue   — L2 candidates awaiting user approval
  kb_alerts            — persistent semantic alert subscriptions (G11)
  kb_user_config       — per-user brief preferences (G22)
  kb_briefs            — generated brief history cache (G2)
  kb_snapshots         — longitudinal topic snapshots (G28)

Design reference: KnowledgeBase_design.md §7
User-isolation note (G27): all tables include user_id; single-user deployments
use the implicit default 'default', allowing seamless multi-user migration later.
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "010"
MIGRATION_NAME = "knowledge_base"
MIGRATION_DESCRIPTION = "Create Knowledge Base tables (accounts, ingestion, graph, alerts, briefs)"


def get_db_path() -> str:
    project_root = Path(__file__).parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


_SQL = """
-- ============================================================
-- USER ISOLATION NOTE (G27)
-- All KB tables include user_id for multi-user isolation.
-- knowledge_base_search always filters by calling user's ID.
-- Default value 'default' ensures single-user deployments work
-- without any code changes.
-- ============================================================

-- Account registry (social graph nodes)
CREATE TABLE IF NOT EXISTS kb_accounts (
    id           TEXT PRIMARY KEY,                     -- internal UUID
    user_id      TEXT NOT NULL DEFAULT 'default',      -- G27: user isolation
    display_name TEXT NOT NULL,
    layer        INTEGER NOT NULL DEFAULT 1,           -- 1=manual, 2=approved, 3=pending
    domains      TEXT NOT NULL,                        -- pipe-separated domain IDs: "domain_a|domain_b"
    active       INTEGER DEFAULT 1,
    added_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    added_by     TEXT DEFAULT 'user'                   -- 'user' | 'agent'
);
CREATE INDEX IF NOT EXISTS idx_kb_accounts_user ON kb_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_kb_accounts_user_active ON kb_accounts(user_id, active);

-- Cross-platform identity mapping
CREATE TABLE IF NOT EXISTS kb_account_aliases (
    account_id   TEXT NOT NULL REFERENCES kb_accounts(id) ON DELETE CASCADE,
    platform     TEXT NOT NULL,                        -- 'nostr','twitter','youtube','podcast','website','rumble'
    platform_id  TEXT NOT NULL,                        -- npub, @handle, channel_id, feed_url, url
    confidence   REAL DEFAULT 1.0,                     -- 0.0-1.0; agent-discovered links start lower
    verified     INTEGER DEFAULT 0,                    -- 0=unverified, 1=manually verified by user
    PRIMARY KEY (account_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_kb_aliases_account ON kb_account_aliases(account_id);

-- Social graph edges (weighted, typed)
CREATE TABLE IF NOT EXISTS kb_relations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    from_account_id  TEXT NOT NULL REFERENCES kb_accounts(id) ON DELETE CASCADE,
    to_account_id    TEXT NOT NULL REFERENCES kb_accounts(id) ON DELETE CASCADE,
    relation_type    TEXT NOT NULL,                    -- co-host, frequently-mentions, responds-to, appears-in-podcast, re-tweets, nostr-follows, cited-by, contradicts
    weight           REAL DEFAULT 1.0,                 -- 0.0-1.0, decays over time
    evidence_count   INTEGER DEFAULT 1,                -- number of observations
    first_seen       DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen        DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_kb_relations_from ON kb_relations(from_account_id);
CREATE INDEX IF NOT EXISTS idx_kb_relations_to ON kb_relations(to_account_id);

-- Ingestion tracking (G23: exact hash dedup; status tracking)
CREATE TABLE IF NOT EXISTS kb_ingestion_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL DEFAULT 'default',    -- G27
    account_id     TEXT REFERENCES kb_accounts(id) ON DELETE SET NULL,
    platform       TEXT NOT NULL,                      -- 'website','twitter','nostr','youtube','podcast','rumble','manual'
    item_url       TEXT,
    item_hash      TEXT,                               -- SHA256 of content for exact dedup
    ingested_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    chunk_count    INTEGER,
    ingestion_type TEXT DEFAULT 'scheduled',           -- 'scheduled' | 'manual' (G21)
    status         TEXT DEFAULT 'ok'                   -- ok, error, skipped, dedup_skipped
);
CREATE INDEX IF NOT EXISTS idx_kb_ingestion_user_account ON kb_ingestion_log(user_id, account_id);
CREATE INDEX IF NOT EXISTS idx_kb_ingestion_hash ON kb_ingestion_log(item_hash);

-- Layer-2 discovery queue (pending user approval)
CREATE TABLE IF NOT EXISTS kb_discovery_queue (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT NOT NULL DEFAULT 'default', -- G27
    candidate_name     TEXT NOT NULL,
    candidate_handles  TEXT,                            -- JSON: {"platform": "handle"}
    discovered_via     TEXT,                            -- account_id that led to discovery
    evidence           TEXT,                            -- JSON list of content URLs
    mention_count      INTEGER DEFAULT 1,
    discovery_source   TEXT DEFAULT 'ingestion',        -- 'ingestion' | 'web_research' (G25)
    rationale          TEXT,                            -- agent-supplied reasoning (G25)
    status             TEXT DEFAULT 'pending',          -- pending, approved, rejected
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    reviewed_at        DATETIME
);
CREATE INDEX IF NOT EXISTS idx_kb_discovery_user_status ON kb_discovery_queue(user_id, status);

-- Persistent semantic alert subscriptions (G11)
CREATE TABLE IF NOT EXISTS kb_alerts (
    id                   TEXT PRIMARY KEY,              -- UUID
    user_id              TEXT NOT NULL DEFAULT 'default',
    query                TEXT NOT NULL,                 -- alert query string
    query_embedding      BLOB,                          -- pre-computed embedding for fast comparison
    domain_filter        TEXT,                          -- JSON array of domains, NULL = all
    account_filter       TEXT,                          -- JSON array of account IDs, NULL = all
    similarity_threshold REAL DEFAULT 0.85,
    active               INTEGER DEFAULT 1,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_triggered_at    DATETIME
);
CREATE INDEX IF NOT EXISTS idx_kb_alerts_user_active ON kb_alerts(user_id, active);

-- Per-user KB and brief configuration (G22)
CREATE TABLE IF NOT EXISTS kb_user_config (
    user_id             TEXT NOT NULL,
    domain              TEXT NOT NULL,                  -- knowledge domain ID
    brief_enabled       INTEGER DEFAULT 1,
    brief_frequency     TEXT DEFAULT 'daily',           -- 'daily' | 'weekly' | 'disabled'
    brief_time          TEXT DEFAULT '07:00',            -- HH:MM UTC
    brief_min_items     INTEGER DEFAULT 3,              -- G26: minimum items before generating brief
    brief_extend_factor INTEGER DEFAULT 2,              -- G26: multiply window by this if below min_items
    PRIMARY KEY (user_id, domain)
);

-- Brief history cache (G2)
CREATE TABLE IF NOT EXISTS kb_briefs (
    id              TEXT PRIMARY KEY,                   -- UUID
    user_id         TEXT NOT NULL DEFAULT 'default',
    domain          TEXT NOT NULL,
    frequency       TEXT NOT NULL,                      -- 'daily' | 'weekly' | 'adhoc'
    content         TEXT NOT NULL,                      -- brief markdown text
    window_start    DATETIME NOT NULL,                  -- content window start
    window_end      DATETIME NOT NULL,                  -- content window end
    generated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    delivered       INTEGER DEFAULT 0,
    extended_window INTEGER DEFAULT 0                   -- G26: 1 if fallback window was used
);
CREATE INDEX IF NOT EXISTS idx_kb_briefs_user_domain ON kb_briefs(user_id, domain, generated_at DESC);

-- Topic snapshots for longitudinal comparison (G28)
CREATE TABLE IF NOT EXISTS kb_snapshots (
    id          TEXT PRIMARY KEY,                       -- UUID
    user_id     TEXT NOT NULL DEFAULT 'default',
    topic_query TEXT NOT NULL,                          -- the query/topic being tracked
    summary     TEXT NOT NULL,                          -- agent's conclusion about this topic at snapshot time
    source_ids  TEXT,                                   -- JSON array of account IDs cited
    snapshot_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    session_id  TEXT                                    -- link to conversation session
);
CREATE INDEX IF NOT EXISTS idx_kb_snapshots_user_topic ON kb_snapshots(user_id, topic_query);
"""


def run_migration(db_path: Optional[str] = None) -> bool:
    """
    Run migration. Safe to re-run: all tables use CREATE TABLE IF NOT EXISTS.
    """
    path = db_path or get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {path}")

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    try:
        cur.executescript(_SQL)

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
        print("  Tables created: kb_accounts, kb_account_aliases, kb_relations,")
        print("                  kb_ingestion_log, kb_discovery_queue, kb_alerts,")
        print("                  kb_user_config, kb_briefs, kb_snapshots")
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
