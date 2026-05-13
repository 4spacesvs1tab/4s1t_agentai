#!/usr/bin/env python3
"""
Database Migration 017: Assistant Core — Tasks, Reminders, Calendar.

Phase KB-13: adds the data layer for proactive assistant features.

New tables:
  kb_tasks             — user tasks with status / priority / due date
  kb_task_updates      — audit log of updates to tasks
  kb_reminders         — one-shot and recurring reminder delivery queue
  kb_calendar_events   — calendar events (manual + extracted + iCal import)

New column on users:
  role_description     — free-text professional context e.g. "Business Analyst, Warsaw"
                         used by UserProfile.to_system_prompt_snippet()

Design reference: KB_assistant_design_v2.md §7.2, §7.3, §8.1
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "017"
MIGRATION_NAME = "assistant_core"
MIGRATION_DESCRIPTION = (
    "KB-13 assistant core: kb_tasks, kb_task_updates, kb_reminders, "
    "kb_calendar_events, users.role_description"
)


def get_db_path() -> str:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


_SQL = """
-- ── kb_tasks ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kb_tasks (
    id           TEXT PRIMARY KEY,
    user_id      TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    description  TEXT,
    status       TEXT    DEFAULT 'open',
    -- 'open' | 'in_progress' | 'blocked' | 'done' | 'cancelled'
    priority     TEXT    DEFAULT 'normal',
    -- 'low' | 'normal' | 'high' | 'urgent'
    due_date     TEXT,
    -- ISO date (YYYY-MM-DD), optional
    due_time     TEXT,
    -- ISO time (HH:MM), optional
    timezone     TEXT,
    context      TEXT,
    -- JSON: {session_id, related_chunk_ids, ...}
    source       TEXT    DEFAULT 'manual',
    -- 'manual' | 'extracted' | 'reminder_promoted'
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_status
    ON kb_tasks(user_id, status, due_date);

-- ── kb_task_updates ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kb_task_updates (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    update_text TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES kb_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_updates_task
    ON kb_task_updates(task_id, created_at DESC);

-- ── kb_reminders ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kb_reminders (
    id           TEXT PRIMARY KEY,
    user_id      TEXT    NOT NULL,
    message      TEXT    NOT NULL,
    trigger_at   TEXT    NOT NULL,
    -- UTC ISO timestamp: when to fire
    timezone     TEXT    NOT NULL,
    -- user TZ for display e.g. 'Europe/Warsaw'
    status       TEXT    DEFAULT 'pending',
    -- 'pending' | 'sent' | 'cancelled' | 'snoozed'
    delivered_at TEXT,
    snooze_until TEXT,
    -- UTC ISO timestamp; non-null when status='snoozed'
    recurrence   TEXT,
    -- NULL | 'daily' | 'weekly' | 'monthly' | JSON RRULE-like
    context      TEXT,
    -- JSON: {session_id, task_id, ...}
    priority     INTEGER DEFAULT 0,
    -- 0=normal, 1=high, 2=urgent
    created_at   TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reminders_pending
    ON kb_reminders(user_id, status, trigger_at);

-- ── kb_calendar_events ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS kb_calendar_events (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT,
    start_time  TEXT NOT NULL,
    -- UTC ISO timestamp
    end_time    TEXT,
    timezone    TEXT NOT NULL,
    location    TEXT,
    source      TEXT DEFAULT 'manual',
    -- 'manual' | 'extracted' | 'ical_import'
    recurrence  TEXT,
    -- NULL or iCal RRULE string
    created_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_user
    ON kb_calendar_events(user_id, start_time);
"""


def _add_column_if_missing(cur: sqlite3.Cursor, table: str, column: str,
                           definition: str) -> bool:
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
        # ── users: role_description column ───────────────────────────────────
        added = _add_column_if_missing(cur, "users", "role_description", "TEXT")
        if added:
            print("  Added column users.role_description")
        else:
            print("  users.role_description already present — skipped.")

        # ── new tables ────────────────────────────────────────────────────────
        cur.executescript(_SQL)
        print("  Created tables: kb_tasks, kb_task_updates, kb_reminders, "
              "kb_calendar_events")

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
