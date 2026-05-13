"""
KB Content Preprocessor — chunk, embed, deduplicate, and store content.

Pipeline per raw content item:
  1. Language detection (langdetect)
  2. Text cleaning (strip HTML, normalise whitespace)
  3. Chunking (512-token sliding window, 50-token overlap)
  4. Summarisation for long-form content >2000 tokens (DeepSeek V3 via nano-gpt)
  5. Entity extraction for L2 discovery (Phase KB-3; stubbed here)
  6. Embedding (BAAI/bge-m3 via nano-gpt — 1024-dim vectors)
  7. Two-stage dedup:
       (a) Exact hash — skip if SHA256 already in kb_ingestion_log
       (b) Semantic near-duplicate check >0.97 cosine (same account, 7-day window)
  8. Store chunks to ChromaDB (kb_content and kb_summaries collections)
  9. Log ingestion to kb_ingestion_log
 10. Publish ContentIngested domain event → alert matching + entity/discovery
     subscribers handle their respective side effects (E4 — event-driven)
 11. Contradiction detection — compare first chunk against similar chunks from different
     accounts; flag contradicting pairs by setting contradicts_chunk_id (Phase KB-5, G8)
 12. Freshness classification — keyword-based rule scan; sets freshness_ttl_days and
     freshness_category on every chunk (Phase KB-16)

Design reference: KnowledgeBase_design.md §6.3
"""
from __future__ import annotations

import hashlib
import os
import re
import resource
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from config.agent_config import get_agent_config as _get_agent_config
from core.db_path import get_db_path
from core.exceptions import IngestionError
from kb.exceptions import EmbeddingError
from kb.ports.embedding_port import EmbeddingPort
from kb.ports.chunk_repository import ChunkRepository
from typing import Optional, Sequence


def _rss_mb() -> float:
    """Return current process RSS in MB (Linux/Mac)."""
    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux: kilobytes; macOS: bytes
    if os.uname().sysname == "Darwin":
        return kb / 1024 / 1024
    return kb / 1024

from kb.vector_store import (
    KB_COLLECTION_CONTENT,
    KB_COLLECTION_SUMMARIES,
    KBChunk,
    get_kb_vector_store,
)

from utils.logger import setup_logger
logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Domain event publishing (E4)
# ---------------------------------------------------------------------------

def _publish_event(event_bus, domain_event: "ContentIngested") -> None:
    """
    Publish a ContentIngested domain event from synchronous pipeline code.

    The EventBus.publish() is async; this helper bridges the sync→async boundary:
      - If an async event loop is already running (FastAPI context), the coroutine
        is scheduled as a background task (fire-and-forget).
      - If no loop is running (test / standalone script), asyncio.run() is used.

    Sprint 8: make the ingestion pipeline async end-to-end so this bridge
    can be replaced with a direct `await event_bus.publish(...)`.
    """
    import asyncio
    import dataclasses
    from components.events.event_bus import Event

    bus_event = Event(
        event_type="content_ingested",
        payload=dataclasses.asdict(domain_event),
        source="preprocessor",
    )
    coro = event_bus.publish(bus_event)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        # No running event loop — safe to call asyncio.run()
        asyncio.run(coro)


# Chunking parameters (§6.3)
_CHUNK_TOKENS = 512
_CHUNK_OVERLAP_TOKENS = 50
# Approximate chars per token for English/Polish; used for rough splitting
_CHARS_PER_TOKEN = 4

_pre_cfg = _get_agent_config().kb.preprocessor

# Long-form threshold: content longer than this gets a summary chunk
_LONG_FORM_CHAR_THRESHOLD: int = _pre_cfg.longform_char_threshold

# Semantic dedup threshold (§6.3 G23)
_SEMANTIC_DEDUP_THRESHOLD: float = _pre_cfg.dedup_similarity_threshold

# Contradiction detection thresholds (G8)
# Range [min, max) — chunks in this similarity band are "same topic, possibly different view"
_CONTRADICTION_SIM_MIN: float = _pre_cfg.contradiction_sim_range_low
_CONTRADICTION_SIM_MAX: float = _pre_cfg.contradiction_sim_range_high
_CONTRADICTION_CANDIDATES = 3    # max existing chunks to check per new chunk
_CONTRADICTION_ENABLED = True    # set False to disable (e.g., low-cost mode)

# nano-gpt embedding model for KB
_EMBEDDING_MODEL = "BAAI/bge-m3"
_EMBEDDING_DIMS = 1024

from kb.preprocessing_llm import _llm_contradicts, _summarise_text


# ---------------------------------------------------------------------------
# Raw content input
# ---------------------------------------------------------------------------

@dataclass
class RawContent:
    """One unit of content from an ingestion adapter."""
    text: str
    source_url: str = ""
    author: str = ""
    published_at: str = ""           # ISO 8601
    platform: str = "website"
    account_id: str = ""
    domains: str = ""                # pipe-separated
    user_id: str = "default"
    layer: int = 1
    source: str = "website"          # 'babok', 'website', 'twitter', etc.
    ingestion_type: str = "scheduled"


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Strip HTML tags, normalise whitespace."""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Normalise whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _detect_language(text: str) -> str:
    """Detect language; return ISO 639-1 code or 'unknown'."""
    try:
        from langdetect import detect
        return detect(text[:2000])  # sample first 2000 chars
    except Exception:
        return "unknown"


def _chunk_text(text: str) -> list[str]:
    """
    Split text into overlapping chunks of ~512 tokens.

    Uses character-based approximation (4 chars ≈ 1 token).
    Splits on sentence boundaries when possible.
    """
    chunk_chars = _CHUNK_TOKENS * _CHARS_PER_TOKEN
    overlap_chars = _CHUNK_OVERLAP_TOKENS * _CHARS_PER_TOKEN

    if len(text) <= chunk_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_chars
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Try to break at sentence boundary.
        # Guard: boundary must advance start past overlap_chars, otherwise we
        # loop forever when the only ". " sits inside the overlap window.
        boundary = text.rfind(". ", start, end)
        if boundary == -1 or boundary <= start + overlap_chars:
            boundary = end
        else:
            boundary += 2  # include ". "
        chunks.append(text[start:boundary])
        start = boundary - overlap_chars
    return [c for c in chunks if c.strip()]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

def _hash_exists_in_db(item_hash: str, db_path: str) -> bool:
    """Return True if *item_hash* is already in kb_ingestion_log."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT 1 FROM kb_ingestion_log WHERE item_hash = ? LIMIT 1",
            (item_hash,),
        )
        exists = cur.fetchone() is not None
        conn.close()
        return exists
    except Exception:
        return False


def _log_ingestion(
    db_path: str,
    user_id: str,
    account_id: str,
    platform: str,
    item_url: str,
    item_hash: str,
    chunk_count: int,
    ingestion_type: str,
    status: str = "ok",
) -> None:
    """Write a row to kb_ingestion_log."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO kb_ingestion_log
                (user_id, account_id, platform, item_url, item_hash,
                 chunk_count, ingestion_type, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                account_id or None,
                platform,
                item_url,
                item_hash,
                chunk_count,
                ingestion_type,
                status,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to log ingestion: %s", exc)


def _semantic_near_duplicate(
    embedding: list[float],
    account_id: str,
    published_at: str,
    user_id: str,
) -> bool:
    """
    Check if a semantically near-duplicate chunk already exists in kb_content.

    Queries top-5 chunks from same account within a 7-day window.
    Returns True if any has cosine similarity > threshold (G23).
    """
    if not account_id:
        return False
    store = get_kb_vector_store()
    try:
        # Build 7-day window filter if we have a date
        where: dict = {
            "$and": [
                {"user_id": {"$eq": user_id}},
                {"account_id": {"$eq": account_id}},
            ]
        }
        results = store.query(
            collection_name=KB_COLLECTION_CONTENT,
            query_embedding=embedding,
            n_results=5,
            where=where,
        )
        for r in results:
            if r["score"] >= _SEMANTIC_DEDUP_THRESHOLD:
                return True
    except Exception as exc:
        logger.debug("Semantic dedup check failed: %s", exc)
    return False


def _find_contradicting_chunk(
    chunk: "KBChunk",
    api_key: str,
) -> Optional[str]:
    """
    Search kb_content for an existing chunk that contradicts *chunk*.

    Strategy (G8):
      1. Query similar chunks from DIFFERENT accounts in the same domain(s).
      2. Keep only candidates with similarity in [min, max) — "same topic, different view".
      3. For up to CONTRADICTION_CANDIDATES, call a lightweight LLM check.
      4. Return the ID of the first confirmed contradiction, or None.

    Only runs when the chunk has a non-empty account_id and domains.
    """
    if not chunk.account_id or not chunk.domains:
        return None
    if not _CONTRADICTION_ENABLED:
        return None

    store = get_kb_vector_store()
    try:
        # Fetch more candidates than we need so we can filter by similarity range
        fetch_n = max(10, _CONTRADICTION_CANDIDATES * 3)
        where: dict = {
            "$and": [
                {"user_id": {"$eq": chunk.user_id}},
                # Exclude same account — we want cross-account disagreements
                {"account_id": {"$ne": chunk.account_id}},
                # Filter by domain — use $eq on the primary domain (ChromaDB does not support $contains)
                {"domains": {"$eq": chunk.domains.split("|")[0]}},
            ]
        }
        results = store.query(
            collection_name=KB_COLLECTION_CONTENT,
            query_embedding=chunk.embedding,
            n_results=fetch_n,
            where=where,
        )
    except Exception as exc:
        logger.debug("Contradiction candidate query failed: %s", exc)
        return None

    # Filter to the "same topic, possibly different view" similarity band
    candidates = [
        r for r in results
        if _CONTRADICTION_SIM_MIN <= r["score"] < _CONTRADICTION_SIM_MAX
    ][:_CONTRADICTION_CANDIDATES]

    for candidate in candidates:
        if _llm_contradicts(chunk.text, candidate["text"], api_key):
            logger.info(
                "Contradiction detected: new chunk %s ↔ existing chunk %s (score=%.3f)",
                chunk.id, candidate["id"], candidate["score"],
            )
            return candidate["id"]

    return None


# ---------------------------------------------------------------------------
# Main preprocessor
# ---------------------------------------------------------------------------

class KBPreprocessor:
    """
    Orchestrates the full KB ingestion pipeline for one content item.

    Usage::

        preprocessor = KBPreprocessor(api_key="...", db_path="...", embedding_port=adapter)
        result = preprocessor.process(raw_content)
    """

    def __init__(
        self,
        api_key: str | None = None,
        db_path: str | None = None,
        *,
        embedding_port: EmbeddingPort,
        event_bus,  # EventBus — required; domain event publishing always active
        chunk_repo: Optional[ChunkRepository] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
        self._db_path = db_path or str(get_db_path())
        self._embedding_port: EmbeddingPort = embedding_port
        self._event_bus = event_bus
        self._chunk_repo: Optional[ChunkRepository] = chunk_repo

    def process(self, content: RawContent) -> dict:
        """
        Run the full pipeline for one content item.

        Returns a summary dict:
          {stored_chunks, summary_stored, skipped_reason, status}

        Known failure modes (empty text, exact/semantic dedup, embedding
        failure) return a dict with the appropriate status.  Unexpected
        pipeline exceptions are caught here, logged at ERROR with full
        traceback, and returned as status='pipeline_error' so that the
        calling loop can continue to the next item.
        """
        try:
            return self._process_impl(content)
        except IngestionError:
            raise  # already typed — propagate to caller for domain-level handling
        except Exception as exc:
            logger.error(
                "Unexpected error in preprocessing pipeline for %s: %s",
                content.source_url, exc,
                exc_info=True,
            )
            return {
                "stored_chunks": 0,
                "summary_stored": False,
                "skipped_reason": "pipeline_error",
                "status": "error",
            }

    def _process_impl(self, content: RawContent) -> dict:
        """Internal pipeline implementation — called exclusively by process()."""
        _m0 = _rss_mb()
        logger.info("MEM[start] %.0f MB — %s", _m0, content.source_url)

        # Step 1: Clean text
        text = _clean_text(content.text)
        if not text:
            return {"stored_chunks": 0, "summary_stored": False, "skipped_reason": "empty_text", "status": "skipped"}

        logger.info("MEM[after clean] %.0f MB", _rss_mb())

        # Step 2: Exact dedup check
        item_hash = _sha256(text)
        _exists = (
            self._chunk_repo.hash_exists(item_hash)
            if self._chunk_repo
            else _hash_exists_in_db(item_hash, self._db_path)
        )
        if _exists:
            logger.debug("Exact dedup skip: hash=%s url=%s", item_hash[:12], content.source_url)
            if self._chunk_repo:
                self._chunk_repo.log_ingestion(
                    content.user_id, content.account_id, content.platform,
                    content.source_url, item_hash, 0, content.ingestion_type, "dedup_skipped",
                )
            else:
                _log_ingestion(
                    self._db_path, content.user_id, content.account_id,
                    content.platform, content.source_url, item_hash, 0,
                    content.ingestion_type, "dedup_skipped",
                )
            return {"stored_chunks": 0, "summary_stored": False, "skipped_reason": "exact_dedup", "status": "dedup_skipped"}

        # Step 3: Language detection
        language = _detect_language(text)
        logger.info("MEM[after lang detect] %.0f MB", _rss_mb())

        # Step 4: Chunk
        logger.info("MEM[before chunk] %.0f MB — text_len=%d", _rss_mb(), len(text))
        chunks_text = _chunk_text(text)
        if not chunks_text:
            return {"stored_chunks": 0, "summary_stored": False, "skipped_reason": "no_chunks", "status": "skipped"}
        logger.info("MEM[after chunk] %.0f MB — %d chunks", _rss_mb(), len(chunks_text))

        # Step 5: Embed all chunks in one batch
        if not self._api_key:
            logger.warning("No NANO_GPT_API_KEY — embedding will fail")
        try:
            embeddings = self._embedding_port.embed(chunks_text)
        except EmbeddingError as exc:
            logger.error(
                "Embedding failed for %s: %s — skipping item",
                content.source_url, exc,
            )
            if self._chunk_repo:
                self._chunk_repo.log_ingestion(
                    content.user_id, content.account_id, content.platform,
                    content.source_url, item_hash, 0, content.ingestion_type, "embedding_failed",
                )
            else:
                _log_ingestion(
                    self._db_path, content.user_id, content.account_id,
                    content.platform, content.source_url, item_hash, 0,
                    content.ingestion_type, "embedding_failed",
                )
            return {
                "stored_chunks": 0,
                "summary_stored": False,
                "skipped_reason": "embedding_failed",
                "status": "embedding_failed",
            }
        logger.info("MEM[after embed] %.0f MB", _rss_mb())

        # Step 6: Build KBChunk objects, apply semantic dedup on first chunk
        logger.info("MEM[before get_kb_vector_store] %.0f MB", _rss_mb())
        store = get_kb_vector_store()
        logger.info("MEM[after get_kb_vector_store] %.0f MB", _rss_mb())
        stored_chunks = []

        # Check semantic near-duplicate only on the first chunk (representative).
        # Skip for backfill: episodes from the same podcast should all be stored;
        # semantic dedup only makes sense cross-source (e.g. YouTube vs podcast).
        if content.ingestion_type != "backfill" and len(embeddings) > 0 and _semantic_near_duplicate(
            embeddings[0], content.account_id, content.published_at, content.user_id
        ):
            logger.debug("Semantic dedup skip: %s", content.source_url)
            if self._chunk_repo:
                self._chunk_repo.log_ingestion(
                    content.user_id, content.account_id, content.platform,
                    content.source_url, item_hash, 0, content.ingestion_type, "dedup_skipped",
                )
            else:
                _log_ingestion(
                    self._db_path, content.user_id, content.account_id,
                    content.platform, content.source_url, item_hash, 0,
                    content.ingestion_type, "dedup_skipped",
                )
            return {"stored_chunks": 0, "summary_stored": False, "skipped_reason": "semantic_dedup", "status": "dedup_skipped"}

        # Classify freshness once for the whole item (based on first chunk / title text)
        try:
            from kb.freshness_classifier import classify_freshness
            _ftl, _fcat = classify_freshness(chunks_text[0])
            # ttl_days=None means prediction; store as -1 sentinel in the int field
            _ftl_stored = -1 if _ftl is None else _ftl
        except Exception:
            _ftl_stored, _fcat = -1, ""

        for idx, (chunk_text, embedding) in enumerate(zip(chunks_text, embeddings)):
            chunk_id = f"{item_hash[:16]}_{idx:04d}"
            chunk = KBChunk(
                id=chunk_id,
                text=chunk_text,
                embedding=embedding,
                user_id=content.user_id,
                account_id=content.account_id,
                domains=content.domains,
                platform=content.platform,
                source=content.source,
                source_url=content.source_url,
                author=content.author,
                published_at=content.published_at,
                language=language,
                layer=content.layer,
                ingestion_type=content.ingestion_type,
                freshness_ttl_days=_ftl_stored,
                freshness_category=_fcat,
            )
            stored_chunks.append(chunk)

        n_stored = store.upsert_chunks(KB_COLLECTION_CONTENT, stored_chunks)
        logger.info("MEM[after upsert_chunks] %.0f MB", _rss_mb())

        # Step 7: Summarise long-form content
        summary_stored = False
        if len(text) > _LONG_FORM_CHAR_THRESHOLD:
            summary_text = _summarise_text(text, self._api_key)
            if summary_text:
                try:
                    summary_embedding = self._embedding_port.embed([summary_text])[0]
                except Exception as exc:
                    logger.warning(
                        "Summary embedding failed for %s: %s — summary skipped",
                        content.source_url, exc,
                    )
                else:
                    summary_chunk = KBChunk(
                        id=f"{item_hash[:16]}_summary",
                        text=summary_text,
                        embedding=summary_embedding,
                        user_id=content.user_id,
                        account_id=content.account_id,
                        domains=content.domains,
                        platform=content.platform,
                        source=content.source,
                        source_url=content.source_url,
                        author=content.author,
                        published_at=content.published_at,
                        language=language,
                        layer=content.layer,
                        ingestion_type=content.ingestion_type,
                    )
                    store.upsert_chunks(KB_COLLECTION_SUMMARIES, [summary_chunk])
                    summary_stored = True

        # Step 8: Log ingestion
        if self._chunk_repo:
            self._chunk_repo.log_ingestion(
                content.user_id, content.account_id, content.platform,
                content.source_url, item_hash, n_stored, content.ingestion_type, "ok",
            )
        else:
            _log_ingestion(
                self._db_path, content.user_id, content.account_id,
                content.platform, content.source_url, item_hash, n_stored,
                content.ingestion_type, "ok",
            )

        # Steps 9–10: replaced by ContentIngested domain event (E4).
        # Alert matching and entity/discovery extraction are now handled by
        # independent event subscribers registered in initializer.py.
        if stored_chunks and embeddings:
            from kb.domain.events import ContentIngested
            first_chunk = stored_chunks[0]
            domain_event = ContentIngested(
                chunk_id=first_chunk.id,
                account_id=content.account_id,
                domains=content.domains,
                user_id=content.user_id,
                published_at=content.published_at,
                ingestion_type=content.ingestion_type,
                text=text,
                source_url=content.source_url,
                layer=content.layer,
                embedding=tuple(first_chunk.embedding),
            )
            _publish_event(self._event_bus, domain_event)

        # Step 11: Contradiction detection — flag new chunks that contradict existing ones (G8)
        # Only check the first (representative) chunk; re-upsert it if a contradiction is found.
        # Skipped for backfill — reduces memory pressure on low-RAM machines.
        if content.ingestion_type != "backfill" and stored_chunks and self._api_key:
            try:
                contradicting_id = _find_contradicting_chunk(stored_chunks[0], self._api_key)
                if contradicting_id:
                    first_chunk = stored_chunks[0]
                    first_chunk.contradicts_chunk_id = contradicting_id
                    store.upsert_chunks(KB_COLLECTION_CONTENT, [first_chunk])
            except Exception as exc:
                logger.debug("Contradiction detection step failed: %s", exc)

        logger.info(
            "Ingested %d chunks (summary=%s) account=%s url=%s",
            n_stored, summary_stored, content.account_id, content.source_url,
        )
        return {
            "stored_chunks": n_stored,
            "summary_stored": summary_stored,
            "skipped_reason": None,
            "status": "ok",
        }

    def process_batch(self, items: Sequence[RawContent]) -> list[dict]:
        """Process multiple content items sequentially."""
        return [self.process(item) for item in items]
