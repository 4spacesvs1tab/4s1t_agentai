"""
KB scheduling — maintenance jobs.

Periodic background tasks that run alongside ingestion:
  - Proactive assistant (reminders + task nudges)       Phase KB-13
  - Action item extraction                               Phase KB-17
  - Prediction verification (weekly)                    Phase KB-15
  - Cross-domain insights (weekly)                      Phase KB-20
  - Knowledge diff (weekly)                             Phase KB-20
  - Expired conversation message cleanup                 Phase KB-25-H
  - Expired revoked-token cleanup                        Phase KB-26-F

Each public function receives all dependencies (db_path, executor, …) as
explicit arguments — no module-level globals, no imports from scheduler.py.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sqlite3
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger(__name__)


# ── Data helpers (used only by run_proactive) ─────────────────────────────────

def _load_due_reminders(db_path: str, user_id: str) -> list[dict]:
    """Return pending kb_reminders rows whose trigger_at has passed."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            """
            SELECT id, user_id, message, trigger_at, timezone,
                   priority, recurrence, context
            FROM   kb_reminders
            WHERE  user_id = ?
              AND  status  = 'pending'
              AND  trigger_at <= ?
            ORDER  BY trigger_at ASC
            LIMIT  20
            """,
            (user_id, now_iso),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("Could not load due reminders for user=%s: %s", user_id, exc)
        return []


def _mark_reminder_sent(
    db_path: str,
    reminder_id: str,
    recurrence: Optional[str],
    trigger_at_iso: str,
) -> None:
    """Mark a reminder sent; if recurring, advance trigger_at to next occurrence."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(db_path)
        if recurrence:
            try:
                dt = datetime.fromisoformat(trigger_at_iso.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(timezone.utc)
            deltas = {
                "daily": timedelta(days=1),
                "weekly": timedelta(weeks=1),
                "monthly": timedelta(days=30),
            }
            next_dt = dt + deltas.get(recurrence, timedelta(days=1))
            conn.execute(
                "UPDATE kb_reminders SET trigger_at = ?, status = 'pending' WHERE id = ?",
                (next_dt.isoformat(), reminder_id),
            )
        else:
            conn.execute(
                "UPDATE kb_reminders SET status = 'sent', delivered_at = ? WHERE id = ?",
                (now_iso, reminder_id),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Could not mark reminder sent id=%s: %s", reminder_id, exc)


def _load_stale_task_nudges(db_path: str, user_id: str) -> list[dict]:
    """
    Return tasks that need a nudge:
      - due tomorrow (due_date == tomorrow's date, status open/in_progress)
      - open > 14 days with no update (stale alert)
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

        rows = conn.execute(
            """
            SELECT id, title, due_date, status, updated_at,
                   CASE
                     WHEN due_date = :tomorrow THEN 'due_tomorrow'
                     ELSE 'stale'
                   END AS nudge_type
            FROM   kb_tasks
            WHERE  user_id = :user_id
              AND  status IN ('open', 'in_progress', 'blocked')
              AND  (
                     due_date = :tomorrow
                     OR (updated_at < :stale_cutoff AND due_date IS NULL)
                   )
            ORDER  BY due_date ASC NULLS LAST
            LIMIT  5
            """,
            {"user_id": user_id, "tomorrow": tomorrow, "stale_cutoff": stale_cutoff},
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("Could not load task nudges for user=%s: %s", user_id, exc)
        return []


# ── Maintenance coroutines ────────────────────────────────────────────────────

async def run_proactive(user_id: str, db_path: str) -> None:
    """
    Phase KB-13: proactive assistant tick.

    For each active user:
      1. Deliver any kb_reminders whose trigger_at has passed.
      2. Send a nudge for tasks due tomorrow or stale > 14 days.

    NIP-17 delivery goes through NostrNIP17DeliveryAdapter (DeliveryPort).
    Runs at every scheduler tick (top
    of each UTC hour) so the maximum latency for a due reminder is 1 hour.
    """
    try:
        from communication.nostr_nip17.delivery_adapter import get_delivery_port
        _send_dm = get_delivery_port().send

        # ── 1. Due reminders ──────────────────────────────────────────
        due = _load_due_reminders(db_path, user_id)
        for rem in due:
            priority = rem.get("priority", 0)
            msg = rem["message"]
            if priority == 2:
                text = f"🚨 URGENT: {msg}"
            elif priority == 1:
                text = f"⚠️ {msg}"
            else:
                text = f"⏰ {msg}"

            text += "\n\n_Reply 'snooze 1h' or 'done' to manage._"

            ok = await _send_dm(text)
            if ok:
                _mark_reminder_sent(
                    db_path, rem["id"],
                    rem.get("recurrence"), rem["trigger_at"]
                )
                logger.info("Delivered reminder id=%s user=%s", rem["id"], user_id)
            else:
                logger.debug("NIP-17 unavailable — reminder id=%s deferred", rem["id"])

        # ── 2. Task nudges ────────────────────────────────────────────
        nudges = _load_stale_task_nudges(db_path, user_id)
        for task in nudges:
            if task["nudge_type"] == "due_tomorrow":
                msg = (
                    f"[ASSISTANT] Task due tomorrow: '{task['title']}' "
                    f"(status: {task['status']})."
                )
            else:
                msg = (
                    f"[ASSISTANT] Task '{task['title']}' has been open "
                    "for over 2 weeks with no update. Still relevant?"
                )
            await _send_dm(msg)

    except Exception as exc:
        logger.warning("Proactive tick failed for user=%s: %s", user_id, exc)


async def run_action_item_extraction(
    user_id: str,
    db_path: str,
    executor: concurrent.futures.Executor,
) -> None:
    """
    Phase KB-17: nightly action item extraction.

    Scans today's ChromaDB chunks for urgency keywords, then calls the LLM
    to extract concrete action items and stores them in kb_action_items.
    Runs in a thread-pool executor (synchronous httpx + SQLite calls).
    Only runs on ticks at or after _brief_send_hour_utc().
    """
    try:
        loop = asyncio.get_event_loop()
        api_key = os.environ.get("NANO_GPT_API_KEY", "")

        def _extract_sync() -> int:
            from kb.action_item_extractor import ActionItemJob
            job = ActionItemJob(api_key=api_key, db_path=db_path)
            return job.run(user_id=user_id)

        count = await loop.run_in_executor(executor, _extract_sync)
        if count:
            logger.info(
                "Action item extraction user=%s: stored %d item(s)", user_id, count
            )
    except Exception as exc:
        logger.warning("Action item extraction failed for user=%s: %s", user_id, exc)


async def run_prediction_verification(
    user_id: str,
    db_path: str,
    executor: concurrent.futures.Executor,
) -> None:
    """
    Phase KB-15: weekly prediction verification pass.

    Runs on Mondays for all active users.  For each prediction whose
    predicted_date has passed, searches the KB for evidence and asks
    the LLM for a verdict (verified / failed / inconclusive).

    Runs in a thread-pool executor to avoid blocking the event loop
    during synchronous httpx + SQLite calls.
    """
    try:
        loop = asyncio.get_event_loop()
        api_key = os.environ.get("NANO_GPT_API_KEY", "")

        def _verify_sync() -> dict[str, int]:
            from kb.prediction_verifier import PredictionVerifier
            v = PredictionVerifier(api_key=api_key, db_path=db_path)
            return v.verify_pending(user_id=user_id)

        counts = await loop.run_in_executor(executor, _verify_sync)
        if counts.get("total_checked", 0) or counts.get("expired", 0):
            logger.info(
                "Prediction verification user=%s: checked=%d verified=%d "
                "failed=%d inconclusive=%d expired=%d",
                user_id,
                counts.get("total_checked", 0),
                counts.get("verified", 0),
                counts.get("failed", 0),
                counts.get("inconclusive", 0),
                counts.get("expired", 0),
            )
    except Exception as exc:
        logger.warning("Prediction verification failed for user=%s: %s", user_id, exc)


async def run_cross_domain_insights(
    user_id: str,
    db_path: str,
    executor: concurrent.futures.Executor,
) -> None:
    """
    Phase KB-20: weekly cross-domain insight generation.

    Finds entities appearing across multiple KB domains, calls GLM 4.7 once
    to surface connection insights, and writes a brief section file.

    Runs on Mondays via the weekly Monday block in KBScheduler._tick().
    """
    try:
        loop = asyncio.get_event_loop()
        api_key = os.environ.get("NANO_GPT_API_KEY", "")

        def _run_sync() -> int:
            from kb.cross_domain_insights import CrossDomainInsightJob
            job = CrossDomainInsightJob(api_key=api_key, db_path=db_path)
            insights = job.run(user_id=user_id)
            if insights:
                job.write_brief_section(insights)
            return len(insights)

        count = await loop.run_in_executor(executor, _run_sync)
        if count:
            logger.info(
                "KB-20 cross-domain insights: generated %d insight(s) for user=%s",
                count, user_id,
            )
    except Exception as exc:
        logger.warning("KB-20 cross-domain insights failed for user=%s: %s", user_id, exc)


async def run_knowledge_diff(
    user_id: str,
    db_path: str,
    executor: concurrent.futures.Executor,
) -> None:
    """
    Phase KB-20: weekly knowledge diff summary (zero LLM tokens).

    Assembles stats from kb_ingestion_log, kb_predictions, and kb_entities
    into a markdown diff string and writes it to a brief section file.

    Runs on Mondays via the weekly Monday block in KBScheduler._tick().
    """
    try:
        loop = asyncio.get_event_loop()

        def _run_sync() -> Optional[str]:
            from kb.knowledge_diff import KnowledgeDiffJob
            job = KnowledgeDiffJob(db_path=db_path)
            return job.write_brief_section(user_id=user_id)

        path = await loop.run_in_executor(executor, _run_sync)
        if path:
            logger.info("KB-20 knowledge diff written to %s (user=%s)", path, user_id)
    except Exception as exc:
        logger.warning("KB-20 knowledge diff failed for user=%s: %s", user_id, exc)


async def cleanup_expired_messages(
    db_path: str,
    executor: concurrent.futures.Executor,
) -> None:
    """Phase KB-25-H: delete conversation_messages rows past their expires_at.

    Runs once per scheduler tick (hourly).  expires_at is also checked at
    SELECT time, so this is defence-in-depth only — expired messages are
    already invisible to queries before cleanup runs.
    """
    try:
        import sqlite3 as _sq3

        loop = asyncio.get_event_loop()

        def _do_cleanup(path: str) -> int:
            with _sq3.connect(path) as conn:
                result = conn.execute(
                    "DELETE FROM conversation_messages "
                    "WHERE expires_at IS NOT NULL AND expires_at < datetime('now')"
                )
                deleted = result.rowcount
                conn.commit()
            return deleted

        deleted = await loop.run_in_executor(executor, _do_cleanup, db_path)
        if deleted:
            logger.info(
                "KB-25-H cleanup: deleted %d expired conversation messages", deleted
            )
    except Exception as exc:
        logger.warning("KB-25-H message cleanup failed: %s", exc)


async def cleanup_expired_revoked_tokens(
    db_path: str,
    executor: concurrent.futures.Executor,
) -> None:
    """Phase KB-26-F: prune expired rows from revoked_tokens table."""
    try:
        import sqlite3 as _sq3

        loop = asyncio.get_event_loop()

        def _do_cleanup(path: str) -> int:
            with _sq3.connect(path) as conn:
                result = conn.execute(
                    "DELETE FROM revoked_tokens WHERE expires_at < datetime('now')"
                )
                deleted = result.rowcount
                conn.commit()
            return deleted

        deleted = await loop.run_in_executor(executor, _do_cleanup, db_path)
        if deleted:
            logger.info("KB-26-F cleanup: pruned %d expired revoked tokens", deleted)
    except Exception as exc:
        logger.warning("KB-26-F revoked_tokens cleanup failed: %s", exc)
