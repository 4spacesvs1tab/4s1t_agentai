"""
KB Narrative Evolution Service — Phase KB-20.

Tracks how a topic or source's narrative position has evolved over time.

Pipeline:
  1. Query ChromaDB for up to 50 chunks matching the topic (+ optional account
     filter), covering the requested timeframe.
  2. Sort chunks by published_at (ascending).
  3. Cluster into monthly periods.
  4. For each period with content: call GLM 4.7 with up to 5 representative
     chunks → extract the dominant narrative stance for that month.
  5. Return a structured timeline dict.

Token cost: ~200K per call (on-demand only; not scheduled).
Model: GLM 4.7 via nano-gpt.

Design reference: KB_assistant_design_v2.md §12.4
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from kb.exceptions import EmbeddingError
from kb.ports.embedding_port import EmbeddingPort
from utils.logger import setup_logger
logger = setup_logger(__name__)

_EMBEDDING_MODEL = "BAAI/bge-m3"
_SUMMARY_MODEL = "glm-4-7"
_MAX_FETCH = 50
_MAX_CHUNKS_PER_PERIOD = 5
_MAX_CHARS_PER_CHUNK = 400
_DEFAULT_CHROMA_PATH = "./data/chroma"

_NARRATIVE_SUMMARY_PROMPT = """\
You are a media analyst. Below are {count} content excerpts from {period} about "{topic}".
Identify the **dominant narrative stance** in this period: the main claim, framing, or
position taken. Be concise (2–3 sentences). Focus on what changed vs. what was constant.

Excerpts:
{chunks}

Output format (JSON, no markdown):
{{
  "stance": "<dominant stance in 2-3 sentences>",
  "key_claims": ["<claim 1>", "<claim 2>"],
  "tone": "bullish|bearish|cautious|neutral|mixed"
}}
"""

_TIMEFRAME_TO_DAYS: dict[str, int] = {
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
}


def _call_llm(prompt: str, api_key: str) -> str:
    """Call GLM 4.7 via nano-gpt. Returns raw text or empty string on error."""
    import httpx
    base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": _SUMMARY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 600,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("LLM call failed for narrative_evolution: %s", exc)
        return ""


def _parse_period_summary(raw: str) -> dict:
    """Parse the LLM JSON response for one period. Returns empty dict on failure."""
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.startswith("```")
        ).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {"stance": raw[:300] if raw else "", "key_claims": [], "tone": "unknown"}


def _period_label(dt: datetime) -> str:
    """Return 'YYYY-MM' period label from a datetime."""
    return dt.strftime("%Y-%m")


def _cluster_by_month(
    chunks: list[dict],
) -> dict[str, list[dict]]:
    """
    Group chunks by year-month, ordered ascending.

    Each chunk dict must have a 'published_at' field (ISO 8601 string).
    Chunks without a parseable date are placed in an 'unknown' bucket.
    """
    periods: dict[str, list[dict]] = {}
    for chunk in chunks:
        pub = chunk.get("published_at", "") or ""
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            key = _period_label(dt)
        except Exception:
            key = "unknown"
        periods.setdefault(key, []).append(chunk)

    # Return sorted by key (ISO month strings sort lexicographically)
    return dict(sorted(periods.items()))


class NarrativeEvolutionService:
    """
    On-demand service for KB-20 narrative evolution tracking.

    Usage::

        svc = NarrativeEvolutionService(api_key="...", chroma_path="...", embedding_port=adapter)
        result = svc.run(topic="Fed rate cuts", timeframe="6m", user_id="<uuid>")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        chroma_path: Optional[str] = None,
        *,
        embedding_port: EmbeddingPort,
    ) -> None:
        self._api_key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
        self._chroma_path = chroma_path or os.environ.get(
            "CHROMA_PATH", _DEFAULT_CHROMA_PATH
        )
        self._embedding_port: EmbeddingPort = embedding_port

    def run(
        self,
        topic: str,
        timeframe: str = "6m",
        account: Optional[str] = None,
        user_id: str = "default",
    ) -> dict:
        """
        Return a narrative evolution timeline for *topic*.

        Parameters
        ----------
        topic :
            Free-text query (e.g. "Fed interest rate policy").
        timeframe :
            One of '1m', '3m', '6m', '1y'. Controls the lookback window.
        account :
            Optional account_id to restrict the search to one source.
        user_id :
            User isolation key (required for multi-user deployments).

        Returns
        -------
        dict with keys:
            topic, timeframe, account, user_id,
            periods_found (int), periods_analysed (int),
            timeline (list of period dicts),
            error (str|None)
        """
        if not topic or not topic.strip():
            return self._error_result(topic, timeframe, account, user_id, "'topic' is required")

        days = _TIMEFRAME_TO_DAYS.get(timeframe.lower(), 180)
        since_dt = datetime.now(timezone.utc) - timedelta(days=days)
        since_iso = since_dt.isoformat()

        # 1. Embed the query
        try:
            embedding = self._embedding_port.embed([topic])[0]
        except EmbeddingError as exc:
            return self._error_result(topic, timeframe, account, user_id, f"Embedding failed: {exc}")

        # 2. Build ChromaDB where filter
        where = self._build_where(user_id, account)

        # 3. Fetch chunks from ChromaDB
        raw_chunks = self._query_chroma(embedding, where, n=_MAX_FETCH)
        if not raw_chunks:
            return self._error_result(
                topic, timeframe, account, user_id,
                "No content found for this topic in the knowledge base"
            )

        # 4. Apply time filter in Python (ChromaDB 0.6.x doesn't filter on string dates)
        chunks = [
            c for c in raw_chunks
            if self._after_since(c.get("published_at", ""), since_iso)
        ]
        if not chunks:
            return self._error_result(
                topic, timeframe, account, user_id,
                f"No content found in the last {days} days for this topic"
            )

        # 5. Cluster by month
        periods = _cluster_by_month(chunks)
        periods_found = len(periods)

        # 6. Summarise each period with the LLM
        timeline: list[dict] = []
        for period_key, period_chunks in periods.items():
            if period_key == "unknown":
                continue
            sample = period_chunks[:_MAX_CHUNKS_PER_PERIOD]
            chunk_text = "\n\n".join(
                f"[{c.get('author', 'unknown')}] {c.get('text', '')[:_MAX_CHARS_PER_CHUNK]}"
                for c in sample
            )
            prompt = _NARRATIVE_SUMMARY_PROMPT.format(
                count=len(sample),
                period=period_key,
                topic=topic,
                chunks=chunk_text,
            )
            raw = _call_llm(prompt, self._api_key)
            summary = _parse_period_summary(raw) if raw else {}
            timeline.append({
                "period": period_key,
                "chunk_count": len(period_chunks),
                "analysed_count": len(sample),
                **summary,
            })

        return {
            "topic": topic,
            "timeframe": timeframe,
            "account": account,
            "user_id": user_id,
            "periods_found": periods_found,
            "periods_analysed": len(timeline),
            "timeline": timeline,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_where(user_id: str, account: Optional[str]) -> dict:
        conditions: list[dict] = [{"user_id": {"$eq": user_id}}]
        if account:
            conditions.append({"account_id": {"$eq": account}})
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _query_chroma(self, embedding: list[float], where: dict, n: int) -> list[dict]:
        """Query ChromaDB; return list of chunk dicts (metadata + text)."""
        try:
            import chromadb
            client = chromadb.PersistentClient(path=self._chroma_path)
            col = client.get_or_create_collection("kb_content")
            results = col.query(
                query_embeddings=[embedding],
                n_results=n,
                where=where,
                include=["documents", "metadatas"],
            )
            docs = (results.get("documents") or [[]])[0]
            metas = (results.get("metadatas") or [[]])[0]
            chunks = []
            for text, meta in zip(docs, metas):
                entry = dict(meta or {})
                entry["text"] = text or ""
                chunks.append(entry)
            return chunks
        except Exception as exc:
            logger.warning("ChromaDB query failed in narrative_evolution: %s", exc)
            return []

    @staticmethod
    def _after_since(published_at: str, since_iso: str) -> bool:
        """Return True if published_at >= since_iso (both ISO 8601)."""
        if not published_at:
            return False
        try:
            pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            return pub >= since
        except Exception:
            return False

    @staticmethod
    def _error_result(
        topic: str, timeframe: str, account: Optional[str],
        user_id: str, error: str,
    ) -> dict:
        return {
            "topic": topic,
            "timeframe": timeframe,
            "account": account,
            "user_id": user_id,
            "periods_found": 0,
            "periods_analysed": 0,
            "timeline": [],
            "error": error,
        }
