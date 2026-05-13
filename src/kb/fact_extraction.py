"""
KB-12: Fact Extraction + Interest Scoring.

FactExtractionJob runs asynchronously (never on the query path), triggered
either after every TRIGGER_EVERY_N_TURNS conversation turns or when a session
ends.  It:

  1. Checks that the user consented to memory for this session.
  2. Calls a cheap LLM (glm-4-flash via NanoGPT) to extract facts and topics
     from a batch of scrubbed conversation turns.
  3. Stores high-confidence facts in kb_user_facts.
  4. Adds topic mentions to kb_user_interests (for decay scoring).

Auto-summary (generate_session_summary) produces a one-sentence conversation
summary for conversations.summary — triggered asynchronously from the sync
endpoint.

PII ordering guarantee: fact extraction must ONLY run on PII-scrubbed text.
Tier-1 PII (PESEL, IBAN, CC, NIP, dowód) is never stored regardless of consent.

Design reference: KB_assistant_design_v2.md §4, §9.3
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRIGGER_EVERY_N_TURNS = 5

_EXTRACT_MODEL = "glm-4-flash"      # cheap; ~800 tokens per batch
_SUMMARY_MODEL = "glm-4-flash"
_MIN_CONFIDENCE = 0.8               # discard low-confidence facts
_MAX_FACTS_PER_BATCH = 10





def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """\
You are a personal fact extractor.  Given a conversation between a user and an AI assistant,
extract durable facts about the USER (not the assistant).

Focus on:
- Professional context: job role, industry, projects, tools, skills, goals
- Preferences: language, communication style, recurring interests
- Relationships: colleagues, clients, stakeholders (by role only — never store full names as PII)
- Context: organisation type, team size, geography (city/country level only)

STRICT rules:
- NEVER extract: passwords, tokens, PESEL, IBAN, credit card numbers, full names with IDs
- Minimum confidence 0.8 — only include facts the conversation clearly establishes
- prefer_confirmation=true for inferred or ambiguous facts; false only for explicit statements

Return ONLY valid JSON in this exact shape (no markdown fences):
{
  "facts": [
    {
      "fact_type": "preference|professional|contextual|relationship",
      "fact_key":  "short snake_case key",
      "fact_value": "concise value",
      "confidence": 0.0-1.0,
      "requires_confirmation": true|false,
      "source": "explicit|confirmed|inferred"
    }
  ],
  "topics": ["topic1", "topic2"]
}

Return {"facts": [], "topics": []} if nothing durable is found.\
"""

_SUMMARY_SYSTEM = """\
You write one-sentence conversation summaries.
Given a conversation transcript, output a single sentence (max 120 chars) that captures the
main topic and outcome.  Start with a verb.  No markdown.  No quotes around the output.\
"""


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------

def _nano_gpt_call(model: str, system: str, user: str, max_tokens: int = 600) -> Optional[str]:
    """Call NanoGPT synchronously.  Returns content string or None on failure."""
    key = os.environ.get("NANO_GPT_API_KEY", "")
    if not key:
        logger.debug("No NANO_GPT_API_KEY — skipping LLM call")
        return None

    import httpx

    base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.0,
            },
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.debug("NanoGPT call failed (%s): %s", model, exc)
        return None


# ---------------------------------------------------------------------------
# Interest scoring (read-side; stored as timestamps)
# ---------------------------------------------------------------------------

def compute_interest_score(timestamps: list[str]) -> float:
    """
    Exponential decay score.  Each mention contributes 1.0 decayed by age.
    Half-life = 14 days.
    """
    import math
    now = datetime.now(timezone.utc)
    half_life = 14.0
    score = 0.0
    for ts in timestamps:
        try:
            age_days = (now - datetime.fromisoformat(ts)).total_seconds() / 86400
            score += math.pow(0.5, age_days / half_life)
        except Exception:
            pass
    return round(score, 4)


# ---------------------------------------------------------------------------
# FactExtractionJob
# ---------------------------------------------------------------------------

class FactExtractionJob:
    """
    Async (but synchronous-internally) job that extracts facts from conversation turns.

    Call process_session_batch() from an asyncio.to_thread() wrapper so it does
    not block the event loop.
    """

    def check_consent(self, user_id: str, session_id: str) -> tuple[bool, str]:
        """
        Return (consented, scope) for this user/session.

        Checks kb_session_memory_consent first (session opt-in), then falls
        back to the user's memory_scope column default.
        Returns (False, 'off') if no memory consent is active.
        """
        with _conn() as conn:
            # Session-level consent (most specific)
            row = conn.execute(
                """
                SELECT scope FROM kb_session_memory_consent
                WHERE session_id = ? AND user_id = ?
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
                """,
                (session_id, user_id),
            ).fetchone()
            if row:
                return True, row["scope"]

            # Fall back to user default
            user_row = conn.execute(
                "SELECT memory_scope FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if user_row:
                scope = user_row["memory_scope"] or "off"
                return scope != "off", scope

        return False, "off"

    def enable_session_memory(self, user_id: str, session_id: str, scope: str = "private") -> None:
        """Insert or update consent for a session."""
        now = _utcnow()
        with _conn() as conn:
            conn.execute(
                """
                INSERT INTO kb_session_memory_consent (session_id, user_id, scope, consented_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET scope = excluded.scope, consented_at = excluded.consented_at
                """,
                (session_id, user_id, scope, now),
            )

    def disable_session_memory(self, user_id: str, session_id: str) -> None:
        """Remove session consent."""
        with _conn() as conn:
            conn.execute(
                "DELETE FROM kb_session_memory_consent WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            )

    def process_session_batch(
        self,
        user_id: str,
        session_id: str,
        turns: list[dict],
    ) -> int:
        """
        Extract facts and topics from *turns* and persist them.

        *turns* is a list of {"role": "user"|"assistant", "content": "..."} dicts.
        Content MUST be PII-scrubbed before calling this method.

        Returns the number of facts stored.
        """
        consented, scope = self.check_consent(user_id, session_id)
        if not consented:
            logger.debug("No memory consent for user=%s session=%s — skipping", user_id, session_id)
            return 0

        if not turns:
            return 0

        # Format turns as a simple transcript for the LLM
        transcript_lines = []
        for t in turns[-TRIGGER_EVERY_N_TURNS * 2:]:  # bound context
            role_label = "User" if t.get("role") == "user" else "Assistant"
            content = str(t.get("content", "")).strip()
            if content:
                transcript_lines.append(f"{role_label}: {content}")
        transcript = "\n".join(transcript_lines)

        if len(transcript) < 100:
            return 0

        raw = _nano_gpt_call(
            _EXTRACT_MODEL,
            _EXTRACT_SYSTEM,
            f"Extract facts from this conversation:\n\n{transcript}",
            max_tokens=800,
        )
        if not raw:
            return 0

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Fact extraction returned non-JSON: %s", raw[:200])
            return 0

        stored = 0

        # Store facts
        for fact in data.get("facts", [])[:_MAX_FACTS_PER_BATCH]:
            confidence = float(fact.get("confidence", 0.0))
            if confidence < _MIN_CONFIDENCE:
                continue
            if fact.get("requires_confirmation"):
                # For now: skip facts requiring user confirmation; KB-13 will wire the prompt
                logger.debug("Skipping fact requiring confirmation: %s", fact.get("fact_key"))
                continue
            try:
                self._store_fact(user_id, fact, scope)
                stored += 1
            except Exception as exc:
                logger.warning("Failed to store fact %s: %s", fact.get("fact_key"), exc)

        # Update interest topics
        for topic in data.get("topics", []):
            try:
                self._add_interest_mention(user_id, str(topic).strip())
            except Exception as exc:
                logger.debug("Failed to update interest %s: %s", topic, exc)

        if stored:
            logger.info("Stored %d facts for user=%s session=%s", stored, user_id, session_id)
        return stored

    def _store_fact(self, user_id: str, fact: dict, consent_level: str) -> None:
        """Upsert a fact into kb_user_facts.

        If a fact with the same (user_id, fact_key) already exists, update it
        only if the new confidence is higher.
        """
        fact_id = str(uuid.uuid4())
        now = _utcnow()
        with _conn() as conn:
            existing = conn.execute(
                "SELECT id, confidence FROM kb_user_facts WHERE user_id = ? AND fact_key = ?",
                (user_id, fact["fact_key"]),
            ).fetchone()

            if existing:
                if float(fact.get("confidence", 1.0)) >= float(existing["confidence"]):
                    conn.execute(
                        """
                        UPDATE kb_user_facts
                        SET fact_value = ?, confidence = ?, source = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            fact["fact_value"],
                            fact.get("confidence", 1.0),
                            fact.get("source", "inferred"),
                            now,
                            existing["id"],
                        ),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO kb_user_facts
                        (id, user_id, fact_type, fact_key, fact_value,
                         confidence, source, consent_level, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact_id,
                        user_id,
                        fact.get("fact_type", "contextual"),
                        fact["fact_key"],
                        fact["fact_value"],
                        fact.get("confidence", 1.0),
                        fact.get("source", "inferred"),
                        consent_level,
                        now,
                        now,
                    ),
                )

    def _add_interest_mention(self, user_id: str, topic: str) -> None:
        """Record a new mention timestamp for *topic* in kb_user_interests."""
        if not topic:
            return
        now = _utcnow()
        with _conn() as conn:
            row = conn.execute(
                "SELECT id, mention_timestamps FROM kb_user_interests WHERE user_id = ? AND topic = ?",
                (user_id, topic),
            ).fetchone()

            if row:
                try:
                    timestamps = json.loads(row["mention_timestamps"] or "[]")
                except Exception:
                    timestamps = []
                timestamps.append(now)
                # Keep at most 100 timestamps (trim oldest)
                timestamps = timestamps[-100:]
                conn.execute(
                    """
                    UPDATE kb_user_interests
                    SET mention_timestamps = ?, last_mentioned = ?
                    WHERE id = ?
                    """,
                    (json.dumps(timestamps), now, row["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO kb_user_interests (id, user_id, topic, mention_timestamps, last_mentioned)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), user_id, topic, json.dumps([now]), now),
                )


# ---------------------------------------------------------------------------
# Auto-summary
# ---------------------------------------------------------------------------

def generate_session_summary(turns: list[dict]) -> Optional[str]:
    """
    Generate a one-sentence summary of a conversation.

    *turns* is a list of {"role": ..., "content": ...} dicts (scrubbed or raw —
    summary is stored in the server-side conversations table, never message content).
    Returns the summary string, or None if the LLM call failed.
    """
    if len(turns) < 3:
        return None

    # Use the first ~20 turns, capped at 4000 chars total
    transcript_lines = []
    char_budget = 4000
    for t in turns[:20]:
        role_label = "User" if t.get("role") == "user" else "Assistant"
        content = str(t.get("content", "")).strip()[:400]
        line = f"{role_label}: {content}"
        if char_budget - len(line) < 0:
            break
        transcript_lines.append(line)
        char_budget -= len(line)

    transcript = "\n".join(transcript_lines)
    if not transcript:
        return None

    return _nano_gpt_call(
        _SUMMARY_MODEL,
        _SUMMARY_SYSTEM,
        transcript,
        max_tokens=60,
    )


# ---------------------------------------------------------------------------
# Convenience: store session memory consent from Nostr command
# ---------------------------------------------------------------------------

_job = FactExtractionJob()


def get_fact_extraction_job() -> FactExtractionJob:
    """Return the module-level singleton FactExtractionJob."""
    return _job
