#!/usr/bin/env python3
"""
map_stakeholders skill handler.

CRUD + influence/interest matrix analysis for ba_stakeholders.

Input:  {"parameters": {"action": ..., "user_id": ..., ...}}
Output: {"success": true, "result": {...}}
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path

# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------






def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema bootstrap (idempotent)
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS ba_stakeholders (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    project             TEXT NOT NULL,
    name                TEXT NOT NULL,
    role                TEXT,
    interest            TEXT,
    influence           TEXT,
    position            TEXT,
    engagement_approach TEXT,
    notes               TEXT,
    last_updated        TEXT NOT NULL
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_stakeholder(conn: sqlite3.Connection, stakeholder_id: str, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM ba_stakeholders WHERE id = ? AND user_id = ?",
        (stakeholder_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Stakeholder {stakeholder_id!r} not found for user {user_id!r}")
    return dict(row)


def _row_list(rows) -> list:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _add(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    name = (params.get("name") or "").strip()
    if not project:
        raise ValueError("'project' is required for add")
    if not name:
        raise ValueError("'name' is required for add")

    stakeholder_id = str(uuid.uuid4())
    now = _now()

    conn.execute(
        """
        INSERT INTO ba_stakeholders
            (id, user_id, project, name, role, interest, influence,
             position, engagement_approach, notes, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stakeholder_id, user_id, project, name,
            params.get("role"),
            params.get("interest"),
            params.get("influence"),
            params.get("position", "unknown"),
            params.get("engagement_approach"),
            params.get("notes"),
            now,
        ),
    )
    conn.commit()
    record = _fetch_stakeholder(conn, stakeholder_id, user_id)
    return {
        "stakeholder_id": stakeholder_id,
        "record": record,
        "message": f"Stakeholder '{name}' added to project '{project}'.",
    }


def _update(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    stakeholder_id = (params.get("stakeholder_id") or "").strip()
    if not stakeholder_id:
        raise ValueError("'stakeholder_id' is required for update")

    _fetch_stakeholder(conn, stakeholder_id, user_id)  # ownership check

    updatable = [
        ("name",                "name"),
        ("role",                "role"),
        ("interest",            "interest"),
        ("influence",           "influence"),
        ("position",            "position"),
        ("engagement_approach", "engagement_approach"),
        ("notes",               "notes"),
        ("project",             "project"),
    ]
    updates: list[str] = []
    values: list = []

    for col, key in updatable:
        if key in params and params[key] is not None:
            updates.append(f"{col} = ?")
            values.append(params[key])

    if not updates:
        raise ValueError("No fields provided to update")

    updates.append("last_updated = ?")
    values.append(_now())
    values.extend([stakeholder_id, user_id])

    conn.execute(
        f"UPDATE ba_stakeholders SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    conn.commit()
    record = _fetch_stakeholder(conn, stakeholder_id, user_id)
    return {
        "stakeholder_id": stakeholder_id,
        "record": record,
        "message": f"Stakeholder '{record['name']}' updated.",
    }


def _remove(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    stakeholder_id = (params.get("stakeholder_id") or "").strip()
    if not stakeholder_id:
        raise ValueError("'stakeholder_id' is required for remove")

    record = _fetch_stakeholder(conn, stakeholder_id, user_id)
    conn.execute(
        "DELETE FROM ba_stakeholders WHERE id = ? AND user_id = ?",
        (stakeholder_id, user_id),
    )
    conn.commit()
    return {"message": f"Stakeholder '{record['name']}' removed."}


def _list(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    if not project:
        raise ValueError("'project' is required for list")

    rows = conn.execute(
        """
        SELECT * FROM ba_stakeholders
        WHERE user_id = ? AND project = ?
        ORDER BY name ASC
        """,
        (user_id, project),
    ).fetchall()
    stakeholders = _row_list(rows)
    return {"stakeholders": stakeholders, "count": len(stakeholders)}


def _analyze(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    if not project:
        raise ValueError("'project' is required for analyze")

    rows = conn.execute(
        "SELECT * FROM ba_stakeholders WHERE user_id = ? AND project = ? ORDER BY name ASC",
        (user_id, project),
    ).fetchall()
    stakeholders = _row_list(rows)

    quadrants: dict[str, list] = {
        "manage_closely": [],
        "keep_satisfied": [],
        "keep_informed":  [],
        "monitor":        [],
    }

    for s in stakeholders:
        influence = (s.get("influence") or "low").lower()
        interest  = (s.get("interest")  or "low").lower()
        high_influence = influence == "high"
        high_interest  = interest  == "high"

        entry = {"id": s["id"], "name": s["name"], "role": s.get("role"),
                 "influence": influence, "interest": interest,
                 "position": s.get("position", "unknown")}

        if high_influence and high_interest:
            quadrants["manage_closely"].append(entry)
        elif high_influence and not high_interest:
            quadrants["keep_satisfied"].append(entry)
        elif not high_influence and high_interest:
            quadrants["keep_informed"].append(entry)
        else:
            quadrants["monitor"].append(entry)

    # Markdown matrix table
    def _names(lst: list) -> str:
        return ", ".join(s["name"] for s in lst) if lst else "—"

    matrix_markdown = (
        "| | **High Influence** | **Low/Medium Influence** |\n"
        "|---|---|---|\n"
        f"| **High Interest** | Manage Closely: {_names(quadrants['manage_closely'])} "
        f"| Keep Informed: {_names(quadrants['keep_informed'])} |\n"
        f"| **Low/Medium Interest** | Keep Satisfied: {_names(quadrants['keep_satisfied'])} "
        f"| Monitor: {_names(quadrants['monitor'])} |\n"
    )

    # RACI position summary
    position_counts: dict[str, int] = {
        "champion": 0, "supporter": 0, "neutral": 0, "blocker": 0, "unknown": 0,
    }
    for s in stakeholders:
        pos = (s.get("position") or "unknown").lower()
        if pos in position_counts:
            position_counts[pos] += 1
        else:
            position_counts["unknown"] += 1

    return {
        "quadrants": quadrants,
        "matrix_markdown": matrix_markdown,
        "raci_summary": position_counts,
        "count": len(stakeholders),
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_ACTIONS = {
    "add":     _add,
    "update":  _update,
    "remove":  _remove,
    "list":    _list,
    "analyze": _analyze,
}


def execute(params: dict) -> dict:
    action = (params.get("action") or "list").strip().lower()
    user_id = (params.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("'user_id' is required")
    if action not in _ACTIONS:
        raise ValueError(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}")
    conn = _open()
    try:
        _ensure_schema(conn)
        return _ACTIONS[action](conn, user_id, params)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Subprocess entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    if len(sys.argv) < 3:
        print("Usage: handler.py input.json output.json", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(open(sys.argv[1]).read())
        result = execute(data.get("parameters", {}))
        output = {"success": True, "result": result, "error": None, "logs": []}
    except Exception as exc:
        output = {"success": False, "result": None, "error": str(exc), "logs": []}
    with open(sys.argv[2], "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()
