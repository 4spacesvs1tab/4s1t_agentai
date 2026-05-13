"""
KB Vector Store — ChromaDB service for the three KB collections.

Collections:
  kb_content    — main corpus (all text chunks from all sources)
  kb_summaries  — per-episode/article 3-sentence summaries
  kb_graph_ctx  — account bios and cross-platform descriptions

Embedding is performed externally (by the preprocessor via nano-gpt bge-m3).
This module handles only ChromaDB persistence and query.

Design reference: KnowledgeBase_design.md §6.4
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from kb.domain.value_objects import Platform, SimilarityScore
from utils.logger import setup_logger
logger = setup_logger(__name__)

_DEFAULT_CHROMA_PATH = "./data/chroma"

# ChromaDB collections used by the KB
KB_COLLECTION_CONTENT = "kb_content"
KB_COLLECTION_SUMMARIES = "kb_summaries"
KB_COLLECTION_GRAPH_CTX = "kb_graph_ctx"

ALL_KB_COLLECTIONS = (KB_COLLECTION_CONTENT, KB_COLLECTION_SUMMARIES, KB_COLLECTION_GRAPH_CTX)


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------

@dataclass
class KBChunk:
    """A single unit of KB content ready for vector storage."""
    id: str                           # unique chunk ID (UUID or hash)
    text: str                         # chunk text
    embedding: list[float]            # 1024-dim bge-m3 vector

    # Metadata (all primitive scalars — ChromaDB constraint)
    user_id: str = "default"
    account_id: str = ""              # "" for source-only content (e.g., bootstrap documents)
    domains: str = ""                 # pipe-separated domain IDs: "domain_a|domain_b"
    platform: str = ""                # 'website', 'twitter', 'nostr', 'youtube', 'podcast', 'rumble', 'manual'; Platform enum also accepted
    source: str = ""                  # source tag: 'website', 'twitter', or custom source_tag from kb_domains.yaml
    source_url: str = ""
    author: str = ""
    published_at: str = ""            # ISO 8601 string
    language: str = ""                # ISO 639-1: 'en', 'pl', etc.
    layer: int = 1                    # 1=L1, 2=L2
    ingestion_type: str = "scheduled" # 'scheduled' | 'manual'
    contradicts_chunk_id: str = ""    # chunk ID of contradicting chunk, if detected (G8)
    freshness_ttl_days: int = -1      # days until stale; -1=unclassified, 0=prediction (no expiry proxy)
    freshness_category: str = ""      # 'market_data'|'news'|'analysis'|'evergreen'|'periodic'|'prediction'|'general'

    def to_metadata(self) -> dict[str, Any]:
        """Return flat scalar dict for ChromaDB metadata."""
        return {
            "user_id": self.user_id,
            "account_id": self.account_id,
            "domains": self.domains,
            "platform": self.platform.value if isinstance(self.platform, Platform) else self.platform,
            "source": self.source,
            "source_url": self.source_url,
            "author": self.author,
            "published_at": self.published_at,
            "language": self.language,
            "layer": self.layer,
            "ingestion_type": self.ingestion_type,
            "contradicts_chunk_id": self.contradicts_chunk_id,
            "freshness_ttl_days": self.freshness_ttl_days,
            "freshness_category": self.freshness_category,
        }


# ---------------------------------------------------------------------------
# KBVectorStore
# ---------------------------------------------------------------------------

class KBVectorStore:
    """
    Manages the three KB ChromaDB collections.

    Embedding is handled upstream (preprocessor calls nano-gpt bge-m3).
    This class only stores pre-computed embeddings and runs queries.

    Thread-safety: ChromaDB PersistentClient is thread-safe for reads.
    Writes should be serialised at the preprocessor level.
    """

    def __init__(self, chroma_path: str | None = None) -> None:
        self._path = chroma_path or os.environ.get("CHROMA_PATH", _DEFAULT_CHROMA_PATH)
        self._client = None

    def _get_client(self):
        if self._client is None:
            import chromadb
            self._client = chromadb.PersistentClient(path=self._path)
        return self._client

    def initialize_collections(self) -> None:
        """Create the three KB collections if they do not exist."""
        client = self._get_client()
        for name in ALL_KB_COLLECTIONS:
            client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},  # cosine similarity for bge-m3
            )
        logger.info("KB collections initialised: %s", ALL_KB_COLLECTIONS)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert_chunks(
        self,
        collection_name: str,
        chunks: list[KBChunk],
    ) -> int:
        """
        Upsert chunks into *collection_name*.

        Returns number of chunks upserted.
        Uses upsert (not add) so re-ingestion of same chunk ID is idempotent.
        """
        if not chunks:
            return 0
        client = self._get_client()
        col = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        col.upsert(
            ids=[c.id for c in chunks],
            embeddings=[c.embedding for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[c.to_metadata() for c in chunks],
        )
        return len(chunks)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        collection_name: str,
        query_embedding: list[float],
        n_results: int = 20,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Query *collection_name* using a pre-computed embedding.

        Returns a list of result dicts:
          {id, text, metadata, score}
        where score is cosine similarity (0.0 – 1.0, higher = more relevant).

        ChromaDB returns distances; we convert: score = 1 - distance.
        """
        client = self._get_client()
        try:
            col = client.get_collection(collection_name)
        except Exception:
            return []

        count = col.count()
        if count == 0:
            return []

        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": min(n_results, count),
            "include": ["documents", "distances", "metadatas"],
        }
        if where:
            kwargs["where"] = where

        try:
            raw = col.query(**kwargs)
        except Exception as exc:
            logger.error("ChromaDB query failed on %s: %s", collection_name, exc)
            return []

        results = []
        ids = raw.get("ids", [[]])[0]
        docs = raw.get("documents", [[]])[0]
        distances = raw.get("distances", [[]])[0]
        metas = raw.get("metadatas", [[]])[0] or [{}] * len(ids)

        for chunk_id, doc, dist, meta in zip(ids, docs, distances, metas):
            results.append({
                "id": chunk_id,
                "text": doc,
                "metadata": meta or {},
                "score": SimilarityScore(round(1.0 - float(dist), 4)),
            })
        return results

    def document_exists(self, collection_name: str, chunk_id: str) -> bool:
        """Return True if a chunk with *chunk_id* exists in *collection_name*."""
        client = self._get_client()
        try:
            col = client.get_collection(collection_name)
            result = col.get(ids=[chunk_id], include=[])
            return bool(result.get("ids"))
        except Exception:
            return False

    def collection_count(self, collection_name: str) -> int:
        """Return number of documents in *collection_name*, or 0 if not found."""
        client = self._get_client()
        try:
            return client.get_collection(collection_name).count()
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_store: KBVectorStore | None = None


def get_kb_vector_store(chroma_path: str | None = None) -> KBVectorStore:
    """Return the shared KBVectorStore singleton."""
    global _store
    if _store is None:
        _store = KBVectorStore(chroma_path)
        _store.initialize_collections()
    return _store
