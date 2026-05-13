"""
KB Freshness Classifier — Phase KB-16.

Rule-based keyword scan that assigns a TTL (time-to-live in days) and a
content category to a chunk of text.  Runs entirely in-process; no LLM call.
Covers ~90 % of cases by design — the remaining 10 % fall through to the
90-day default.

Returned TTL semantics
  None   — prediction/forecast: never expires on its own; handled separately
  int    — chunk should be considered stale after this many days from
           published_at (0 = already stale on arrival)

Usage::

    from kb.freshness_classifier import classify_freshness

    ttl_days, category = classify_freshness(chunk_text)
    # e.g.  (1, 'market_data')  or  (730, 'evergreen')  or  (None, 'prediction')

Design reference: KB_assistant_design_v2.md §12.3
"""
from __future__ import annotations

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

# Each entry: (regex_pattern, ttl_days_or_None, category_label)
# Rules are evaluated in order; first match wins.
_FRESHNESS_RULES: list[tuple[str, Optional[int], str]] = [
    # Predictions / forecasts — no absolute TTL; keep until verified
    (r'\bpredicts?\b|\bforecasts?\b|\bwill\b.{1,60}\bby \d{4}\b|\bexpects?\b.{1,40}\bby \d{4}\b', None, "prediction"),

    # Market / price data — stale after 1 day
    (r'\bprice\b|\brates?\b|\bspread\b|\byield\b|\btrading\b|\bvolume\b|\bmarket cap\b', 1, "market_data"),

    # Breaking news / real-time events — stale after 7 days
    (r'\bbreaking\b|\bjust\b|\btoday\b|\bannounced\b|\balert\b|\bflash\b', 7, "news"),

    # Analysis / commentary — stale after 30 days
    (r'\banalysis\b|\bdeep.?dive\b|\bthread\b|\bopinion\b|\bcommentary\b|\bperspective\b', 30, "analysis"),

    # Reference / evergreen — 2-year TTL
    (r'\bframework\b|\bmethod\w*\b|\bprinciple\b|\bbabok\b|\bguide\b|\btutorial\b|\bhow.to\b|\bdefinition\b', 730, "evergreen"),

    # Weekly / monthly macro summaries — stale after 90 days
    (r'\bweekly\b|\bmonthly\b|\bquarterly\b|\breport\b|\bsummary\b|\bupdate\b', 90, "periodic"),
]

_DEFAULT_TTL = 90
_DEFAULT_CATEGORY = "general"


def classify_freshness(text: str) -> tuple[Optional[int], str]:
    """
    Classify a text chunk and return ``(ttl_days, category)``.

    ``ttl_days`` is ``None`` for predictions (no expiry), or an integer
    number of days after which the chunk is considered stale.

    Only the first 1 000 characters of ``text`` are inspected (fast path).
    """
    sample = text[:1000]
    for pattern, ttl, category in _FRESHNESS_RULES:
        if re.search(pattern, sample, re.IGNORECASE):
            return ttl, category
    return _DEFAULT_TTL, _DEFAULT_CATEGORY


def is_stale(ttl_days: Optional[int], published_at_iso: str) -> bool:
    """
    Return True if a chunk has passed its TTL.

    ``published_at_iso`` should be an ISO 8601 string.
    Predictions (ttl_days is None) are never considered stale by this function.
    """
    if ttl_days is None:
        return False
    if not published_at_iso:
        return False
    try:
        from datetime import datetime, timezone
        published = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - published).days
        return age_days > ttl_days
    except Exception:
        return False


def freshness_boost(ttl_days: Optional[int], published_at_iso: str) -> float:
    """
    Return a multiplicative relevance boost in [0.5, 1.0] based on freshness.

    Fresh chunks (age < ttl/2) get boost 1.0.
    Chunks past their TTL get boost 0.5.
    Predictions always get boost 1.0 (no TTL).
    """
    if ttl_days is None:
        return 1.0
    if not published_at_iso:
        return 1.0
    try:
        from datetime import datetime, timezone
        published = datetime.fromisoformat(published_at_iso.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - published).days
        if age_days <= 0:
            return 1.0
        if ttl_days <= 0:
            return 0.5
        ratio = age_days / ttl_days
        if ratio <= 0.5:
            return 1.0
        if ratio >= 1.0:
            return 0.5
        # Linear interpolation from 1.0 to 0.5 in the second half of TTL
        return 1.0 - (ratio - 0.5)
    except Exception:
        return 1.0
