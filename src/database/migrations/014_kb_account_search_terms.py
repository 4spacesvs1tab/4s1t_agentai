#!/usr/bin/env python3
"""
Database Migration 014: KB Account Search Terms.

Adds search_terms TEXT column to kb_accounts — a JSON array of alternative
names, brand names, nicknames and handles used to resolve natural-language
references to exact account IDs.

Examples:
  john_doe  → ["Brand Name", "Nickname", "twitter_handle"]

Used by kb/account_resolver.py to perform fuzzy name → account_id resolution
inside knowledge_base_search, so agents don't need to know exact IDs.

Design reference: KnowledgeBase_design.md §6.1 (social graph)
"""
import json
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "014"
MIGRATION_NAME = "kb_account_search_terms"
MIGRATION_DESCRIPTION = "Add search_terms to kb_accounts for natural-language account resolution"

# Alternative names / brand names / nicknames / handles per account.
# Keys must match the account IDs used in kb_accounts.
# Values are matched case-insensitively during resolution.
#
# Populate this dict with your own accounts before running the migration,
# or update search_terms directly via the KB Accounts UI after seeding accounts.
_SEARCH_TERMS: dict[str, list[str]] = {
    # "account_id": ["Alternative Name", "Brand Name", "nickname", "@handle"],
}


def get_db_path() -> str:
    project_root = Path(__file__).parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


def run_migration(db_path: Optional[str] = None) -> bool:
    path = db_path or get_db_path()
    print(f"Running migration {MIGRATION_ID}: {MIGRATION_DESCRIPTION}")
    print(f"Database: {path}")

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    try:
        # Add column (idempotent)
        cur.execute("PRAGMA table_info(kb_accounts)")
        cols = [row[1] for row in cur.fetchall()]
        if "search_terms" in cols:
            print("  search_terms column already exists — skipping ALTER TABLE.")
        else:
            cur.execute("ALTER TABLE kb_accounts ADD COLUMN search_terms TEXT DEFAULT NULL")
            print("  Added search_terms column to kb_accounts.")

        # Seed search_terms for known accounts (UPDATE — does not touch rows not in dict)
        updated = 0
        for account_id, terms in _SEARCH_TERMS.items():
            cur.execute(
                "UPDATE kb_accounts SET search_terms = ? WHERE id = ?",
                (json.dumps(terms, ensure_ascii=False), account_id),
            )
            if cur.rowcount:
                updated += 1
        print(f"  Seeded search_terms for {updated} accounts.")

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
