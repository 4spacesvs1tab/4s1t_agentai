#!/usr/bin/env python3
"""
snooze_reminder skill handler.

Finds the most recently delivered (status='sent' or 'snoozed') reminder for the
user and either reschedules it (action='snooze') or cancels it (action='done').
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.db_path import get_db_path






def _resolve_user_id(conn: sqlite3.Connection, user_id: str) -> str:
    """Resolve username → UUID if needed."""
    if re.match(r'^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$|^[0-9a-fA-F]{32}$', user_id):
        return user_id
    row = conn.execute(
        "SELECT id FROM users WHERE username = ? OR id = ? LIMIT 1",
        (user_id, user_id),
    ).fetchone()
    if not row:
        raise ValueError(f"User not found: {user_id!r}")
    return row[0]


def _parse_duration(duration: str) -> timedelta:
    """Parse '1h', '30m', '2h30m', etc. into a timedelta. Defaults to 1h."""
    duration = duration.strip().lower()
    total = timedelta()
    for value, unit in re.findall(r'(\d+)\s*([hm])', duration):
        if unit == 'h':
            total += timedelta(hours=int(value))
        elif unit == 'm':
            total += timedelta(minutes=int(value))
    if total == timedelta():
        total = timedelta(hours=1)
    return total


def _format_local(dt_utc: datetime, tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        local = dt_utc.astimezone(ZoneInfo(tz_name))
        return local.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def execute(params: dict) -> dict:
    user_id = params.get("user_id", "").strip()
    action = params.get("action", "snooze").strip().lower()
    duration_str = params.get("duration", "1h") or "1h"

    if not user_id:
        raise ValueError("'user_id' is required")
    if action not in ("snooze", "done"):
        action = "snooze"

    db = str(get_db_path())
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        user_id = _resolve_user_id(conn, user_id)

        # Find the most recently delivered or snoozed reminder for this user
        row = conn.execute(
            """
            SELECT id, message, trigger_at, timezone, recurrence
            FROM   kb_reminders
            WHERE  user_id = ?
              AND  status IN ('sent', 'snoozed')
            ORDER  BY COALESCE(delivered_at, created_at) DESC
            LIMIT  1
            """,
            (user_id,),
        ).fetchone()

        if not row:
            raise ValueError(
                "No recently delivered reminder found for this user. "
                "Use schedule_reminder to create a new one."
            )

        reminder_id = row["id"]
        message = row["message"]
        tz_name = row["timezone"] or "UTC"
        now_utc = datetime.now(timezone.utc)

        if action == "done":
            conn.execute(
                "UPDATE kb_reminders SET status = 'cancelled' WHERE id = ?",
                (reminder_id,),
            )
            conn.commit()
            return {
                "reminder_id": reminder_id,
                "message": message,
                "action": "done",
                "new_trigger_at": None,
                "new_trigger_at_local": None,
            }
        else:
            delta = _parse_duration(duration_str)
            new_trigger = now_utc + delta
            new_trigger_iso = new_trigger.isoformat()
            conn.execute(
                """
                UPDATE kb_reminders
                SET status = 'pending',
                    trigger_at = ?,
                    snooze_until = ?
                WHERE id = ?
                """,
                (new_trigger_iso, new_trigger_iso, reminder_id),
            )
            conn.commit()
            return {
                "reminder_id": reminder_id,
                "message": message,
                "action": "snooze",
                "new_trigger_at": new_trigger_iso,
                "new_trigger_at_local": _format_local(new_trigger, tz_name),
            }
    finally:
        conn.close()


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
