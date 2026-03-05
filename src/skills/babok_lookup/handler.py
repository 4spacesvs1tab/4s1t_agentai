#!/usr/bin/env python3
"""
babok_lookup skill handler.

Queries the ChromaDB 'babok_v3' collection for relevant BABOK v3 sections.
CHROMA_PATH env var points to the ChromaDB directory (default: ./data/chroma).

Input:  {"parameters": {"query": "...", "max_sections": 3}}
Output: {"success": true, "result": {"sections": [...], "source_references": [...], "section_count": N}}
"""
import json
import os
import sys

_DEFAULT_CHROMA_PATH = "./data/chroma"
_BABOK_COLLECTION = "babok_v3"


def execute(params: dict) -> dict:
    import chromadb

    query = params.get("query", "").strip()
    if not query:
        raise ValueError("'query' parameter is required.")
    max_sections = int(params.get("max_sections", 3))
    max_sections = max(1, min(10, max_sections))

    path = os.environ.get("CHROMA_PATH", _DEFAULT_CHROMA_PATH)
    client = chromadb.PersistentClient(path=path)

    try:
        col = client.get_collection(_BABOK_COLLECTION)
    except Exception:
        return {
            "sections": [],
            "source_references": [],
            "section_count": 0,
            "_note": (
                f"BABOK collection '{_BABOK_COLLECTION}' not found. "
                "Run src/skills/babok_lookup/ingest_babok.py first."
            ),
        }

    count = col.count()
    if count == 0:
        return {"sections": [], "source_references": [], "section_count": 0}

    results = col.query(
        query_texts=[query],
        n_results=min(max_sections, count),
        include=["documents", "distances", "metadatas"],
    )

    docs = results.get("documents", [[]])[0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    sections = []
    source_refs = set()
    for doc, dist, meta in zip(docs, distances, metadatas or [{}] * len(docs)):
        chapter = meta.get("chapter", "") if meta else ""
        section = meta.get("section", "") if meta else ""
        source = meta.get("source", "BABOK v3") if meta else "BABOK v3"
        sections.append({
            "text": doc,
            "chapter": chapter,
            "section": section,
            "relevance_score": round(1.0 - float(dist), 4),
        })
        ref = f"BABOK v3 — {chapter}" if chapter else "BABOK v3"
        if section:
            ref += f", {section}"
        source_refs.add(ref)

    return {
        "sections": sections,
        "source_references": sorted(source_refs),
        "section_count": len(sections),
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
