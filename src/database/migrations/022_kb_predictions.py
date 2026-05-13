#!/usr/bin/env python3
"""
Database Migration 022: Prediction Tracking.

Phase KB-15: adds the data layer for automated prediction extraction and
weekly verification.

New table:
  kb_predictions  — one row per prediction extracted from ingested content;
                    tracks extraction, stated confidence, outcome date, and
                    the result of weekly verification runs.

Design reference: KB_assistant_design_v2.md §12.1
"""
import sqlite3
import sys
from pathlib import Path

MIGRATION_ID = "022"
MIGRATION_NAME = "kb_predictions"
MIGRATION_DESCRIPTION = (
    "KB-15 Prediction Tracking: kb_predictions table with per-account "
    "leaderboard support (verified/failed/pending counts)"
)


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kb_predictions (
                id                   TEXT PRIMARY KEY,
                user_id              TEXT NOT NULL,
                source_account       TEXT NOT NULL,
                source_chunk_id      TEXT,
                prediction_text      TEXT NOT NULL,
                predicted_outcome    TEXT,
                predicted_date       TEXT,           -- ISO-8601 date, NULL if no specific date
                confidence_stated    REAL,           -- 0.0-1.0; NULL if not stated by source
                extracted_at         TEXT NOT NULL,  -- ISO-8601 UTC
                verification_status  TEXT NOT NULL DEFAULT 'pending',
                    -- 'pending' | 'verified' | 'failed' | 'inconclusive' | 'expired'
                verified_at          TEXT,
                verification_evidence TEXT,
                verification_url     TEXT,
                FOREIGN KEY (source_account) REFERENCES kb_accounts(id)
            )
        """)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_predictions_user "
            "ON kb_predictions(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_predictions_account "
            "ON kb_predictions(source_account)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_predictions_status "
            "ON kb_predictions(verification_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kb_predictions_date "
            "ON kb_predictions(predicted_date)"
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
