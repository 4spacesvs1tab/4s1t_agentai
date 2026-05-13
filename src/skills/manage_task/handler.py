#!/usr/bin/env python3
"""
manage_task skill handler — KB-13.

CRUD interface for kb_tasks and kb_task_updates.

Input:  {"parameters": {"action": ..., "user_id": ..., ...}}
Output: {"success": true, "result": {"task_id": ..., "task": {...}, ...}}
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
# DB path resolution
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_task(conn: sqlite3.Connection, task_id: str, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM kb_tasks WHERE id = ? AND user_id = ?",
        (task_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Task {task_id!r} not found for user {user_id!r}")
    return dict(row)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _create(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    title = (params.get("title") or "").strip()
    if not title:
        raise ValueError("'title' is required for create")

    task_id = str(uuid.uuid4())
    now = _now()
    priority = params.get("priority", "normal")
    valid_priorities = {"low", "normal", "high", "urgent"}
    if priority not in valid_priorities:
        priority = "normal"

    conn.execute(
        """
        INSERT INTO kb_tasks
            (id, user_id, title, description, status, priority,
             due_date, due_time, timezone, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, 'manual', ?, ?)
        """,
        (
            task_id, user_id, title,
            params.get("description"),
            priority,
            params.get("due_date"),
            params.get("due_time"),
            params.get("user_timezone"),
            now, now,
        ),
    )
    conn.commit()
    task = _fetch_task(conn, task_id, user_id)
    return {
        "task_id": task_id,
        "task": task,
        "message": f"Task created: '{title}'"
                   + (f" (due {task['due_date']})" if task.get("due_date") else ""),
    }


def _update(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    task_id = (params.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("'task_id' is required for update")

    # Fetch first to confirm ownership
    _fetch_task(conn, task_id, user_id)

    updates: list[str] = []
    values: list = []

    for col, param_key in [
        ("title",       "title"),
        ("description", "description"),
        ("status",      "status"),
        ("priority",    "priority"),
        ("due_date",    "due_date"),
        ("due_time",    "due_time"),
    ]:
        if param_key in params and params[param_key] is not None:
            updates.append(f"{col} = ?")
            values.append(params[param_key])

    if not updates:
        raise ValueError("No fields provided to update")

    updates.append("updated_at = ?")
    values.append(_now())
    values.append(task_id)
    values.append(user_id)

    conn.execute(
        f"UPDATE kb_tasks SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    conn.commit()
    task = _fetch_task(conn, task_id, user_id)
    return {"task_id": task_id, "task": task, "message": "Task updated."}


def _complete(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    task_id = (params.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("'task_id' is required for complete")

    _fetch_task(conn, task_id, user_id)
    now = _now()
    conn.execute(
        """
        UPDATE kb_tasks
           SET status = 'done', completed_at = ?, updated_at = ?
         WHERE id = ? AND user_id = ?
        """,
        (now, now, task_id, user_id),
    )
    conn.commit()
    task = _fetch_task(conn, task_id, user_id)
    return {"task_id": task_id, "task": task, "message": f"Task '{task['title']}' marked done."}


def _cancel(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    task_id = (params.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("'task_id' is required for cancel")

    _fetch_task(conn, task_id, user_id)
    now = _now()
    conn.execute(
        "UPDATE kb_tasks SET status = 'cancelled', updated_at = ? WHERE id = ? AND user_id = ?",
        (now, task_id, user_id),
    )
    conn.commit()
    task = _fetch_task(conn, task_id, user_id)
    return {"task_id": task_id, "task": task, "message": f"Task '{task['title']}' cancelled."}


def _list(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    filter_status = params.get("filter_status", "active")
    if filter_status == "active":
        rows = conn.execute(
            """
            SELECT * FROM kb_tasks
            WHERE  user_id = ?
              AND  status IN ('open', 'in_progress', 'blocked')
            ORDER  BY due_date ASC NULLS LAST, priority DESC
            LIMIT  50
            """,
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM kb_tasks
            WHERE  user_id = ? AND status = ?
            ORDER  BY due_date ASC NULLS LAST, updated_at DESC
            LIMIT  50
            """,
            (user_id, filter_status),
        ).fetchall()

    tasks = [dict(r) for r in rows]
    return {
        "tasks": tasks,
        "message": f"{len(tasks)} task(s) found.",
    }


def _add_update(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    task_id = (params.get("task_id") or "").strip()
    update_text = (params.get("update_text") or "").strip()
    if not task_id:
        raise ValueError("'task_id' is required for add_update")
    if not update_text:
        raise ValueError("'update_text' is required for add_update")

    _fetch_task(conn, task_id, user_id)

    update_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO kb_task_updates (id, task_id, update_text, created_at) VALUES (?, ?, ?, ?)",
        (update_id, task_id, update_text, now),
    )
    conn.execute(
        "UPDATE kb_tasks SET updated_at = ? WHERE id = ? AND user_id = ?",
        (now, task_id, user_id),
    )
    conn.commit()
    return {"task_id": task_id, "message": "Progress note added."}


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

_ACTIONS = {
    "create":     _create,
    "update":     _update,
    "complete":   _complete,
    "cancel":     _cancel,
    "list":       _list,
    "add_update": _add_update,
}


def execute(params: dict) -> dict:
    action = (params.get("action") or "").strip().lower()
    user_id = (params.get("user_id") or "").strip()

    if not action:
        raise ValueError("'action' is required")
    if not user_id:
        raise ValueError("'user_id' is required")
    if action not in _ACTIONS:
        raise ValueError(
            f"Unknown action '{action}'. "
            f"Valid actions: {', '.join(_ACTIONS)}"
        )

    conn = _open()
    try:
        return _ACTIONS[action](conn, user_id, params)
    finally:
        conn.close()


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
