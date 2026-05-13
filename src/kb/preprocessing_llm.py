"""
LLM helper calls used during the KB ingestion pipeline.

These functions call chat/completions endpoints (summarisation and contradiction
detection). They are extracted here to keep preprocessor.py free of httpx
imports, satisfying the E1 DDD boundary for the embedding path.

Sprint E2/E3 follow-up: define an LLMPort (ABC) in kb/ports/, move the
httpx calls to infrastructure/llm/, and inject via KBPreprocessor.__init__.
"""
from __future__ import annotations

import os
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger(__name__)

_SUMMARY_MODEL = "deepseek-v3.2"
_CONTRADICTION_MODEL = "deepseek-v3.2"


def _summarise_text(text: str, api_key: str) -> Optional[str]:
    """
    Generate a 3-sentence summary for long-form content using DeepSeek V3.
    Returns None on failure (non-blocking).
    """
    import httpx

    nano_gpt_base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    prompt = (
        "Summarise the following content in exactly 3 sentences. "
        "Be concise, factual, and preserve the author's key claims:\n\n"
        + text[:8000]  # limit input to avoid token overflow
    )
    try:
        resp = httpx.post(
            f"{nano_gpt_base}/chat/completions",
            json={
                "model": _SUMMARY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("Summarisation failed: %s", exc)
        return None


def _llm_contradicts(text_a: str, text_b: str, api_key: str) -> bool:
    """
    Ask DeepSeek V3 whether two text excerpts contradict each other.

    Returns True if the model answers YES (contradiction detected).
    Returns False on any error or unclear answer (non-blocking).
    """
    import httpx

    nano_gpt_base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    prompt = (
        "Do the following two statements contradict each other? "
        "Answer only YES or NO — no explanation.\n\n"
        f"Statement A:\n{text_a[:600]}\n\n"
        f"Statement B:\n{text_b[:600]}"
    )
    try:
        resp = httpx.post(
            f"{nano_gpt_base}/chat/completions",
            json={
                "model": _CONTRADICTION_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 5,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"].strip().upper()
        return answer.startswith("YES")
    except Exception as exc:
        logger.debug("Contradiction LLM check failed: %s", exc)
        return False
