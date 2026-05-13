"""
KB Domain Events — Phase KB-E4 (Sprint 7).

Domain events for cross-context communication within the KB bounded context.
Events are plain frozen dataclasses: no infrastructure imports, no methods.

Subscribers are registered at startup in components/system/initializer.py.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentIngested:
    """
    Fired after a content chunk is successfully stored in ChromaDB (preprocessor step 8).

    Decouples the ingestion pipeline from downstream side effects:
      - Alert matching (AlertEngine)
      - Entity extraction + L2 discovery (EntityExtractor)
      - Discovery candidate upsert placeholder (DiscoveryManager — Sprint 8)

    Fields:
        chunk_id:       ID of the first stored chunk (representative for alert cosine check).
        account_id:     KB account ID of the content source.
        domains:        Pipe-separated domain labels (e.g. "macro|geopolitics").
        user_id:        Owner user ID for data isolation.
        published_at:   ISO 8601 publication timestamp, or None if unknown.
        ingestion_type: "scheduled" | "manual" | "backfill".
        text:           Full cleaned text (for entity extraction; may be large).
        source_url:     Content URL (for evidence tracking in entity/discovery pipeline).
        layer:          Account trust layer (1=L1 manually curated, 2=L2 agent-approved).
        embedding:      First chunk's embedding vector (1024-dim BAAI/bge-m3).
                        Stored as tuple for hashability in frozen dataclass.
    """
    chunk_id: str
    account_id: str
    domains: str
    user_id: str
    published_at: str | None
    ingestion_type: str
    text: str
    source_url: str
    layer: int
    embedding: tuple[float, ...]
