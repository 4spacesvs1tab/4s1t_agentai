#!/usr/bin/env python3
"""
knowledge_graph_query skill handler — v1.0.0

Traverses the KB entity graph to surface entity relationships.

Input:  {"parameters": {"entity": "...", "relation_type": null, "max_hops": 2}}
Output: {"success": true, "result": {"root_entity": {...}, "entities": [...], ...}}

Design reference: KB_assistant_design_v2.md §14
"""
import json
import os
import sys
from pathlib import Path

from core.db_path import get_db_path


def _resolve_db_path() -> str:
    return str(get_db_path())


def execute(params: dict) -> dict:
    entity = (params.get("entity") or "").strip()
    if not entity:
        raise ValueError("'entity' parameter is required and must not be empty")

    relation_type = params.get("relation_type") or None
    max_hops = max(1, min(3, int(params.get("max_hops", 2))))

    db_path = _resolve_db_path()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from kb.knowledge_graph import get_knowledge_graph_service

    svc = get_knowledge_graph_service(db_path)
    return svc.query_graph(entity, relation_type=relation_type, max_hops=max_hops)


if __name__ == "__main__":
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"success": False, "error": f"Invalid JSON: {exc}"}))
        sys.exit(1)

    try:
        result = execute(payload.get("parameters", {}))
        print(json.dumps({"success": True, "result": result}))
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        sys.exit(1)
