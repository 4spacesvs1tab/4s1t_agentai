#!/usr/bin/env python3
"""
BABOK v3 ingestion script — run once to index BABOK content into ChromaDB.

Usage:
    python src/skills/babok_lookup/ingest_babok.py /path/to/BABOK_v3.txt [--chroma-path ./data/chroma]
    python src/skills/babok_lookup/ingest_babok.py /path/to/BABOK_v3.pdf  [--chroma-path ./data/chroma]

The script chunks the document into ~500-word sections and indexes them into
the 'babok_v3' ChromaDB collection. Re-running with the same content is safe
(IDs are deterministic — existing documents will be overwritten).

Requirements: chromadb (installed in venv), optionally pdfplumber for PDF input.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path


_COLLECTION_NAME = "babok_v3"
_CHUNK_SIZE_WORDS = 500
_CHUNK_OVERLAP_WORDS = 50


def _chunk_text(text: str, chunk_size: int = _CHUNK_SIZE_WORDS, overlap: int = _CHUNK_OVERLAP_WORDS) -> list[str]:
    """Split text into overlapping word chunks."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk_words = words[i: i + chunk_size]
        chunks.append(" ".join(chunk_words))
        i += chunk_size - overlap
    return [c for c in chunks if c.strip()]


def _detect_chapter(chunk: str) -> str:
    """Heuristically detect a chapter heading in a text chunk."""
    match = re.search(r"(chapter\s+\d+|knowledge area\s+\d+)", chunk, re.IGNORECASE)
    return match.group(0).title() if match else ""


def _load_text(source_path: Path) -> str:
    """Load text from a .txt or .pdf file."""
    if source_path.suffix.lower() == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            raise ImportError(
                "pdfplumber is required for PDF ingestion: pip install pdfplumber"
            )
        pages = []
        with pdfplumber.open(source_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        return "\n".join(pages)
    else:
        return source_path.read_text(encoding="utf-8", errors="replace")


def ingest(source_path: Path, chroma_path: str) -> int:
    """Ingest the source document and return the number of chunks indexed."""
    import chromadb

    print(f"Loading: {source_path}")
    text = _load_text(source_path)
    chunks = _chunk_text(text)
    print(f"Chunks to index: {len(chunks)}")

    client = chromadb.PersistentClient(path=chroma_path)

    # Get or create the collection
    try:
        col = client.get_collection(_COLLECTION_NAME)
        print(f"Collection '{_COLLECTION_NAME}' already exists ({col.count()} docs). Updating.")
    except Exception:
        col = client.create_collection(
            name=_COLLECTION_NAME,
            metadata={"description": "IIBA BABOK v3 knowledge base", "source": str(source_path)},
        )
        print(f"Created collection '{_COLLECTION_NAME}'.")

    # Build deterministic IDs from content hash
    ids = [hashlib.sha256(chunk.encode()).hexdigest()[:16] for chunk in chunks]
    metadatas = [
        {
            "source": "BABOK v3",
            "chunk_index": i,
            "chapter": _detect_chapter(chunk),
            "section": "",
        }
        for i, chunk in enumerate(chunks)
    ]

    # Upsert in batches of 100
    batch_size = 100
    for start in range(0, len(chunks), batch_size):
        end = min(start + batch_size, len(chunks))
        col.upsert(
            ids=ids[start:end],
            documents=chunks[start:end],
            metadatas=metadatas[start:end],
        )
        print(f"  Indexed chunks {start}–{end}")

    final_count = col.count()
    print(f"Done. Collection '{_COLLECTION_NAME}' now has {final_count} documents.")
    return len(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index BABOK v3 content into ChromaDB.")
    parser.add_argument("source", help="Path to BABOK source file (.txt or .pdf)")
    parser.add_argument(
        "--chroma-path",
        default=os.environ.get("CHROMA_PATH", "./data/chroma"),
        help="ChromaDB persist directory (default: ./data/chroma)",
    )
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"Error: source file not found: {source}", file=sys.stderr)
        sys.exit(1)

    count = ingest(source, args.chroma_path)
    print(f"Ingestion complete: {count} chunks indexed.")


if __name__ == "__main__":
    main()
