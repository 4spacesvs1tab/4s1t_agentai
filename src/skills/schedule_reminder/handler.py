#!/usr/bin/env python3
"""
schedule_reminder skill handler — KB-13.

Creates a row in kb_reminders with a UTC trigger_at timestamp resolved from
a natural-language or ISO 8601 expression.

Input:  {"parameters": {"message": ..., "trigger_expression": ..., "user_id": ..., ...}}
Output: {"success": true, "result": {"reminder_id": ..., "trigger_at": ..., ...}}

Depends on: dateparser (pip install dateparser)
"""
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional


# ---------------------------------------------------------------------------
# DB path resolution — same pattern as knowledge_base_search
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

def _parse_trigger(expression: str, user_timezone: str = "UTC") -> Optional[datetime]:
    """
    Parse a natural-language or ISO time expression to a UTC datetime.

    Tries dateparser first (handles "in 2 days", "next Monday at 10am", etc.),
    then falls back to datetime.fromisoformat for plain ISO strings.
    """
    expression = expression.strip()

    try:
        import dateparser  # type: ignore
        settings = {
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DAY_OF_MONTH": "first",
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": user_timezone,  # interpret bare times in user's local TZ
            "TO_TIMEZONE": "UTC",       # then convert to UTC for storage
        }
        parsed = dateparser.parse(expression, settings=settings)
        if parsed:
            return parsed.astimezone(timezone.utc)
    except ImportError:
        pass  # fall through to ISO parse
    except Exception:
        pass

    # ISO 8601 fallback
    try:
        dt = datetime.fromisoformat(expression.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    return None


def _format_local(dt_utc: datetime, tz_name: str) -> str:
    """Format *dt_utc* in the given IANA timezone, falling back to UTC."""
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        local = dt_utc.astimezone(ZoneInfo(tz_name))
        return local.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

def execute(params: dict) -> dict:
    message = params.get("message", "").strip()
    trigger_expression = params.get("trigger_expression", "").strip()
    user_id = params.get("user_id", "").strip()
    user_timezone = params.get("user_timezone", "UTC").strip() or "UTC"
    priority_str = params.get("priority", "normal")
    recurrence = params.get("recurrence") or None

    # Validate required fields
    if not message:
        raise ValueError("'message' is required")
    if not trigger_expression:
        raise ValueError("'trigger_expression' is required")
    if not user_id:
        raise ValueError("'user_id' is required")

    # Resolve username → UUID if needed (agent may pass username instead of UUID)
    import re as _re
    if not _re.match(r'^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$|^[0-9a-fA-F]{32}$', user_id):
        db = str(get_db_path())
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT id FROM users WHERE username = ? OR id = ? LIMIT 1",
                (user_id, user_id),
            ).fetchone()
            if row:
                user_id = row[0]
            else:
                raise ValueError(f"User not found: {user_id!r}")
        finally:
            conn.close()

    # Parse time — interpret bare times in the user's timezone
    trigger_dt = _parse_trigger(trigger_expression, user_timezone=user_timezone)
    if trigger_dt is None:
        raise ValueError(
            f"Could not parse trigger time from: {trigger_expression!r}. "
            "Try an ISO timestamp or a clearer natural-language expression "
            "(e.g. 'tomorrow at 10am', 'in 3 hours', 'next Monday morning')."
        )

    # Map priority string to int
    priority_map = {"normal": 0, "high": 1, "urgent": 2}
    priority_int = priority_map.get(priority_str, 0)

    # Validate recurrence
    valid_recurrences = (None, "daily", "weekly", "monthly")
    if recurrence not in valid_recurrences:
        recurrence = None  # silently discard invalid value

    now_utc = datetime.now(timezone.utc).isoformat()
    reminder_id = str(uuid.uuid4())
    trigger_at_iso = trigger_dt.isoformat()

    db = str(get_db_path())
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            INSERT INTO kb_reminders
                (id, user_id, message, trigger_at, timezone, status,
                 priority, recurrence, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (reminder_id, user_id, message, trigger_at_iso,
             user_timezone, priority_int, recurrence, now_utc),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "reminder_id": reminder_id,
        "trigger_at": trigger_at_iso,
        "trigger_at_local": _format_local(trigger_dt, user_timezone),
        "recurrence": recurrence,
    }


# ---------------------------------------------------------------------------
# Subprocess entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: handler.py input.json output.json", file=sys.stderr)
        sys.exit(1)
    input_path, output_path = sys.argv[1], sys.argv[2]
    try:
        data = json.loads(open(input_path).read())
        params = data.get("parameters", {})
        result = execute(params)
        output = {"success": True, "result": result, "error": None, "logs": []}
    except Exception as exc:
        output = {"success": False, "result": None, "error": str(exc), "logs": []}
    with open(output_path, "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()
