#!/usr/bin/env python3
"""
generate_wiki_page skill handler — v1.0.0

Generates (or refreshes) a structured Markdown wiki page about a topic,
synthesised from KB content.  The page is persisted in kb_wiki_pages and
returned immediately.  Subsequent calls with the same topic return the cached
version unless force_refresh=true.

Input:  {"parameters": {"topic": "...", "force_refresh": false, "user_id": "..."}}
Output: {"success": true, "result": {"page_id": "...", "title": "...", "content": "...", ...}}

Design reference: KB_assistant_design_v2.md §17 KB-23
"""
import json
import os
import sys
from pathlib import Path

from core.db_path import get_db_path


def _resolve_paths() -> tuple[str, str]:
    """Return (chroma_path, db_path) from environment / filesystem convention."""
    chroma_path = os.environ.get("CHROMA_PATH", "")
    db_path = str(get_db_path())
    if not chroma_path:
        chroma_path = str(Path(db_path).parent / "chroma")
    return chroma_path, db_path


def execute(params: dict) -> dict:
    topic = (params.get("topic") or "").strip()
    if not topic:
        raise ValueError("'topic' parameter is required and must not be empty")

    force_refresh = bool(params.get("force_refresh", False))
    user_id = (params.get("user_id") or "default").strip()
    api_key = os.environ.get("NANO_GPT_API_KEY", "")

    chroma_path, db_path = _resolve_paths()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from kb.wiki_service import WikiPageService

    svc = WikiPageService(api_key=api_key, chroma_path=chroma_path, db_path=db_path)
    return svc.generate(user_id=user_id, topic=topic, force_refresh=force_refresh)


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
