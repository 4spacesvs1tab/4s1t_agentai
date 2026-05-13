#!/usr/bin/env python3
"""
web_search skill handler.

Uses the duckduckgo-search library (no API key required).

Input:  {"parameters": {"query": "...", "limit": 5, "region": "wt-wt"}}
Output: {"success": true, "result": {"results": [...], "result_count": N}}
"""
import json
import sys


def execute(params: dict) -> dict:
    from duckduckgo_search import DDGS

    query = params.get("query", "").strip()
    if not query:
        raise ValueError("'query' parameter is required and must not be empty.")

    limit = int(params.get("limit", 5))
    limit = max(1, min(10, limit))  # clamp to [1, 10]
    region = params.get("region", "wt-wt")

    raw = DDGS().text(
        keywords=query,
        region=region,
        max_results=limit,
    )

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "snippet": r.get("body", ""),
        }
        for r in (raw or [])
    ]

    return {
        "results": results,
        "result_count": len(results),
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
