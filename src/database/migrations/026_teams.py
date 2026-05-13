"""
Migration 026 — Teams + Family Mode (KB-21)

Creates:
  - deployment_config  (singleton: mode, max_users, allow_registration)
  - teams              (team definitions with JSON settings)
  - team_members       (RBAC: owner | admin | member | viewer)

Alters:
  - kb_accounts: adds scope TEXT DEFAULT 'personal'
  - kb_accounts: adds scope_id TEXT DEFAULT NULL
"""
import sqlite3
import sys
from pathlib import Path


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # ── deployment_config ──────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS deployment_config (
            id                  INTEGER PRIMARY KEY CHECK (id = 1),
            mode                TEXT    NOT NULL DEFAULT 'solo',
            max_users           INTEGER NOT NULL DEFAULT 1,
            allow_registration  INTEGER NOT NULL DEFAULT 0,
            require_invite      INTEGER NOT NULL DEFAULT 1,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # Seed singleton row if absent
    c.execute("INSERT OR IGNORE INTO deployment_config (id) VALUES (1)")

    # ── teams ──────────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT,
            created_by  TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            settings    TEXT,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)

    # ── team_members ───────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            team_id   TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            role      TEXT NOT NULL DEFAULT 'member',
            joined_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (team_id, user_id),
            FOREIGN KEY (team_id)  REFERENCES teams(id)   ON DELETE CASCADE,
            FOREIGN KEY (user_id)  REFERENCES users(id)
        )
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(user_id)")

    # ── kb_accounts: scope columns (idempotent via pragma) ────────────────────
    existing_cols = {row["name"] for row in c.execute("PRAGMA table_info(kb_accounts)").fetchall()}

    if "scope" not in existing_cols:
        c.execute("ALTER TABLE kb_accounts ADD COLUMN scope TEXT NOT NULL DEFAULT 'personal'")

    if "scope_id" not in existing_cols:
        c.execute("ALTER TABLE kb_accounts ADD COLUMN scope_id TEXT DEFAULT NULL")

    conn.commit()
    conn.close()
    print(f"[026_teams] migration applied to {db_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 026_teams.py <path/to/agent.db>")
        sys.exit(1)
    run(sys.argv[1])
