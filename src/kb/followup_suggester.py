"""
Follow-Up Suggestions — Phase KB-18.

After the orchestrator produces an answer, call GLM 4.7 (~300 tokens) to
generate 3 follow-up questions.  At least one must be actionable (reminder,
task, or alert setup).  The suggestions are returned as a plain string list
and appended to the ChatResponse so the web UI can render them as clickable
chips.

Design reference: KB_assistant_design_v2.md §13, phase table row KB-18.
"""
from __future__ import annotations

import json
import os
from typing import List

from utils.logger import setup_logger
logger = setup_logger(__name__)

_MODEL = "zai-org/glm-4.7"
_MAX_TOKENS = 400
_ANSWER_PREVIEW_CHARS = 200
_TIMEOUT = 12.0  # seconds — hard cap so we never block a chat response

_PROMPT_TEMPLATE = """\
User asked: {question}
Agent answered: {answer_summary}
User's top interests: {interests}

Suggest 3 follow-up questions. Rules:
- Each question must be under 60 characters.
- At least one must be actionable: start it with [Set alert], [Add task], or [Remind me].
- Do NOT repeat the original question.
- Output a JSON array of strings only — no explanation, no markdown.

Example output: ["Question one?", "Question two?", "[Set alert] for topic X"]
"""


def _build_prompt(question: str, answer: str, interests: List[str]) -> str:
    answer_summary = answer[:_ANSWER_PREVIEW_CHARS].strip()
    if len(answer) > _ANSWER_PREVIEW_CHARS:
        answer_summary += "…"
    interests_str = ", ".join(interests[:5]) if interests else "general"
    return _PROMPT_TEMPLATE.format(
        question=question,
        answer_summary=answer_summary,
        interests=interests_str,
    )


def _call_llm(prompt: str, api_key: str) -> str:
    """Synchronous httpx call — run via asyncio.to_thread from async callers."""
    import httpx

    base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    resp = httpx.post(
        f"{base}/chat/completions",
        json={
            "model": _MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": _MAX_TOKENS,
            "temperature": 0.7,
        },
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _parse_suggestions(raw: str) -> List[str]:
    """Parse the LLM JSON array response into a list of strings."""
    text = raw.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(l for l in lines if not l.startswith("```")).strip()
    try:
        items = json.loads(text)
        if isinstance(items, list):
            return [str(s).strip() for s in items if s][:3]
    except Exception:
        pass
    # Fallback: extract quoted strings
    import re
    matches = re.findall(r'"([^"]{3,80})"', text)
    return [m.strip() for m in matches[:3]]


def generate_followups_sync(
    question: str,
    answer: str,
    interests: List[str],
    api_key: str,
) -> List[str]:
    """
    Generate 3 follow-up questions synchronously.

    Returns an empty list on any error so callers are never blocked.
    Intended to be called via ``asyncio.to_thread``.
    """
    try:
        prompt = _build_prompt(question, answer, interests)
        raw = _call_llm(prompt, api_key)
        suggestions = _parse_suggestions(raw)
        return suggestions if suggestions else []
    except Exception as exc:
        logger.debug("Follow-up generation failed (non-critical): %s", exc)
        return []


def load_user_interests(user_id: str, db_path: str) -> List[str]:
    """
    Load the top 5 active interest topics for a user from kb_user_interests.

    Returns empty list on any error.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT topic FROM kb_user_interests "
            "WHERE user_id = ? "
            "ORDER BY last_mentioned DESC LIMIT 5",
            (user_id,),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []
