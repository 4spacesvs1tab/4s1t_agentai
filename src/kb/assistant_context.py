"""
Assistant Context — Phase KB-13.

Provides three closely-related building blocks that together transform the
chatbot into a context-aware assistant:

  UserProfile
      Assembled from the DB at session start.  Carries the user's name,
      language, role, active interests, upcoming tasks, and reminders.
      Exposes to_system_prompt_snippet() for injection into agent prompts.

  LanguagePolicy
      Resolves the response language for a given user message using a
      four-level waterfall:
        1. Inline override in the message ("reply in English")
        2. Stored fact in kb_user_facts (source='explicit')
        3. langdetect on the message text (requires ≥ 8 words / 0.85 conf)
        4. User's default_language column

  AssistantContext
      Thin coordinator.  build(user_id, session_id, message, db_path) returns
      a ready-to-use (UserProfile, response_language) tuple.  Performs only
      fast synchronous DB look-ups — no LLM calls, safe on the async path.

Design reference: KB_assistant_design_v2.md §7.1, §7.2, §5.2
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)



# Minimum langdetect confidence and word count to trust auto-detection
_LANG_MIN_CONFIDENCE = 0.85
_LANG_MIN_WORDS = 8

# Half-life for interest decay (days); matches kb_user_interests design
_INTEREST_HALF_LIFE_DAYS = 14.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



def _open(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _decay_score(timestamps_json: str) -> float:
    """
    Compute the interest decay score from a JSON array of ISO timestamps.

    Score = sum of exp(-λ * age_days) for each mention, where
    λ = ln(2) / half_life_days (so score halves every _INTEREST_HALF_LIFE_DAYS).
    """
    try:
        timestamps = json.loads(timestamps_json or "[]")
    except Exception:
        return 0.0

    now = datetime.now(timezone.utc)
    lam = math.log(2) / _INTEREST_HALF_LIFE_DAYS
    score = 0.0
    for ts in timestamps:
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_days = (now - t).total_seconds() / 86_400
            score += math.exp(-lam * age_days)
        except Exception:
            pass
    return score


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------

@dataclass
class UserProfile:
    user_id: str
    display_name: str
    default_language: str                   # 'pl' | 'en'
    role_description: str                   # e.g. "Business Analyst, Warsaw"
    user_timezone: str = "Europe/Warsaw"    # IANA TZ string
    active_interests: list[str] = field(default_factory=list)
    # top topics by current decay score, descending
    active_tasks: list[dict] = field(default_factory=list)
    # tasks due within the next 7 days, open/in_progress only
    upcoming_reminders: list[dict] = field(default_factory=list)
    # reminders firing within the next 48 hours
    memory_scope: str = "off"               # 'off' | 'private' | 'family' | 'team'

    # ------------------------------------------------------------------
    def to_system_prompt_snippet(self) -> str:
        """
        Return a compact paragraph for injection into the agent system prompt.

        Keeps the snippet short (< 300 chars in typical cases) to avoid
        token bloat.  Only non-empty sections are included.
        """
        lines: list[str] = []

        name_role = self.display_name
        if self.role_description:
            name_role += f", {self.role_description}"
        lines.append(f"You are speaking with {name_role}.")

        if self.active_interests:
            interests = ", ".join(self.active_interests[:5])
            lines.append(f"Their active interests: {interests}.")

        if self.active_tasks:
            task_bullets = "; ".join(
                f"{t['title']} (due {t['due_date']})" if t.get("due_date")
                else t["title"]
                for t in self.active_tasks[:3]
            )
            lines.append(f"Upcoming tasks: {task_bullets}.")

        if self.upcoming_reminders:
            rem_bullets = "; ".join(
                r["message"] for r in self.upcoming_reminders[:2]
            )
            lines.append(f"Upcoming reminders: {rem_bullets}.")

        lines.append(f"Their timezone: {self.user_timezone}.")
        lines.append(
            f"Respond in {self.default_language} unless their message "
            "is in a different language."
        )

        return " ".join(lines)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, user_id: str, db_path: Optional[str] = None) -> "UserProfile":
        """
        Load a UserProfile from the database.  Returns a minimal profile on
        any error so the caller is never blocked by a DB failure.
        """
        path = db_path or str(get_db_path())
        try:
            return cls._load_from_db(user_id, path)
        except Exception as exc:
            logger.warning("UserProfile.load failed for user=%s: %s", user_id, exc)
            return cls(
                user_id=user_id,
                display_name="User",
                default_language="en",
                role_description="",
            )

    @classmethod
    def _load_from_db(cls, user_id: str, db_path: str) -> "UserProfile":
        conn = _open(db_path)
        now = datetime.now(timezone.utc)
        try:
            # ── Basic user row ─────────────────────────────────────────────
            row = conn.execute(
                """
                SELECT username, default_language, role_description,
                       memory_scope, user_timezone
                FROM   users
                WHERE  id = ?
                """,
                (user_id,),
            ).fetchone()

            if row is None:
                raise ValueError(f"User {user_id!r} not found")

            display_name = row["username"]
            default_language = (row["default_language"] or "en").strip()
            role_description = row["role_description"] or ""
            memory_scope = row["memory_scope"] or "off"
            user_timezone = row["user_timezone"] or "Europe/Warsaw"

            # ── Active interests (top 5 by decay score) ───────────────────
            interest_rows = conn.execute(
                """
                SELECT topic, mention_timestamps
                FROM   kb_user_interests
                WHERE  user_id = ?
                ORDER  BY last_mentioned DESC
                LIMIT  30
                """,
                (user_id,),
            ).fetchall()

            scored = sorted(
                [(r["topic"], _decay_score(r["mention_timestamps"]))
                 for r in interest_rows],
                key=lambda x: x[1],
                reverse=True,
            )
            active_interests = [t for t, _ in scored if _ > 0][:10]

            # ── Active tasks (due within 7 days) ──────────────────────────
            cutoff = (now + timedelta(days=7)).date().isoformat()
            task_rows = conn.execute(
                """
                SELECT id, title, due_date, priority, status
                FROM   kb_tasks
                WHERE  user_id = ?
                  AND  status IN ('open', 'in_progress', 'blocked')
                  AND  (due_date IS NULL OR due_date <= ?)
                ORDER  BY due_date ASC NULLS LAST, priority DESC
                LIMIT  10
                """,
                (user_id, cutoff),
            ).fetchall()
            active_tasks = [dict(r) for r in task_rows]

            # ── Upcoming reminders (next 48 h) ────────────────────────────
            in_48h = (now + timedelta(hours=48)).isoformat()
            rem_rows = conn.execute(
                """
                SELECT id, message, trigger_at, priority
                FROM   kb_reminders
                WHERE  user_id = ?
                  AND  status = 'pending'
                  AND  trigger_at <= ?
                ORDER  BY trigger_at ASC
                LIMIT  5
                """,
                (user_id, in_48h),
            ).fetchall()
            upcoming_reminders = [dict(r) for r in rem_rows]

        finally:
            conn.close()

        return cls(
            user_id=user_id,
            display_name=display_name,
            default_language=default_language,
            role_description=role_description,
            user_timezone=user_timezone,
            active_interests=active_interests,
            active_tasks=active_tasks,
            upcoming_reminders=upcoming_reminders,
            memory_scope=memory_scope,
        )


# ---------------------------------------------------------------------------
# LanguagePolicy
# ---------------------------------------------------------------------------

# Inline override patterns (case-insensitive)
_INLINE_EN = re.compile(
    r"\b(reply|respond|answer|write)\s+(in\s+)?english\b",
    re.IGNORECASE,
)
_INLINE_PL = re.compile(
    r"\b(odpowiedz|odpisz|pisz|pisać)\s+(po\s+)?polsku\b"
    r"|reply\s+in\s+polish\b"
    r"|\bpo\s+polsku\b",
    re.IGNORECASE,
)


class LanguagePolicy:
    """
    Four-level language resolution waterfall.

    All methods are synchronous and fast (no LLM, no embeddings).
    """

    def resolve(
        self,
        user_id: str,
        user_message: str,
        db_path: Optional[str] = None,
        default_language: str = "en",
    ) -> str:
        """
        Return the ISO 639-1 code for the response language ('en' or 'pl').

        Parameters
        ----------
        user_id :
            Used for stored-preference look-up.
        user_message :
            The raw user text.
        db_path :
            SQLite path.  Falls back to AGENT_DB_PATH env var.
        default_language :
            Pre-loaded default (from users.default_language) — avoids a
            redundant DB query if the caller already has UserProfile.
        """
        # 1. Inline override
        inline = self._detect_inline_override(user_message)
        if inline:
            return inline

        # 2. Stored explicit preference in kb_user_facts
        stored = self._get_stored_preference(user_id, db_path or str(get_db_path()))
        if stored:
            return stored

        # 3. langdetect on the message
        detected = self._detect_language(user_message)
        if detected:
            return detected

        # 4. Fallback: caller's pre-loaded default
        return default_language

    # ------------------------------------------------------------------

    @staticmethod
    def _detect_inline_override(message: str) -> Optional[str]:
        if _INLINE_EN.search(message):
            return "en"
        if _INLINE_PL.search(message):
            return "pl"
        return None

    @staticmethod
    def _get_stored_preference(user_id: str, db_path: str) -> Optional[str]:
        try:
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                """
                SELECT fact_value FROM kb_user_facts
                WHERE  user_id = ?
                  AND  fact_key = 'response_language'
                  AND  source   = 'explicit'
                  AND  (expires_at IS NULL OR expires_at > datetime('now'))
                ORDER  BY updated_at DESC
                LIMIT  1
                """,
                (user_id,),
            ).fetchone()
            conn.close()
            if row:
                lang = row[0].strip().lower()[:2]
                if lang in ("pl", "en"):
                    return lang
        except Exception:
            pass
        return None

    @staticmethod
    def _detect_language(message: str) -> Optional[str]:
        words = message.split()
        if len(words) < _LANG_MIN_WORDS:
            return None
        try:
            from langdetect import detect_langs  # type: ignore
            results = detect_langs(message)
            for r in results:
                if r.lang in ("pl", "en") and r.prob >= _LANG_MIN_CONFIDENCE:
                    return r.lang
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# AssistantContext
# ---------------------------------------------------------------------------

_language_policy = LanguagePolicy()


@dataclass
class AssistantContext:
    """
    Assembled context for a single request.

    Attributes
    ----------
    profile :
        Loaded UserProfile.
    response_language :
        Resolved language code for this response ('en' or 'pl').
    system_prompt_snippet :
        Ready-to-inject paragraph for the agent system prompt.
    """
    profile: UserProfile
    response_language: str
    system_prompt_snippet: str

    @classmethod
    def build(
        cls,
        user_id: str,
        session_id: str,
        message: str,
        db_path: Optional[str] = None,
    ) -> "AssistantContext":
        """
        Build an AssistantContext for a single turn.

        Safe to call on the async path — performs only fast synchronous
        DB look-ups (< 5 ms on typical host hardware).
        """
        profile = UserProfile.load(user_id, db_path=db_path)

        response_language = _language_policy.resolve(
            user_id=user_id,
            user_message=message,
            db_path=db_path,
            default_language=profile.default_language,
        )

        snippet = profile.to_system_prompt_snippet()

        return cls(
            profile=profile,
            response_language=response_language,
            system_prompt_snippet=snippet,
        )
