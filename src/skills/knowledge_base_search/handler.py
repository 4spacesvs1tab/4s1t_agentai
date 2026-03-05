#!/usr/bin/env python3
"""
knowledge_base_search skill handler.

Queries one or all ChromaDB collections for semantically relevant chunks.
ChromaDB path is read from CHROMA_PATH env var (default: ./data/chroma).

Input:  {"parameters": {"query": "...", "collection": "", "limit": 5}}
Output: {"success": true, "result": {"chunks": [...], "chunk_count": N, "collections_searched": [...]}}
"""
import json
import os
import sys


_DEFAULT_CHROMA_PATH = "./data/chroma"


def _get_client():
    import chromadb
    path = os.environ.get("CHROMA_PATH", _DEFAULT_CHROMA_PATH)
    return chromadb.PersistentClient(path=path)


def _list_collections(client) -> list[str]:
    """Return all collection names, falling back gracefully if none exist."""
    try:
        return [c.name for c in client.list_collections()]
    except Exception:
        return []


def execute(params: dict) -> dict:
    query = params.get("query", "").strip()
    if not query:
        raise ValueError("'query' parameter is required.")

    collection_name = params.get("collection", "").strip()
    limit = int(params.get("limit", 5))
    limit = max(1, min(20, limit))

    client = _get_client()
    all_collections = _list_collections(client)

    if not all_collections:
        return {
            "chunks": [],
            "chunk_count": 0,
            "collections_searched": [],
        }

    if collection_name:
        if collection_name not in all_collections:
            raise ValueError(
                f"Collection '{collection_name}' not found. "
                f"Available: {all_collections}"
            )
        target_collections = [collection_name]
    else:
        target_collections = all_collections

    all_chunks = []
    for col_name in target_collections:
        try:
            col = client.get_collection(col_name)
            count = col.count()
            if count == 0:
                continue
            results = col.query(
                query_texts=[query],
                n_results=min(limit, count),
                include=["documents", "distances", "metadatas"],
            )
            docs = results.get("documents", [[]])[0]
            distances = results.get("distances", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            for doc, dist, meta in zip(docs, distances, metadatas or [{}] * len(docs)):
                source = meta.get("source", meta.get("filename", col_name)) if meta else col_name
                all_chunks.append({
                    "text": doc,
                    "collection": col_name,
                    "source": source,
                    "distance": round(float(dist), 4),
                })
        except Exception as exc:
            # Log per-collection errors but continue searching others
            sys.stderr.write(f"Warning: failed to query collection '{col_name}': {exc}\n")

    # Sort by distance (most relevant first), take top limit
    all_chunks.sort(key=lambda c: c["distance"])
    all_chunks = all_chunks[:limit]

    return {
        "chunks": all_chunks,
        "chunk_count": len(all_chunks),
        "collections_searched": target_collections,
    }


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: handler.py input.json output.json", file=sys.stderr)
        sys.exit(1)
    input_path, output_path = sys.argv[1], sys.argv[2]
    try:
        data = json.loads(open(input_path).read())
        params = data.get("parameters", {})
        result = execute(params)
        output = {"success": True, "result": result, "error": None, "logs": []}
    except Exception as exc:
        output = {"success": False, "result": None, "error": str(exc), "logs": []}
    with open(output_path, "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()
