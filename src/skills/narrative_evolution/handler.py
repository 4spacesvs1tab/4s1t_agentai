#!/usr/bin/env python3
"""
narrative_evolution skill handler — v1.0.0

Tracks how a topic or source's narrative has shifted over time by clustering
KB content into monthly periods and summarising each period's dominant stance.

Input:  {"parameters": {"topic": "...", "timeframe": "6m", "account": null, "user_id": "..."}}
Output: {"success": true, "result": {"timeline": [...], "periods_found": N, ...}}

Design reference: KB_assistant_design_v2.md §12.4
"""
import json
import os
import sys
from pathlib import Path


def _resolve_chroma_path() -> str:
    chroma_path = os.environ.get("CHROMA_PATH", "")
    if chroma_path:
        return chroma_path
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("sqlite:///"):
        db_file = Path(db_url[len("sqlite:///"):])
        return str(db_file.parent / "chroma")
    return str(
        Path(__file__).resolve().parent.parent.parent.parent / "data" / "chroma"
    )


def execute(params: dict) -> dict:
    topic = (params.get("topic") or "").strip()
    if not topic:
        raise ValueError("'topic' parameter is required and must not be empty")

    timeframe = (params.get("timeframe") or "6m").strip()
    valid_timeframes = {"1m", "3m", "6m", "1y"}
    if timeframe not in valid_timeframes:
        timeframe = "6m"

    account = params.get("account") or None
    user_id = (params.get("user_id") or "default").strip()

    chroma_path = _resolve_chroma_path()
    api_key = os.environ.get("NANO_GPT_API_KEY", "")

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from kb.narrative_evolution import NarrativeEvolutionService
    from infrastructure.embedding.nano_gpt_embedding_adapter import NanoGptEmbeddingAdapter

    _base_url = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    _embedding_port = NanoGptEmbeddingAdapter(api_key=api_key, base_url=_base_url)
    svc = NarrativeEvolutionService(api_key=api_key, chroma_path=chroma_path, embedding_port=_embedding_port)
    return svc.run(topic=topic, timeframe=timeframe, account=account, user_id=user_id)


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
