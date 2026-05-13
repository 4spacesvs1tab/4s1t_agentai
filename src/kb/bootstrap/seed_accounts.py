#!/usr/bin/env python3
"""
Seed KB accounts from src/config/kb_domains.yaml into SQLite.

This is the intended setup flow for L1 accounts:

  1. Edit src/config/kb_domains.yaml — add/remove accounts, fill in real
     RSS feed URLs, Twitter handles, YouTube channel IDs, etc.
  2. Run this script to push changes into the database.
  3. The KBScheduler then picks up all active accounts and starts ingesting.

The script is fully idempotent:
  - Accounts already in kb_accounts (matched by id) are updated in-place.
  - Aliases already in kb_account_aliases are replaced (INSERT OR REPLACE).
  - Accounts in the DB that are no longer in the YAML are left untouched
    (they may have been added via the API or discovery — do not delete them).

Usage:
  python3 src/kb/bootstrap/seed_accounts.py
  python3 src/kb/bootstrap/seed_accounts.py --db /path/to/agent.db
  python3 src/kb/bootstrap/seed_accounts.py --user-id alice
  python3 src/kb/bootstrap/seed_accounts.py --dry-run

Design reference: KnowledgeBase_design.md §6.5, §10 (Phase KB-1, step 7)
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
from utils.logger import setup_logger
logger = setup_logger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DEFAULT_DB = str(_PROJECT_ROOT / "data" / "agent.db")
_DEFAULT_YAML = str(_PROJECT_ROOT / "src" / "config" / "kb_domains.yaml")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _upsert_account(
    conn: sqlite3.Connection,
    account_id: str,
    user_id: str,
    display_name: str,
    layer: int,
    domains: str,
    dry_run: bool,
) -> str:
    """
    Insert or update a kb_accounts row.

    Returns 'inserted', 'updated', or 'dry-run'.
    """
    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute(
        "SELECT id FROM kb_accounts WHERE id = ? AND user_id = ?",
        (account_id, user_id),
    ).fetchone()

    if dry_run:
        action = "would-insert" if existing is None else "would-update"
        logger.info("[dry-run] %s account: %s (%s)", action, account_id, display_name)
        return "dry-run"

    if existing is None:
        conn.execute(
            """
            INSERT INTO kb_accounts
                (id, user_id, display_name, layer, domains, active, added_at, added_by)
            VALUES (?, ?, ?, ?, ?, 1, ?, 'seed')
            """,
            (account_id, user_id, display_name, layer, domains, now),
        )
        return "inserted"
    else:
        conn.execute(
            """
            UPDATE kb_accounts
            SET display_name = ?, layer = ?, domains = ?, active = 1
            WHERE id = ? AND user_id = ?
            """,
            (display_name, layer, domains, account_id, user_id),
        )
        return "updated"


def _upsert_alias(
    conn: sqlite3.Connection,
    account_id: str,
    platform: str,
    platform_id: str,
    dry_run: bool,
) -> None:
    if not platform_id or platform_id.startswith("placeholder"):
        logger.debug("Skipping placeholder alias: %s / %s", account_id, platform)
        return

    if dry_run:
        logger.info("[dry-run] alias  %s → %s: %s", account_id, platform, platform_id)
        return

    conn.execute(
        """
        INSERT OR REPLACE INTO kb_account_aliases
            (account_id, platform, platform_id, confidence, verified)
        VALUES (?, ?, ?, 1.0, 1)
        """,
        (account_id, platform, platform_id),
    )


# ---------------------------------------------------------------------------
# Main seeder
# ---------------------------------------------------------------------------

def seed(db_path: str, yaml_path: str, user_id: str, dry_run: bool) -> None:
    logger.info("Loading YAML: %s", yaml_path)
    with open(yaml_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    domains_cfg = config.get("domains", {})
    if not domains_cfg:
        logger.error("No 'domains' key found in YAML — nothing to seed.")
        sys.exit(1)

    logger.info("Target DB:   %s", db_path)
    logger.info("User ID:     %s", user_id)
    if dry_run:
        logger.info("DRY RUN — no changes will be written.")

    conn = sqlite3.connect(db_path)

    total_inserted = total_updated = total_aliases = 0

    for domain_id, domain_cfg in domains_cfg.items():
        accounts = domain_cfg.get("initial_accounts", [])
        if not accounts:
            logger.debug("Domain %s has no initial_accounts — skipping.", domain_id)
            continue

        logger.info("Domain: %s (%d accounts)", domain_id, len(accounts))

        for acc in accounts:
            acc_id = acc.get("id", "").strip()
            display_name = acc.get("display_name", acc_id)
            layer = int(acc.get("layer", 1))
            aliases = acc.get("aliases", {}) or {}

            if not acc_id:
                logger.warning("Account entry missing 'id' in domain %s — skipped.", domain_id)
                continue

            # domains field: pipe-separated, includes this domain.
            # An account can appear in multiple domain blocks — if so, re-running
            # will accumulate domain tags correctly.
            if not dry_run:
                # Read existing domains to merge (don't overwrite if account
                # already has extra domains from other seed runs or user edits).
                existing_row = conn.execute(
                    "SELECT domains FROM kb_accounts WHERE id = ? AND user_id = ?",
                    (acc_id, user_id),
                ).fetchone()
                if existing_row:
                    existing_domains = set(existing_row[0].split("|")) if existing_row[0] else set()
                    existing_domains.add(domain_id)
                    domains_str = "|".join(sorted(existing_domains))
                else:
                    domains_str = domain_id
            else:
                domains_str = domain_id  # simplified for dry-run display

            action = _upsert_account(
                conn, acc_id, user_id, display_name, layer, domains_str, dry_run
            )
            if action == "inserted":
                total_inserted += 1
            elif action == "updated":
                total_updated += 1

            for platform, platform_id in aliases.items():
                if platform_id:
                    _upsert_alias(conn, acc_id, platform, str(platform_id), dry_run)
                    if not dry_run and not str(platform_id).startswith("placeholder"):
                        total_aliases += 1

    if not dry_run:
        conn.commit()

    conn.close()

    logger.info(
        "Done. Inserted: %d  Updated: %d  Aliases: %d",
        total_inserted, total_updated, total_aliases,
    )
    if dry_run:
        logger.info("(dry-run — nothing written)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed KB L1 accounts from kb_domains.yaml into SQLite."
    )
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB,
        help=f"Path to agent.db (default: {_DEFAULT_DB})",
    )
    parser.add_argument(
        "--yaml",
        default=_DEFAULT_YAML,
        help=f"Path to kb_domains.yaml (default: {_DEFAULT_YAML})",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help=(
            "User ID to seed accounts for. "
            "Defaults to the first user found in the database (auto-detected). "
            "Pass 'default' explicitly only for dev/test environments with no real users."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing anything.",
    )
    args = parser.parse_args()

    user_id = args.user_id
    if user_id is None:
        # Auto-detect: use the first real user from the DB, fall back to 'default'
        try:
            _conn = sqlite3.connect(args.db)
            _row = _conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
            _conn.close()
            user_id = _row[0] if _row else "default"
            logger.info("Auto-detected user_id: %s", user_id)
        except Exception as _exc:
            logger.warning("Could not auto-detect user_id (%s) — using 'default'", _exc)
            user_id = "default"

    seed(
        db_path=args.db,
        yaml_path=args.yaml,
        user_id=user_id,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
