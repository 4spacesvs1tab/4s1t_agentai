#!/usr/bin/env python3
"""
Generic PDF/document → ChromaDB bootstrap loader.

Loads one or more static reference documents (PDFs) into the KB vector store.
Document metadata (source_tag, domain, author, published_at, path) comes from
CLI arguments or from bootstrap_sources entries in kb_domains.yaml — no
document-specific values are hardcoded in this script.

Re-running is safe: chunks are upserted (idempotent).

Usage — single document:
    python3 src/kb/bootstrap/document_loader.py \\
        --pdf path/to/document.pdf \\
        --source-tag my_source \\
        --domain my_domain \\
        --author "Publisher Name" \\
        --published-at 2020-01-01

Usage — all bootstrap_sources defined in kb_domains.yaml:
    python3 src/kb/bootstrap/document_loader.py --all

Usage — dry run (count chunks without storing):
    python3 src/kb/bootstrap/document_loader.py --all --dry-run

Design reference: KB_privacyEnhancements_design.md §4 Phase 3
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import uuid
from pathlib import Path

# Allow running from repo root
_SRC = Path(__file__).parent.parent.parent
_REPO_ROOT = _SRC.parent
sys.path.insert(0, str(_SRC))

from kb.ports.embedding_port import EmbeddingPort
from kb.vector_store import KB_COLLECTION_CONTENT, KBChunk, get_kb_vector_store

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
from utils.logger import setup_logger
logger = setup_logger(__name__)

_CHUNK_CHARS = 512 * 4   # ~512 tokens × 4 chars/token
_OVERLAP_CHARS = 50 * 4
_BATCH_SIZE = 10          # chunks per embedding API call


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def _extract_pdf_text(pdf_path: str) -> list[dict]:
    """
    Extract text from a PDF. Returns list of {page, text} dicts.

    Tries PyPDF2 first (memory-efficient); falls back to pdfplumber.
    """
    try:
        import PyPDF2
        pages = []
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for i, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append({"page": i, "text": text})
        logger.info("PyPDF2: extracted %d pages", len(pages))
        return pages
    except ImportError:
        pass

    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append({"page": i, "text": text})
        logger.info("pdfplumber: extracted %d pages", len(pages))
        return pages
    except ImportError:
        pass

    raise RuntimeError(
        "No PDF library available. Install PyPDF2 or pdfplumber:\n"
        "  pip install PyPDF2  # or: pip install pdfplumber"
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_page(page_num: int, text: str) -> list[dict]:
    """Chunk page text into overlapping segments. Returns list of {page, text}."""
    text = _clean(text)
    if len(text) <= _CHUNK_CHARS:
        return [{"page": page_num, "text": text}]

    chunks = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_CHARS
        if end >= len(text):
            chunks.append({"page": page_num, "text": text[start:]})
            break
        boundary = text.rfind(". ", start, end)
        if boundary <= start:
            boundary = end
        else:
            boundary += 2
        chunks.append({"page": page_num, "text": text[start:boundary]})
        start = boundary - _OVERLAP_CHARS
    return [c for c in chunks if c["text"].strip()]


# ---------------------------------------------------------------------------
# Core loader
# ---------------------------------------------------------------------------

def load_document(
    pdf_path: str,
    source_tag: str,
    domain: str,
    author: str,
    published_at: str,
    user_id: str = "default",
    api_key: str | None = None,
    dry_run: bool = False,
    embedding_port: EmbeddingPort | None = None,
) -> int:
    """
    Load a PDF into the KB ChromaDB collection.

    Args:
        pdf_path:     Path to the PDF file.
        source_tag:   Source identifier stored in ChromaDB metadata (e.g. 'my_doc').
        domain:       Domain ID this document belongs to (e.g. 'ba').
        author:       Author or publisher name.
        published_at: ISO 8601 publication date string (e.g. '2015-01-01').
        user_id:      KB user scope (default: 'default').
        api_key:      Embedding API key (default: NANO_GPT_API_KEY env var).
        dry_run:      If True, count chunks without storing anything.

    Returns:
        Number of chunks stored (or that would be stored in dry_run mode).
    """
    if not Path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
    if embedding_port is None:
        from infrastructure.embedding.nano_gpt_embedding_adapter import NanoGptEmbeddingAdapter
        _base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
        embedding_port = NanoGptEmbeddingAdapter(api_key=key, base_url=_base)

    logger.info("Loading document: %s (source_tag=%s, domain=%s)", pdf_path, source_tag, domain)
    pages = _extract_pdf_text(pdf_path)
    logger.info("Extracted %d non-empty pages", len(pages))

    import gc
    gc.collect()

    all_chunks_raw: list[dict] = []
    for page_data in pages:
        all_chunks_raw.extend(_chunk_page(page_data["page"], page_data["text"]))

    logger.info("Total chunks: %d", len(all_chunks_raw))

    if dry_run:
        logger.info("Dry run — not storing anything")
        return len(all_chunks_raw)

    store = get_kb_vector_store()
    total_stored = 0

    for batch_start in range(0, len(all_chunks_raw), _BATCH_SIZE):
        batch = all_chunks_raw[batch_start:batch_start + _BATCH_SIZE]
        texts = [c["text"] for c in batch]
        embeddings = embedding_port.embed(texts)

        kb_chunks = []
        for raw, embedding in zip(batch, embeddings):
            chunk_id = hashlib.sha256(
                f"{source_tag}|{raw['page']}|{raw['text'][:100]}".encode()
            ).hexdigest()[:32]

            kb_chunks.append(KBChunk(
                id=chunk_id,
                text=raw["text"],
                embedding=embedding,
                user_id=user_id,
                account_id="",          # bootstrap documents have no social account
                domains=domain,
                platform="pdf",
                source=source_tag,
                source_url=f"{source_tag}_page_{raw['page']}",
                author=author,
                published_at=f"{published_at}T00:00:00+00:00" if "T" not in published_at else published_at,
                language="en",
                layer=1,
                ingestion_type="bootstrap",
            ))

        n = store.upsert_chunks(KB_COLLECTION_CONTENT, kb_chunks)
        total_stored += n
        logger.info(
            "Batch %d-%d: stored %d chunks (total %d)",
            batch_start, batch_start + len(batch), n, total_stored,
        )

    logger.info(
        "Bootstrap complete: %d chunks stored (source_tag=%s, domain=%s, user=%s)",
        total_stored, source_tag, domain, user_id,
    )
    return total_stored


# ---------------------------------------------------------------------------
# Batch mode — load all bootstrap_sources from kb_domains.yaml
# ---------------------------------------------------------------------------

def load_all_from_config(
    user_id: str = "default",
    api_key: str | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Load all bootstrap_sources defined in kb_domains.yaml.

    Returns {source_tag: chunk_count} for each processed source.
    """
    from config.kb_config import get_bootstrap_sources
    from infrastructure.embedding.nano_gpt_embedding_adapter import NanoGptEmbeddingAdapter
    _key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
    _base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    _embedding_port = NanoGptEmbeddingAdapter(api_key=_key, base_url=_base)

    sources = get_bootstrap_sources()

    if not sources:
        logger.warning("No bootstrap_sources found in kb_domains.yaml")
        return {}

    results: dict[str, int] = {}
    for src in sources:
        if src.get("type") != "pdf":
            logger.info("Skipping non-PDF bootstrap source: %s (type=%s)", src.get("source_tag"), src.get("type"))
            continue

        source_tag = src.get("source_tag", "")
        domain = src.get("domain", "")
        author = src.get("author", "Unknown")
        published_at = src.get("published_at", "1970-01-01")
        path_str = src.get("path", "")

        if not source_tag or not domain or not path_str:
            logger.warning("Skipping incomplete bootstrap source entry: %s", src)
            continue

        # Resolve path relative to repo root
        pdf_path = path_str if Path(path_str).is_absolute() else str(_REPO_ROOT / path_str)

        try:
            n = load_document(
                pdf_path=pdf_path,
                source_tag=source_tag,
                domain=domain,
                author=author,
                published_at=published_at,
                user_id=user_id,
                api_key=api_key,
                dry_run=dry_run,
                embedding_port=_embedding_port,
            )
            results[source_tag] = n
        except FileNotFoundError as exc:
            logger.error("PDF not found for source_tag=%s: %s", source_tag, exc)
            results[source_tag] = 0
        except Exception as exc:
            logger.error("Failed to load source_tag=%s: %s", source_tag, exc)
            results[source_tag] = 0

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load static reference documents (PDF) into the KB vector store."
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--all",
        action="store_true",
        help="Load all bootstrap_sources defined in kb_domains.yaml",
    )
    mode_group.add_argument(
        "--pdf",
        metavar="PATH",
        help="Path to a single PDF file to load",
    )

    parser.add_argument("--source-tag", default=None, help="Source tag identifier (required with --pdf)")
    parser.add_argument("--domain", default=None, help="Domain ID for this document (required with --pdf)")
    parser.add_argument("--author", default="Unknown", help="Author or publisher name (default: Unknown)")
    parser.add_argument("--published-at", default="1970-01-01", help="Publication date ISO 8601 (default: 1970-01-01)")
    parser.add_argument("--user-id", default="default", help="User ID for KB isolation (default: default)")
    parser.add_argument("--api-key", default=None, help="Embedding API key (default: NANO_GPT_API_KEY env)")
    parser.add_argument("--dry-run", action="store_true", help="Count chunks without storing")

    args = parser.parse_args()

    try:
        if args.all:
            results = load_all_from_config(
                user_id=args.user_id,
                api_key=args.api_key,
                dry_run=args.dry_run,
            )
            for tag, count in results.items():
                verb = "would store" if args.dry_run else "stored"
                print(f"  {tag}: {count} chunks {verb}")
            total = sum(results.values())
            print(f"\nTotal: {total} chunks across {len(results)} source(s)")
        else:
            if not args.source_tag or not args.domain:
                parser.error("--source-tag and --domain are required when using --pdf")
            n = load_document(
                pdf_path=args.pdf,
                source_tag=args.source_tag,
                domain=args.domain,
                author=args.author,
                published_at=args.published_at,
                user_id=args.user_id,
                api_key=args.api_key,
                dry_run=args.dry_run,
            )
            verb = "would be stored" if args.dry_run else "stored"
            print(f"Done: {n} chunks {verb}")
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
