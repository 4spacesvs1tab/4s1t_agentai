"""
KB Prediction Verifier — Phase KB-15.

Weekly verification scheduler for predictions in kb_predictions whose
predicted_date has passed.

Verification strategy (per prediction):
  1. Search KB for evidence (knowledge_base_search on prediction_text).
  2. If best cosine score < 0.6, fall back to a Tor web search for the
     predicted outcome.
  3. Ask DeepSeek V3 whether the evidence confirms, refutes, or is
     inconclusive on the prediction.
  4. Update verification_status + verification_evidence in kb_predictions.

Statuses:
  pending     — not yet due (predicted_date in the future or NULL)
  verified    — prediction confirmed by evidence
  failed      — prediction refuted by evidence
  inconclusive — insufficient evidence found
  expired     — predicted_date passed > 90 days ago and no evidence was ever found

Design reference: KB_assistant_design_v2.md §12.1
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)


_VERIFICATION_MODEL = "deepseek-v3.2"
_KB_CONFIDENCE_THRESHOLD = 0.6   # below this → try Tor web fallback
_EXPIRY_DAYS = 90                 # mark expired after this many days past predicted_date
_MAX_VERIFY_PER_RUN = 20          # cap LLM calls per weekly run


_VERDICT_PROMPT = """\
A source made the following prediction:

PREDICTION: {prediction}

Available evidence:
{evidence}

Based solely on the evidence above, is the prediction:
  A) VERIFIED  — evidence clearly confirms the outcome occurred
  B) FAILED    — evidence clearly shows the outcome did NOT occur
  C) INCONCLUSIVE — evidence is insufficient or ambiguous

Reply with exactly one word: VERIFIED, FAILED, or INCONCLUSIVE.
"""


def _call_llm(prompt: str, api_key: str) -> str:
    import httpx
    base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": _VERIFICATION_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 10,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip().upper()
    except Exception as exc:
        logger.debug("Verdict LLM call failed: %s", exc)
        return ""


def _kb_search(
    query: str,
    user_id: str,
    n_results: int = 5,
) -> list[dict]:
    """
    Search KB for evidence using the vector store directly (no skill overhead).
    Returns list of dicts with 'text' and 'score'.
    """
    try:
        from kb.vector_store import get_kb_vector_store, KB_COLLECTION_CONTENT
        from kb.preprocessor import _embed_single

        api_key = os.environ.get("NANO_GPT_API_KEY", "")
        if not api_key:
            return []

        embedding = _embed_single(query, api_key)
        store = get_kb_vector_store()
        results = store.query(
            collection_name=KB_COLLECTION_CONTENT,
            query_embedding=embedding,
            n_results=n_results,
            where={"user_id": {"$eq": user_id}},
        )
        return results
    except Exception as exc:
        logger.debug("KB search for verification failed: %s", exc)
        return []


def _tor_web_search(query: str, max_chars: int = 1500) -> str:
    """
    Fetch a DuckDuckGo Lite search results page for *query* through Tor.

    Returns plaintext snippet (best-effort) or empty string on failure.
    This is a lightweight fallback — not a full scraping pipeline.
    """
    import httpx
    import re

    tor_proxy = os.environ.get("TOR_SOCKS_PROXY", "")
    ddg_url = "https://lite.duckduckgo.com/lite/"

    client_kwargs: dict = {
        "timeout": 20.0,
        "follow_redirects": True,
    }
    if tor_proxy:
        try:
            import httpx
            client_kwargs["proxies"] = {"all://": f"socks5://{tor_proxy}"}
        except Exception:
            pass

    try:
        resp = httpx.post(
            ddg_url,
            data={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
                "Accept": "text/html",
            },
            **client_kwargs,
        )
        resp.raise_for_status()
        # Strip HTML tags and normalise whitespace
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as exc:
        logger.debug("Tor web search failed for query=%r: %s", query[:60], exc)
        return ""


def _get_due_predictions(db_path: str, user_id: str) -> list[dict]:
    """
    Return pending predictions whose predicted_date has passed (or is NULL
    and was extracted > 30 days ago), up to _MAX_VERIFY_PER_RUN rows.
    """
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    stale_cutoff = (now - timedelta(days=30)).isoformat()

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, user_id, source_account, source_chunk_id,
                   prediction_text, predicted_outcome, predicted_date,
                   confidence_stated, extracted_at
            FROM   kb_predictions
            WHERE  user_id = ?
              AND  verification_status = 'pending'
              AND  (
                     (predicted_date IS NOT NULL AND predicted_date <= ?)
                     OR
                     (predicted_date IS NULL AND extracted_at <= ?)
                   )
            ORDER  BY predicted_date ASC NULLS LAST
            LIMIT  ?
            """,
            (user_id, today, stale_cutoff, _MAX_VERIFY_PER_RUN),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("_get_due_predictions failed for user=%s: %s", user_id, exc)
        return []


def _mark_expired(db_path: str, user_id: str) -> int:
    """
    Mark predictions as 'expired' when predicted_date is more than _EXPIRY_DAYS ago
    and they are still pending.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_EXPIRY_DAYS)).date().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            """
            UPDATE kb_predictions
            SET    verification_status = 'expired',
                   verified_at = ?,
                   verification_evidence = 'Prediction date passed without resolution.'
            WHERE  user_id = ?
              AND  verification_status = 'pending'
              AND  predicted_date IS NOT NULL
              AND  predicted_date < ?
            """,
            (now, user_id, cutoff),
        )
        conn.commit()
        count = cur.rowcount
        conn.close()
        return count
    except Exception as exc:
        logger.debug("_mark_expired failed for user=%s: %s", user_id, exc)
        return 0


def _update_prediction(
    db_path: str,
    prediction_id: str,
    status: str,
    evidence: str,
    url: str = "",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            UPDATE kb_predictions
            SET    verification_status    = ?,
                   verified_at            = ?,
                   verification_evidence  = ?,
                   verification_url       = ?
            WHERE  id = ?
            """,
            (status, now, evidence[:2000], url or None, prediction_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to update prediction %s: %s", prediction_id, exc)


class PredictionVerifier:
    """
    Runs the weekly verification pass for all due predictions.

    Usage::

        verifier = PredictionVerifier(api_key="...", db_path="...")
        counts = verifier.verify_pending(user_id="...")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
        self._db_path = db_path or str(get_db_path())

    def verify_pending(self, user_id: str) -> dict:
        """
        Verify all due predictions for *user_id*.

        Returns a summary dict:
          {verified, failed, inconclusive, expired, total_checked}
        """
        counts = {"verified": 0, "failed": 0, "inconclusive": 0, "expired": 0, "total_checked": 0}

        # First: mark old predictions as expired
        counts["expired"] = _mark_expired(self._db_path, user_id)

        due = _get_due_predictions(self._db_path, user_id)
        if not due:
            return counts

        for pred in due:
            counts["total_checked"] += 1
            status, evidence, url = self._verify_one(pred)
            _update_prediction(self._db_path, pred["id"], status, evidence, url)
            counts[status] = counts.get(status, 0) + 1

        logger.info(
            "Prediction verification for user=%s: %s", user_id, counts
        )
        return counts

    def _verify_one(self, pred: dict) -> tuple[str, str, str]:
        """
        Return (status, evidence_text, url).

        1. Search KB for evidence.
        2. If best score < threshold, fall back to Tor web.
        3. Ask LLM for verdict.
        """
        query = pred["prediction_text"]
        if pred.get("predicted_outcome"):
            query = f"{query} {pred['predicted_outcome']}"

        # Step 1: KB search
        kb_results = _kb_search(query, user_id=pred["user_id"])
        best_score = max((r["score"] for r in kb_results), default=0.0)
        evidence_text = ""
        source_url = ""

        if best_score >= _KB_CONFIDENCE_THRESHOLD and kb_results:
            top = kb_results[0]
            evidence_text = top.get("text", "")[:600]
            source_url = top.get("source_url", "")
        else:
            # Step 2: Tor web fallback
            logger.debug(
                "KB score %.2f < %.2f — using Tor web fallback for prediction=%s",
                best_score, _KB_CONFIDENCE_THRESHOLD, pred["id"],
            )
            evidence_text = _tor_web_search(query)
            if not evidence_text:
                return "inconclusive", "No evidence found in KB or web.", ""

        # Step 3: LLM verdict
        if not self._api_key:
            return "inconclusive", "API key unavailable for verdict.", ""

        prompt = _VERDICT_PROMPT.format(
            prediction=pred["prediction_text"],
            evidence=evidence_text,
        )
        verdict = _call_llm(prompt, self._api_key)

        if verdict.startswith("VERIFIED"):
            return "verified", evidence_text, source_url
        elif verdict.startswith("FAILED"):
            return "failed", evidence_text, source_url
        else:
            return "inconclusive", evidence_text, source_url
