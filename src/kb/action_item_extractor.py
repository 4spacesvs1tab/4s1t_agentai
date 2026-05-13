"""
KB Action Item Extractor — Phase KB-17.

Nightly batch job that scans today's ingested ChromaDB chunks for urgency
signals and asks the LLM to extract actionable items.

Pipeline:
  1. Pre-filter: pull today's chunks from ChromaDB whose text contains at
     least one urgency keyword (no LLM needed — cheap Python check).
  2. Batch: group up to _MAX_CHUNKS_PER_BATCH chunks → one LLM call each.
  3. Store: write extracted items to kb_action_items (migration 024).

Model: deepseek-v3.2 via nano-gpt (same as prediction_extractor).
Estimated token cost: ~50K/nightly batch.

Design reference: KB_assistant_design_v2.md §12.6
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)


_EXTRACTION_MODEL = "deepseek-v3.2"
_MAX_CHUNKS_PER_BATCH = 10
_MAX_CHARS_PER_CHUNK = 500   # keep prompt tight while preserving context

URGENCY_KEYWORDS: list[str] = [
    "consider", "should", "prepare", "review", "check",
    "important", "before", "deadline", "due", "expires",
    "action", "must", "need to", "watch", "monitor",
    "alert", "warning", "caution", "opportunity", "risk",
]

_EXTRACTION_PROMPT = """\
You are extracting concrete, actionable items from analyst or expert content.
An action item is a specific thing the reader should *do*, *watch*, or *prepare for* —
not a general observation or background information.

Rules:
- Only extract genuine action items: tasks, reviews, checks, time-sensitive
  signals, and decisions that require the reader's attention.
- Do NOT extract general opinions, historical facts, or explanations.
- urgency: 'high' if time-sensitive (deadline, imminent event, expires soon),
  'low' if vague/long-term, 'normal' otherwise.
- action_text: imperative, concise (≤ 120 chars).
- context_snippet: ≤ 100 chars from the source that motivated the item.
- source_chunk_id: the chunk_id from input that contains the action item.

Return a JSON array (possibly empty []) — no other text:

[
  {{
    "action_text": "<imperative action>",
    "urgency": "high" | "normal" | "low",
    "context_snippet": "<brief excerpt>",
    "source_chunk_id": "<chunk_id from input>"
  }},
  ...
]

Content chunks (chunk_id ||| domain ||| account ||| text):
{chunks}
"""


@dataclass
class ExtractedActionItem:
    action_text: str
    urgency: str
    context_snippet: Optional[str]
    source_chunk_id: Optional[str]
    source_account: Optional[str]
    domain: Optional[str]


def _has_urgency_keyword(text: str) -> bool:
    """Return True if *text* contains at least one urgency keyword (case-insensitive)."""
    lower = text.lower()
    return any(kw in lower for kw in URGENCY_KEYWORDS)


def _call_llm(prompt: str, api_key: str) -> str:
    """Call nano-gpt chat completions; return raw text or empty string on error."""
    import httpx

    base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": _EXTRACTION_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1200,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("Action item extraction LLM call failed: %s", exc)
        return ""


def _parse_action_items(
    raw: str,
    chunk_meta: dict[str, dict],
) -> list[ExtractedActionItem]:
    """Parse LLM JSON response into ExtractedActionItem objects."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()

    try:
        items = json.loads(text)
        if not isinstance(items, list):
            return []
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                items = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                logger.debug("Could not parse action item JSON: %s", text[:200])
                return []
        else:
            return []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        action_text = (item.get("action_text") or "").strip()
        if not action_text:
            continue

        raw_cid = (item.get("source_chunk_id") or "").strip()
        meta = chunk_meta.get(raw_cid, {})

        urgency = (item.get("urgency") or "normal").lower()
        if urgency not in ("high", "normal", "low"):
            urgency = "normal"

        results.append(ExtractedActionItem(
            action_text=action_text[:150],
            urgency=urgency,
            context_snippet=((item.get("context_snippet") or "").strip() or None),
            source_chunk_id=raw_cid or None,
            source_account=meta.get("account_id"),
            domain=meta.get("domain"),
        ))
    return results


def _store_action_items(
    items: list[ExtractedActionItem],
    user_id: str,
    db_path: str,
) -> int:
    """Insert extracted action items into kb_action_items; return count stored."""
    if not items:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        for item in items:
            conn.execute(
                """
                INSERT INTO kb_action_items
                    (id, user_id, source_chunk_id, source_account, domain,
                     action_text, urgency, context_snippet, extracted_at,
                     status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    str(uuid.uuid4()),
                    user_id,
                    item.source_chunk_id,
                    item.source_account,
                    item.domain,
                    item.action_text,
                    item.urgency,
                    item.context_snippet,
                    now,
                    now,
                ),
            )
            inserted += 1
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("Failed to store action items for user=%s: %s", user_id, exc)
    return inserted


def _get_today_chunks(user_id: str, db_path: str) -> list[dict]:
    """
    Fetch today's ingested chunks from ChromaDB for *user_id*.

    Returns list of dicts with keys: id, text, account_id, domain.
    Falls back to empty list if ChromaDB is unavailable.
    """
    chunks: list[dict] = []
    try:
        from kb.vector_store import get_kb_vector_store, KB_COLLECTION_CONTENT
        store = get_kb_vector_store()
        client = store._get_client()
        col = client.get_collection(KB_COLLECTION_CONTENT)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # ChromaDB $gte on strings is unreliable; filter in Python post-retrieval.
        raw = col.get(
            where={"user_id": {"$eq": user_id}},
            include=["documents", "metadatas"],
            limit=500,
        )
        docs = raw.get("documents") or []
        ids = raw.get("ids") or []
        metas = raw.get("metadatas") or []

        for cid, text, meta in zip(ids, docs, metas):
            if not text:
                continue
            ingested_at = (meta or {}).get("ingested_at", "")
            if ingested_at and not ingested_at.startswith(today):
                continue
            chunks.append({
                "id": cid,
                "text": text,
                "account_id": (meta or {}).get("account_id", ""),
                "domain": (meta or {}).get("domain", ""),
            })
    except Exception as exc:
        logger.warning("Could not fetch today's chunks from ChromaDB: %s", exc)
    return chunks


class ActionItemJob:
    """
    Nightly batch job: extract action items from today's ingested content.

    Usage::

        job = ActionItemJob(api_key="...", db_path="...")
        n = job.run(user_id="<uuid>")
    """

    MODEL = _EXTRACTION_MODEL
    SCHEDULE = "nightly"

    def __init__(
        self,
        api_key: Optional[str] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
        self._db_path = db_path or str(get_db_path())

    def run(self, user_id: str) -> int:
        """
        Run the full extraction pipeline for *user_id*.

        Returns total number of action items stored.
        """
        if not self._api_key:
            logger.debug("ActionItemJob: no API key configured, skipping")
            return 0

        # Step 1 — fetch today's chunks from ChromaDB
        all_chunks = _get_today_chunks(user_id, self._db_path)
        if not all_chunks:
            logger.debug("ActionItemJob: no chunks for today (user=%s)", user_id)
            return 0

        # Step 2 — pre-filter: keep only chunks with urgency signals
        candidates = [c for c in all_chunks if _has_urgency_keyword(c["text"])]
        if not candidates:
            logger.debug(
                "ActionItemJob: no urgency-keyword chunks for user=%s (%d total chunks)",
                user_id, len(all_chunks),
            )
            return 0

        logger.info(
            "ActionItemJob: %d/%d chunks pass urgency filter for user=%s",
            len(candidates), len(all_chunks), user_id,
        )

        # Step 3 — LLM batches
        total_stored = 0
        for batch_start in range(0, len(candidates), _MAX_CHUNKS_PER_BATCH):
            batch = candidates[batch_start: batch_start + _MAX_CHUNKS_PER_BATCH]
            stored = self._process_batch(batch, user_id)
            total_stored += stored

        if total_stored:
            logger.info(
                "ActionItemJob: stored %d action item(s) for user=%s",
                total_stored, user_id,
            )
        return total_stored

    def _process_batch(self, batch: list[dict], user_id: str) -> int:
        chunk_meta: dict[str, dict] = {}
        chunk_lines: list[str] = []
        for chunk in batch:
            cid = chunk["id"]
            chunk_meta[cid] = {
                "account_id": chunk.get("account_id", ""),
                "domain": chunk.get("domain", ""),
            }
            text = chunk["text"][:_MAX_CHARS_PER_CHUNK]
            account = chunk.get("account_id", "")
            domain = chunk.get("domain", "")
            chunk_lines.append(f"{cid} ||| {domain} ||| {account} ||| {text}")

        prompt = _EXTRACTION_PROMPT.format(chunks="\n".join(chunk_lines))
        raw = _call_llm(prompt, self._api_key)
        if not raw:
            return 0

        items = _parse_action_items(raw, chunk_meta)
        if not items:
            return 0

        return _store_action_items(items, user_id, self._db_path)


# ---------------------------------------------------------------------------
# Singleton helper (mirrors pattern used by other KB services)
# ---------------------------------------------------------------------------

_job: ActionItemJob | None = None


def get_action_item_job(
    api_key: Optional[str] = None,
    db_path: Optional[str] = None,
) -> ActionItemJob:
    global _job
    if _job is None:
        _job = ActionItemJob(api_key=api_key, db_path=db_path)
    return _job
