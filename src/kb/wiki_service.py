"""
KB Wiki Service — Phase KB-23.

Generates and persists structured markdown reference pages ("wiki pages")
about topics on demand, synthesised from KB content.

Pipeline:
  1. Normalise topic → URL slug (lowercase, hyphens).
  2. If a page exists and force_refresh=False → return cached version.
  3. Embed the topic query and fetch up to MAX_FETCH chunks from ChromaDB.
  4. Call GLM 4.7 with the chunks → produce a structured markdown page.
  5. Upsert into kb_wiki_pages (version incremented on refresh).

Token cost: ~7K tokens per call (on-demand only).
Model: GLM 4.7 via nano-gpt.

Design reference: KB_assistant_design_v2.md §17 KB-23
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from kb.exceptions import EmbeddingError
from kb.ports.embedding_port import EmbeddingPort
from utils.logger import setup_logger
logger = setup_logger(__name__)

_EMBEDDING_MODEL = "BAAI/bge-m3"
_WIKI_MODEL = "glm-4-7"
_MAX_FETCH = 15
_MAX_CHARS_PER_CHUNK = 500
_DEFAULT_CHROMA_PATH = "./data/chroma"

_WIKI_PROMPT = """\
You are a knowledge-base analyst. Below are {count} excerpts from multiple sources about "{topic}".
Synthesise them into a structured reference wiki page in Markdown.

Use this exact structure (keep the headings):

# {title}

## Overview
A 2–3 sentence summary: what this topic is and why it matters.

## Key Concepts
Bullet list of the most important ideas, terms, or mechanisms related to this topic.

## Current State
What the sources say is happening right now. Include dates or periods where relevant.

## Key Sources
A brief list of which sources cover this topic and their angle/emphasis.

## Related Topics
A bullet list of adjacent topics worth exploring.

---

Excerpts:
{chunks}

Rules:
- Write in neutral, factual language.
- Do not invent information not present in the excerpts.
- If the excerpts are thin, write what you can and keep sections concise.
- Output only the Markdown — no preamble, no code fences.
"""


def _slugify(text: str) -> str:
    """Normalise a topic title to a URL-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:120]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _call_llm(prompt: str, api_key: str) -> str:
    import httpx
    base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": _WIKI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 900,
            },
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=90.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("LLM call failed in wiki_service: %s", exc)
        return ""


def _extract_title(content: str, fallback: str) -> str:
    """Extract the H1 title from the generated markdown."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


class WikiPageService:
    """
    On-demand wiki page generation and persistence for KB-23.

    Usage::

        svc = WikiPageService(api_key="...", chroma_path="...", db_path="...")
        result = svc.generate(user_id="<uuid>", topic="Fed rate policy")
        page   = svc.get(user_id="<uuid>", topic="fed-rate-policy")
        pages  = svc.list(user_id="<uuid>")
        svc.delete(page_id="...", user_id="<uuid>")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        chroma_path: Optional[str] = None,
        db_path: Optional[str] = None,
        *,
        embedding_port: EmbeddingPort,
    ) -> None:
        self._api_key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
        self._chroma_path = chroma_path or os.environ.get("CHROMA_PATH", _DEFAULT_CHROMA_PATH)
        self._embedding_port: EmbeddingPort = embedding_port
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_path and db_url.startswith("sqlite:///"):
            db_path = db_url[len("sqlite:///"):]
        self._db_path = db_path or str(get_db_path())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        user_id: str,
        topic: str,
        force_refresh: bool = False,
    ) -> dict:
        """
        Generate (or refresh) a wiki page for *topic*.

        Returns a result dict with keys:
            page_id, user_id, topic (slug), title, content, version,
            created_at, updated_at, source_chunk_count, cached (bool), error
        """
        if not topic or not topic.strip():
            return self._error("'topic' is required")

        slug = _slugify(topic)
        title_hint = topic.strip().title()

        # Return cached page if it exists and refresh not requested
        if not force_refresh:
            existing = self._load_page(user_id, slug)
            if existing:
                existing["cached"] = True
                existing["error"] = None
                return existing

        # 1. Embed the topic
        try:
            embedding = self._embedding_port.embed([topic])[0]
        except EmbeddingError as exc:
            return self._error(f"Embedding failed: {exc}")

        # 2. Fetch chunks from ChromaDB
        chunks = self._query_chroma(embedding, user_id, n=_MAX_FETCH)
        if not chunks:
            return self._error(
                f"No KB content found for topic '{topic}'. "
                "Ingest some content first, then retry."
            )

        # 3. Build prompt
        chunk_text = "\n\n".join(
            f"[{c.get('account_id', 'unknown')} | {c.get('published_at', '')[:10]}] "
            f"{c.get('text', '')[:_MAX_CHARS_PER_CHUNK]}"
            for c in chunks
        )
        prompt = _WIKI_PROMPT.format(
            count=len(chunks),
            topic=topic.strip(),
            title=title_hint,
            chunks=chunk_text,
        )

        # 4. Call LLM
        content = _call_llm(prompt, self._api_key)
        if not content:
            return self._error("LLM call failed — no content returned")

        title = _extract_title(content, title_hint)
        source_chunk_ids = [c.get("chunk_id", "") for c in chunks if c.get("chunk_id")]

        # 5. Upsert into DB
        page_id = self._upsert_page(user_id, slug, title, content, source_chunk_ids)

        page = self._load_page(user_id, slug)
        if page:
            page["cached"] = False
            page["error"] = None
            return page

        # Fallback (shouldn't happen)
        return {
            "page_id": page_id,
            "user_id": user_id,
            "topic": slug,
            "title": title,
            "content": content,
            "version": 1,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "source_chunk_count": len(source_chunk_ids),
            "cached": False,
            "error": None,
        }

    def get(self, user_id: str, topic: str) -> Optional[dict]:
        """Return an existing wiki page by topic slug or title (None if not found)."""
        slug = _slugify(topic)
        page = self._load_page(user_id, slug)
        if page:
            page["cached"] = True
            page["error"] = None
        return page

    def list(self, user_id: str) -> list[dict]:
        """Return an index of all wiki pages for user_id (no content body)."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, user_id, topic, title, version, created_at, updated_at,
                       json_array_length(source_chunks) AS source_chunk_count
                FROM kb_wiki_pages
                WHERE user_id = ?
                ORDER BY updated_at DESC
                """,
                (user_id,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("wiki list failed: %s", exc)
            return []

    def delete(self, page_id: str, user_id: str) -> bool:
        """Delete a wiki page by ID (owner check via user_id)."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "DELETE FROM kb_wiki_pages WHERE id = ? AND user_id = ?",
                (page_id, user_id),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as exc:
            logger.warning("wiki delete failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_page(self, user_id: str, slug: str) -> Optional[dict]:
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT id AS page_id, user_id, topic, title, content, version,
                       created_at, updated_at,
                       json_array_length(source_chunks) AS source_chunk_count
                FROM kb_wiki_pages
                WHERE user_id = ? AND topic = ?
                """,
                (user_id, slug),
            ).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as exc:
            logger.warning("wiki load failed: %s", exc)
            return None

    def _upsert_page(
        self,
        user_id: str,
        slug: str,
        title: str,
        content: str,
        source_chunk_ids: list[str],
    ) -> str:
        now = _now_iso()
        try:
            conn = sqlite3.connect(self._db_path)
            existing = conn.execute(
                "SELECT id, version FROM kb_wiki_pages WHERE user_id = ? AND topic = ?",
                (user_id, slug),
            ).fetchone()

            if existing:
                page_id = existing[0]
                new_version = existing[1] + 1
                conn.execute(
                    """
                    UPDATE kb_wiki_pages
                    SET title = ?, content = ?, source_chunks = ?, version = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (title, content, json.dumps(source_chunk_ids), new_version, now, page_id),
                )
            else:
                page_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO kb_wiki_pages
                        (id, user_id, topic, title, content, source_chunks, version,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (page_id, user_id, slug, title, content,
                     json.dumps(source_chunk_ids), now, now),
                )

            conn.commit()
            conn.close()
            logger.info("Upserted wiki page id=%s topic=%r user=%s", page_id, slug, user_id)
            return page_id
        except Exception as exc:
            logger.error("wiki upsert failed: %s", exc)
            raise

    def _query_chroma(self, embedding: list[float], user_id: str, n: int) -> list[dict]:
        try:
            import chromadb
            client = chromadb.PersistentClient(path=self._chroma_path)
            col = client.get_or_create_collection("kb_content")
            results = col.query(
                query_embeddings=[embedding],
                n_results=n,
                where={"user_id": {"$eq": user_id}},
                include=["documents", "metadatas"],
            )
            docs = (results.get("documents") or [[]])[0]
            metas = (results.get("metadatas") or [[]])[0]
            chunks = []
            for text, meta in zip(docs, metas):
                entry = dict(meta or {})
                entry["text"] = text or ""
                chunks.append(entry)
            return chunks
        except Exception as exc:
            logger.warning("ChromaDB query failed in wiki_service: %s", exc)
            return []

    @staticmethod
    def _error(msg: str) -> dict:
        return {
            "page_id": None,
            "user_id": None,
            "topic": None,
            "title": None,
            "content": None,
            "version": None,
            "created_at": None,
            "updated_at": None,
            "source_chunk_count": 0,
            "cached": False,
            "error": msg,
        }


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service: WikiPageService | None = None


def get_wiki_service(
    api_key: Optional[str] = None,
    chroma_path: Optional[str] = None,
    db_path: Optional[str] = None,
    embedding_port: Optional[EmbeddingPort] = None,
) -> WikiPageService:
    """Return the shared WikiPageService singleton.

    On first call, *embedding_port* must be provided.  Subsequent calls may
    omit it — the singleton already holds a reference.
    """
    global _service
    if _service is None:
        if embedding_port is None:
            from infrastructure.embedding.nano_gpt_embedding_adapter import NanoGptEmbeddingAdapter
            _key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
            _base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
            embedding_port = NanoGptEmbeddingAdapter(api_key=_key, base_url=_base)
        _service = WikiPageService(
            api_key=api_key,
            chroma_path=chroma_path,
            db_path=db_path,
            embedding_port=embedding_port,
        )
    return _service
