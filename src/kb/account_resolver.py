"""
KB Account Resolver — natural-language name → account_id mapping.

Bridges the gap between how users refer to accounts in conversation
(display names, brand names, nicknames, aliases) and the exact
snake_case IDs stored in kb_accounts.

Resolution pipeline (first match wins):
  1. Exact match on account_id
  2. Exact match on display_name (case-insensitive)
  3. Exact match on any search_term (case-insensitive)
  4. Exact match on any platform handle (Twitter @handle, etc.)
  5. Fuzzy match on display_name          (difflib, threshold 0.72)
  6. Fuzzy match on account_id            (difflib, threshold 0.72)
  7. Fuzzy match on each search_term      (difflib, threshold 0.72)
  8. Substring match on display_name / search_terms (for "Preston" → "Preston Pysh")

Returns a list of ResolvedAccount sorted by confidence (highest first).
The caller should pick [0] if confidence >= MIN_CONFIDENCE_AUTO (0.72)
and optionally surface alternatives to the user if multiple near-equal
matches exist.

Design reference: KnowledgeBase_design.md §6.1
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from config.agent_config import get_agent_config as _get_agent_config
from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)


# Minimum score to auto-select a fuzzy match without surfacing alternatives
MIN_CONFIDENCE_AUTO: float = _get_agent_config().kb.resolver.min_confidence_auto

# Score assigned to exact-match strategies (always preferred over fuzzy)
_SCORE_EXACT_ID = 1.00
_SCORE_EXACT_DISPLAY = 0.99
_SCORE_EXACT_TERM = 0.98
_SCORE_EXACT_HANDLE = 0.97


@dataclass
class ResolvedAccount:
    account_id: str
    display_name: str
    domains: str
    confidence: float          # 0.0–1.0
    match_reason: str          # human-readable: "exact_id", "fuzzy_display", etc.
    search_terms: list[str] = field(default_factory=list)


def _similarity(a: str, b: str) -> float:
    """Case-insensitive SequenceMatcher ratio."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _load_accounts(db_path: str) -> list[dict]:
    """
    Load all active accounts from kb_accounts + their aliases.
    Returns list of dicts with keys:
      id, display_name, domains, search_terms (list), handles (list of strings)
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            "SELECT id, display_name, domains, search_terms FROM kb_accounts WHERE active = 1"
        )
        rows = cur.fetchall()

        # Load all aliases for these accounts in one query
        ids = [r["id"] for r in rows]
        handles: dict[str, list[str]] = {i: [] for i in ids}
        if ids:
            placeholders = ",".join("?" * len(ids))
            cur.execute(
                f"SELECT account_id, platform_id FROM kb_account_aliases WHERE account_id IN ({placeholders})",
                ids,
            )
            for alias_row in cur.fetchall():
                handles[alias_row["account_id"]].append(alias_row["platform_id"])

        conn.close()

        accounts = []
        for row in rows:
            terms = []
            if row["search_terms"]:
                try:
                    terms = json.loads(row["search_terms"])
                except Exception:
                    pass
            accounts.append({
                "id": row["id"],
                "display_name": row["display_name"],
                "domains": row["domains"] or "",
                "search_terms": terms,
                "handles": handles.get(row["id"], []),
            })
        return accounts
    except Exception as exc:
        logger.warning("account_resolver: failed to load accounts: %s", exc)
        return []


def resolve(
    name: str,
    db_path: str = str(get_db_path()),
    user_id: Optional[str] = None,
    limit: int = 3,
) -> list[ResolvedAccount]:
    """
    Resolve a natural-language account reference to a list of ResolvedAccount
    candidates sorted by confidence descending.

    Parameters
    ----------
    name :
        The name as provided by the user or agent — can be a display name,
        nickname, brand name, Twitter handle, or misspelled account_id.
    db_path :
        Path to the SQLite database.
    user_id :
        When provided, only accounts belonging to this user are searched.
        Pass None to search across all users (e.g., when called from a
        subprocess that doesn't know the user_id yet).
    limit :
        Maximum number of candidates to return.

    Returns
    -------
    list[ResolvedAccount]
        Sorted by confidence descending. Empty list if no accounts found.
        Check candidates[0].confidence >= MIN_CONFIDENCE_AUTO before auto-selecting.
    """
    if not name or not name.strip():
        return []

    name = name.strip()
    name_lower = name.lower()
    # Normalize @handle input
    name_lower_stripped = name_lower.lstrip("@")

    accounts = _load_accounts(db_path)
    if not accounts:
        return []

    # Filter by user_id if provided
    if user_id:
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM kb_accounts WHERE user_id = ? AND active = 1", (user_id,)
            )
            user_account_ids = {r[0] for r in cur.fetchall()}
            conn.close()
            accounts = [a for a in accounts if a["id"] in user_account_ids]
        except Exception:
            pass  # Fall back to all accounts

    candidates: list[ResolvedAccount] = []
    seen_ids: set[str] = set()

    def _add(acc: dict, score: float, reason: str) -> None:
        if acc["id"] not in seen_ids or score > next(
            (c.confidence for c in candidates if c.account_id == acc["id"]), 0
        ):
            seen_ids.add(acc["id"])
            candidates.append(ResolvedAccount(
                account_id=acc["id"],
                display_name=acc["display_name"],
                domains=acc["domains"],
                confidence=round(score, 4),
                match_reason=reason,
                search_terms=acc["search_terms"],
            ))

    # -----------------------------------------------------------------------
    # Strategy 1: Exact match on account_id
    # -----------------------------------------------------------------------
    for acc in accounts:
        if name_lower == acc["id"].lower():
            _add(acc, _SCORE_EXACT_ID, "exact_id")

    # -----------------------------------------------------------------------
    # Strategy 2: Exact match on display_name
    # -----------------------------------------------------------------------
    for acc in accounts:
        if name_lower == acc["display_name"].lower():
            _add(acc, _SCORE_EXACT_DISPLAY, "exact_display")

    # -----------------------------------------------------------------------
    # Strategy 3: Exact match on search_terms
    # -----------------------------------------------------------------------
    for acc in accounts:
        for term in acc["search_terms"]:
            if name_lower == term.lower():
                _add(acc, _SCORE_EXACT_TERM, "exact_search_term")
                break

    # -----------------------------------------------------------------------
    # Strategy 4: Exact match on platform handle (Twitter @handle, etc.)
    # -----------------------------------------------------------------------
    for acc in accounts:
        for handle in acc["handles"]:
            if name_lower_stripped == handle.lstrip("@").lower():
                _add(acc, _SCORE_EXACT_HANDLE, "exact_handle")
                break

    # Return early if we have high-confidence exact matches
    if any(c.confidence >= _SCORE_EXACT_TERM for c in candidates):
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        return candidates[:limit]

    # -----------------------------------------------------------------------
    # Strategy 5: Fuzzy match on display_name
    # -----------------------------------------------------------------------
    for acc in accounts:
        score = _similarity(name, acc["display_name"])
        if score >= MIN_CONFIDENCE_AUTO:
            _add(acc, score, "fuzzy_display")

    # -----------------------------------------------------------------------
    # Strategy 6: Fuzzy match on account_id (handles minor typos)
    # e.g., "lyn_alden" vs "lynn_alden", "jeff_snyder" vs "jeff_snider"
    # -----------------------------------------------------------------------
    for acc in accounts:
        score = _similarity(name_lower.replace(" ", "_"), acc["id"])
        if score >= MIN_CONFIDENCE_AUTO:
            _add(acc, score, "fuzzy_id")

    # -----------------------------------------------------------------------
    # Strategy 7: Fuzzy match on each search_term
    # -----------------------------------------------------------------------
    for acc in accounts:
        best = 0.0
        for term in acc["search_terms"]:
            score = _similarity(name, term)
            if score > best:
                best = score
        if best >= MIN_CONFIDENCE_AUTO:
            _add(acc, best, "fuzzy_search_term")

    # -----------------------------------------------------------------------
    # Strategy 8: Substring / keyword match
    # Handles "Preston" → "Preston Pysh", "Misfits" → "Ungovernable Misfits"
    # -----------------------------------------------------------------------
    if len(name_lower) >= 4:  # avoid matching very short fragments
        for acc in accounts:
            if acc["id"] in seen_ids:
                continue
            # Check display_name
            if name_lower in acc["display_name"].lower():
                score = len(name_lower) / len(acc["display_name"])
                # Scale: exact substring of short display_name scores higher
                _add(acc, min(0.85, 0.60 + score * 0.25), "substring_display")
                continue
            # Check search_terms
            for term in acc["search_terms"]:
                if name_lower in term.lower():
                    score = len(name_lower) / len(term)
                    _add(acc, min(0.82, 0.58 + score * 0.25), "substring_term")
                    break

    # Sort by confidence descending, deduplicate (keep highest score per id)
    by_id: dict[str, ResolvedAccount] = {}
    for c in candidates:
        if c.account_id not in by_id or c.confidence > by_id[c.account_id].confidence:
            by_id[c.account_id] = c

    result = sorted(by_id.values(), key=lambda c: c.confidence, reverse=True)
    return result[:limit]


def resolve_one(
    name: str,
    db_path: str = str(get_db_path()),
    user_id: Optional[str] = None,
) -> Optional[ResolvedAccount]:
    """
    Convenience wrapper: return the single best match if confidence >=
    MIN_CONFIDENCE_AUTO, else None.
    """
    candidates = resolve(name, db_path=db_path, user_id=user_id, limit=1)
    if candidates and candidates[0].confidence >= MIN_CONFIDENCE_AUTO:
        return candidates[0]
    return None
