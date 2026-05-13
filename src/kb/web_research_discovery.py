"""
KB Web Research Discovery — Phase KB-4 (G25).

For pending discovery candidates that have reached PROMOTE_THRESHOLD but have
no platform aliases, performs LLM-based handle enrichment to suggest their
presence on tracked platforms (Twitter/X, YouTube, Nostr, Rumble, podcast RSS).

This uses the LLM's parametric knowledge — NOT a live web scrape. Results are
stored as candidate_handles in kb_discovery_queue and still require user
approval (approve_candidate()) before an account is created. Confidence is
implicitly 0.6 (agent-suggested, unverified).

Trigger: called by KBScheduler after each ingestion tick for any pending
candidate at >= PROMOTE_THRESHOLD with no existing handle data.

Design reference: KnowledgeBase_design.md §6.5 (G25)
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from kb.discovery import PROMOTE_THRESHOLD

from utils.logger import setup_logger
logger = setup_logger(__name__)

_RESEARCH_MODEL = "deepseek-v3.2"
_PLATFORMS = ("twitter", "youtube", "nostr", "rumble", "podcast_rss")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_candidates_needing_enrichment(db_path: str, user_id: str) -> list[dict]:
    """
    Return pending candidates at >= PROMOTE_THRESHOLD whose candidate_handles
    dict has no non-empty values (i.e., never enriched before).
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id, candidate_name, candidate_handles, rationale
            FROM kb_discovery_queue
            WHERE user_id = ? AND status = 'pending' AND mention_count >= ?
            ORDER BY mention_count DESC
            """,
            (user_id, PROMOTE_THRESHOLD),
        )
        rows = []
        for row in cur.fetchall():
            handles: dict = json.loads(row["candidate_handles"] or "{}")
            if not any(v for v in handles.values() if v):
                rows.append(dict(row))
        conn.close()
        return rows
    except Exception as exc:
        logger.debug("get_candidates_needing_enrichment failed: %s", exc)
        return []


def _update_handles(db_path: str, candidate_id: int, handles: dict) -> None:
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE kb_discovery_queue SET candidate_handles = ? WHERE id = ?",
            (json.dumps(handles), candidate_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("_update_handles failed for id=%d: %s", candidate_id, exc)


# ---------------------------------------------------------------------------
# LLM enrichment
# ---------------------------------------------------------------------------

def _llm_find_handles(name: str, rationale: str, api_key: str) -> dict:
    """
    Ask the LLM to suggest platform handles for the entity.

    Returns {platform: handle_or_url} (only non-null values). Empty dict on
    failure or when the LLM cannot identify any handle.
    """
    import httpx

    nano_gpt_base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    platform_list = ", ".join(_PLATFORMS)
    prompt = (
        f"You are a research assistant. Given the entity name and context below, "
        f"provide their known handles or profile URLs on the listed platforms. "
        f"Output ONLY a valid JSON object with platform names as keys and "
        f"handle strings or URLs as values. Use null for platforms you cannot identify.\n\n"
        f"Entity: {name}\n"
        f"Context: {rationale}\n\n"
        f"Platforms: {platform_list}\n\n"
        f'Example: {{"twitter": "@handle", "youtube": "https://youtube.com/@channel", '
        f'"nostr": "npub1...", "rumble": null, "podcast_rss": null}}'
    )
    try:
        resp = httpx.post(
            f"{nano_gpt_base}/chat/completions",
            json={
                "model": _RESEARCH_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        parsed: dict = json.loads(raw)
        # Strip null/empty/unknown values
        return {k: v for k, v in parsed.items() if v and v not in ("null", "unknown")}
    except Exception as exc:
        logger.debug("LLM handle lookup failed for %r: %s", name, exc)
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich_discovery_candidates(
    user_id: str,
    db_path: Optional[str] = None,
    api_key: Optional[str] = None,
) -> int:
    """
    For each pending discovery candidate at PROMOTE_THRESHOLD with no handles,
    ask the LLM to suggest platform handles and update kb_discovery_queue.

    Returns the number of candidates enriched (may be 0 if all are already
    enriched or if the API key is missing).
    """
    db = db_path or str(get_db_path())
    key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
    if not key:
        logger.debug("No NANO_GPT_API_KEY — skipping web research discovery")
        return 0

    candidates = _get_candidates_needing_enrichment(db, user_id)
    enriched = 0

    for cand in candidates:
        handles = _llm_find_handles(
            name=cand["candidate_name"],
            rationale=cand.get("rationale", ""),
            api_key=key,
        )
        if handles:
            _update_handles(db, cand["id"], handles)
            logger.info(
                "Web-research enriched candidate %r with platforms: %s",
                cand["candidate_name"],
                list(handles.keys()),
            )
            enriched += 1

    return enriched
