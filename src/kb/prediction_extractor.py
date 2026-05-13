"""
KB Prediction Extractor — Phase KB-15.

Post-ingestion batch job that extracts predictions and forecasts from newly
ingested content chunks.

Design:
  - Runs after a batch of items is ingested for one account (not per-chunk).
  - Combines up to 10 chunk texts into a single LLM call to keep token cost low.
  - Stores extracted predictions in kb_predictions (migration 022).
  - Model: deepseek-v3.2 via nano-gpt (same as summarisation).
  - Token cost: ~50K/day assuming ~10 accounts × 5 new items/day.

Design reference: KB_assistant_design_v2.md §12.1
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)


_EXTRACTION_MODEL = "deepseek-v3.2"
_MAX_CHUNKS_PER_BATCH = 10
_MAX_CHARS_PER_CHUNK = 400   # keep prompt tight


_PREDICTION_PROMPT = """\
You are extracting explicit predictions and forecasts from analyst or expert content.
A prediction is a specific claim about a future outcome — prices, events, policy decisions,
technology timelines, market movements, etc.

Rules:
- Only extract explicit predictions, not general opinions.
- Do NOT extract historical facts or current-state observations.
- predicted_date: ISO-8601 date string if a specific date/timeframe is mentioned, else null.
- confidence_stated: float 0.0–1.0 if the source explicitly states a confidence level,
  probability, or strong/weak qualifier; else null.
- Keep prediction_text concise (≤ 150 chars).

Return a JSON array (possibly empty []) — no other text:

[
  {{
    "prediction_text": "<concise prediction>",
    "predicted_outcome": "<what will happen>",
    "predicted_date": "YYYY-MM-DD or null",
    "confidence_stated": 0.8 or null,
    "source_chunk_id": "<chunk_id from input>"
  }},
  ...
]

Content chunks (chunk_id ||| text):
{chunks}
"""


@dataclass
class ExtractedPrediction:
    prediction_text: str
    predicted_outcome: Optional[str]
    predicted_date: Optional[str]
    confidence_stated: Optional[float]
    source_chunk_id: Optional[str]


def _call_llm(prompt: str, api_key: str) -> str:
    """Call nano-gpt chat completions; return raw text or empty string on error."""
    import httpx

    base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": _EXTRACTION_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1200,
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
        logger.warning("Prediction extraction LLM call failed: %s", exc)
        return ""


def _parse_predictions(raw: str, chunk_id_map: dict[str, str]) -> list[ExtractedPrediction]:
    """Parse the LLM JSON response into ExtractedPrediction objects."""
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()

    try:
        items = json.loads(text)
        if not isinstance(items, list):
            return []
    except json.JSONDecodeError:
        # Try to find a JSON array inside the response
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                items = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                logger.debug("Could not parse prediction JSON: %s", text[:200])
                return []
        else:
            return []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pred_text = (item.get("prediction_text") or "").strip()
        if not pred_text:
            continue

        # Resolve source_chunk_id from what the model returned; fall back to first chunk
        raw_cid = item.get("source_chunk_id") or ""
        resolved_cid = chunk_id_map.get(raw_cid, raw_cid or None)

        conf = item.get("confidence_stated")
        if conf is not None:
            try:
                conf = float(conf)
                conf = max(0.0, min(1.0, conf))
            except (TypeError, ValueError):
                conf = None

        results.append(ExtractedPrediction(
            prediction_text=pred_text[:200],
            predicted_outcome=(item.get("predicted_outcome") or "").strip() or None,
            predicted_date=item.get("predicted_date") or None,
            confidence_stated=conf,
            source_chunk_id=resolved_cid,
        ))
    return results


def _store_predictions(
    predictions: list[ExtractedPrediction],
    user_id: str,
    account_id: str,
    db_path: str,
) -> int:
    """Insert extracted predictions into kb_predictions; return count stored."""
    if not predictions:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    try:
        conn = sqlite3.connect(db_path)
        for pred in predictions:
            pred_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO kb_predictions
                    (id, user_id, source_account, source_chunk_id,
                     prediction_text, predicted_outcome, predicted_date,
                     confidence_stated, extracted_at, verification_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    pred_id,
                    user_id,
                    account_id,
                    pred.source_chunk_id,
                    pred.prediction_text,
                    pred.predicted_outcome,
                    pred.predicted_date,
                    pred.confidence_stated,
                    now,
                ),
            )
            inserted += 1
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("Failed to store predictions for account=%s: %s", account_id, exc)
    return inserted


class PredictionExtractor:
    """
    Extracts predictions from a batch of ingested content chunks.

    Usage::

        extractor = PredictionExtractor(api_key="...", db_path="...")
        n = extractor.extract_from_batch(chunks, user_id="...", account_id="...")
    """

    TRIGGER = "post_ingestion_batch"

    def __init__(
        self,
        api_key: Optional[str] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
        self._db_path = db_path or str(get_db_path())

    def extract_from_batch(
        self,
        chunks: list[dict],
        user_id: str,
        account_id: str,
    ) -> int:
        """
        Extract predictions from *chunks* (list of dicts with 'id' and 'text').

        Processes up to _MAX_CHUNKS_PER_BATCH chunks per LLM call; loops if more.
        Returns total number of predictions stored.
        """
        if not chunks or not self._api_key:
            return 0

        total_stored = 0
        # Process in batches of _MAX_CHUNKS_PER_BATCH
        for batch_start in range(0, len(chunks), _MAX_CHUNKS_PER_BATCH):
            batch = chunks[batch_start: batch_start + _MAX_CHUNKS_PER_BATCH]
            stored = self._process_batch(batch, user_id, account_id)
            total_stored += stored

        if total_stored:
            logger.info(
                "Extracted %d prediction(s) from account=%s user=%s",
                total_stored, account_id, user_id,
            )
        return total_stored

    def _process_batch(
        self,
        batch: list[dict],
        user_id: str,
        account_id: str,
    ) -> int:
        chunk_id_map: dict[str, str] = {}
        chunk_lines: list[str] = []
        for chunk in batch:
            cid = chunk.get("id", "")
            text = (chunk.get("text") or "")[:_MAX_CHARS_PER_CHUNK]
            chunk_id_map[cid] = cid
            chunk_lines.append(f"{cid} ||| {text}")

        prompt = _PREDICTION_PROMPT.format(chunks="\n".join(chunk_lines))
        raw = _call_llm(prompt, self._api_key)
        if not raw:
            return 0

        predictions = _parse_predictions(raw, chunk_id_map)
        if not predictions:
            return 0

        return _store_predictions(predictions, user_id, account_id, self._db_path)
