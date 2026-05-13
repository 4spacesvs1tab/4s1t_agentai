"""
KB Snapshot Service — Phase KB-4 (G28).

Saves topic snapshots and computes longitudinal diffs between them.

A snapshot captures an agent's conclusion about a query topic at a point in
time. The compare function uses an LLM to highlight what changed between two
snapshots for the same topic.

Usage (from an agent or API route)::

    from kb.snapshot_service import get_snapshot_service
    svc = get_snapshot_service()
    svc.save(user_id="default", topic_query="latest developments in topic X", summary="...")
    diff = svc.compare(user_id="default", topic_query="latest developments in topic X")

Design reference: KnowledgeBase_design.md §6.9 (longitudinal comparison)
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)

_DIFF_MODEL = "deepseek-v3.2"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# LLM diff helper
# ---------------------------------------------------------------------------

def _llm_diff(
    topic: str,
    older_summary: str,
    older_date: str,
    newer_summary: str,
    newer_date: str,
    api_key: str,
) -> Optional[str]:
    """Call DeepSeek V3 to produce a changelog between two topic summaries."""
    import httpx

    nano_gpt_base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    prompt = (
        f"You are a longitudinal analysis assistant. Compare two knowledge-base snapshots "
        f"about the topic: **{topic}**\n\n"
        f"**Older snapshot ({older_date[:10]}):**\n{older_summary}\n\n"
        f"**Newer snapshot ({newer_date[:10]}):**\n{newer_summary}\n\n"
        "Produce a concise markdown changelog: what changed, what is new, what disappeared. "
        "Focus on factual differences only. Format:\n"
        "## What changed\n- ...\n## What is new\n- ...\n## What is no longer mentioned\n- ..."
    )
    try:
        resp = httpx.post(
            f"{nano_gpt_base}/chat/completions",
            json={
                "model": _DIFF_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 600,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("LLM diff failed for topic=%r: %s", topic, exc)
        return None


# ---------------------------------------------------------------------------
# SnapshotService
# ---------------------------------------------------------------------------

class SnapshotService:
    """CRUD + longitudinal comparison for kb_snapshots."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or str(get_db_path())

    def save(
        self,
        user_id: str,
        topic_query: str,
        summary: str,
        source_ids: list[str] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Insert a new snapshot row and return its UUID."""
        snapshot_id = str(uuid.uuid4())
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            """
            INSERT INTO kb_snapshots
                (id, user_id, topic_query, summary, source_ids, snapshot_at, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                user_id,
                topic_query,
                summary,
                json.dumps(source_ids or []),
                _now_iso(),
                session_id,
            ),
        )
        conn.commit()
        conn.close()
        logger.info("Saved snapshot id=%s topic=%r user=%s", snapshot_id, topic_query, user_id)
        return snapshot_id

    def get_by_topic(
        self,
        user_id: str,
        topic_query: str,
        limit: int = 10,
    ) -> list[dict]:
        """Return the most recent snapshots for (user_id, topic_query), newest first."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT * FROM kb_snapshots
                WHERE user_id = ? AND lower(topic_query) = lower(?)
                ORDER BY snapshot_at DESC LIMIT ?
                """,
                (user_id, topic_query, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as exc:
            logger.warning("get_by_topic failed: %s", exc)
            return []

    def get_all(self, user_id: str, limit: int = 50) -> list[dict]:
        """Return snapshot index (no summary text) for user_id, newest first."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT id, user_id, topic_query, snapshot_at, session_id
                FROM kb_snapshots
                WHERE user_id = ?
                ORDER BY snapshot_at DESC LIMIT ?
                """,
                (user_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as exc:
            logger.warning("get_all snapshots failed: %s", exc)
            return []

    def compare(
        self,
        user_id: str,
        topic_query: str,
        api_key: str | None = None,
    ) -> Optional[str]:
        """
        Diff the two most recent snapshots for topic_query using an LLM.

        Returns a markdown diff string, or None if fewer than 2 snapshots exist.
        """
        snaps = self.get_by_topic(user_id, topic_query, limit=2)
        if len(snaps) < 2:
            return None

        newer, older = snaps[0], snaps[1]
        key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
        return _llm_diff(
            topic=topic_query,
            older_summary=older["summary"],
            older_date=older["snapshot_at"],
            newer_summary=newer["summary"],
            newer_date=newer["snapshot_at"],
            api_key=key,
        )

    def delete(self, snapshot_id: str, user_id: str) -> bool:
        """Delete a snapshot by ID (owner check via user_id)."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "DELETE FROM kb_snapshots WHERE id = ? AND user_id = ?",
                (snapshot_id, user_id),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as exc:
            logger.warning("delete snapshot failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: SnapshotService | None = None


def get_snapshot_service(db_path: Optional[str] = None) -> SnapshotService:
    """Return the shared SnapshotService singleton."""
    global _service
    if _service is None:
        _service = SnapshotService(db_path)
    return _service
