#!/usr/bin/env python3
"""
define_success_metrics skill handler.

CRUD for ba_kpis (success metrics / KPIs).

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
CREATE TABLE IF NOT EXISTS ba_kpis (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    project            TEXT NOT NULL,
    metric_name        TEXT NOT NULL,
    description        TEXT,
    unit               TEXT,
    baseline           REAL,
    target             REAL,
    direction          TEXT NOT NULL DEFAULT 'higher_is_better',
    measurement_method TEXT,
    frequency          TEXT,
    owner              TEXT,
    status             TEXT NOT NULL DEFAULT 'active',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE(user_id, project, metric_name)
);
"""

_VALID_DIRECTIONS  = {"higher_is_better", "lower_is_better", "target_value"}
_VALID_FREQUENCIES = {"daily", "weekly", "monthly", "on_demand"}


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_metric(conn: sqlite3.Connection, metric_id: str, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM ba_kpis WHERE id = ? AND user_id = ?",
        (metric_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Metric {metric_id!r} not found for user {user_id!r}")
    return dict(row)


def _row_list(rows) -> list:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _add(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project     = (params.get("project")     or "").strip()
    metric_name = (params.get("metric_name") or "").strip()
    if not project:
        raise ValueError("'project' is required for add")
    if not metric_name:
        raise ValueError("'metric_name' is required for add")

    direction = (params.get("direction") or "higher_is_better").lower()
    if direction not in _VALID_DIRECTIONS:
        raise ValueError(
            f"Invalid direction '{direction}'. Must be one of: {', '.join(sorted(_VALID_DIRECTIONS))}"
        )

    frequency = params.get("frequency")
    if frequency and frequency.lower() not in _VALID_FREQUENCIES:
        raise ValueError(
            f"Invalid frequency '{frequency}'. Must be one of: {', '.join(sorted(_VALID_FREQUENCIES))}"
        )

    metric_id = str(uuid.uuid4())
    now = _now()

    baseline = params.get("baseline")
    target   = params.get("target")
    # Accept numeric types; coerce strings cautiously
    if baseline is not None and not isinstance(baseline, (int, float)):
        baseline = float(baseline)
    if target is not None and not isinstance(target, (int, float)):
        target = float(target)

    try:
        conn.execute(
            """
            INSERT INTO ba_kpis
                (id, user_id, project, metric_name, description, unit,
                 baseline, target, direction, measurement_method, frequency, owner,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                metric_id, user_id, project, metric_name,
                params.get("description"),
                params.get("unit"),
                baseline, target, direction,
                params.get("measurement_method"),
                frequency,
                params.get("owner"),
                now, now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(
            f"Metric '{metric_name}' already exists for project '{project}'."
        )

    record = _fetch_metric(conn, metric_id, user_id)
    return {
        "metric_id": metric_id,
        "record": record,
        "message": f"Metric '{metric_name}' added to project '{project}'.",
    }


def _update(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    metric_id = (params.get("metric_id") or "").strip()
    if not metric_id:
        raise ValueError("'metric_id' is required for update")

    _fetch_metric(conn, metric_id, user_id)  # ownership check

    updates: list[str] = []
    values: list = []

    scalar_fields = [
        ("metric_name",        "metric_name"),
        ("description",        "description"),
        ("unit",               "unit"),
        ("measurement_method", "measurement_method"),
        ("owner",              "owner"),
        ("project",            "project"),
        ("status",             "status"),
    ]
    for col, key in scalar_fields:
        if key in params and params[key] is not None:
            updates.append(f"{col} = ?")
            values.append(params[key])

    for col, key in [("baseline", "baseline"), ("target", "target")]:
        if key in params and params[key] is not None:
            val = params[key]
            if not isinstance(val, (int, float)):
                val = float(val)
            updates.append(f"{col} = ?")
            values.append(val)

    if "direction" in params and params["direction"] is not None:
        d = params["direction"].lower()
        if d not in _VALID_DIRECTIONS:
            raise ValueError(
                f"Invalid direction '{d}'. Must be one of: {', '.join(sorted(_VALID_DIRECTIONS))}"
            )
        updates.append("direction = ?")
        values.append(d)

    if "frequency" in params and params["frequency"] is not None:
        f = params["frequency"].lower()
        if f not in _VALID_FREQUENCIES:
            raise ValueError(
                f"Invalid frequency '{f}'. Must be one of: {', '.join(sorted(_VALID_FREQUENCIES))}"
            )
        updates.append("frequency = ?")
        values.append(f)

    if not updates:
        raise ValueError("No fields provided to update")

    updates.append("updated_at = ?")
    values.append(_now())
    values.extend([metric_id, user_id])

    conn.execute(
        f"UPDATE ba_kpis SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    conn.commit()
    record = _fetch_metric(conn, metric_id, user_id)
    return {
        "metric_id": metric_id,
        "record": record,
        "message": f"Metric '{record['metric_name']}' updated.",
    }


def _list(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    if not project:
        raise ValueError("'project' is required for list")

    rows = conn.execute(
        "SELECT * FROM ba_kpis WHERE user_id = ? AND project = ? ORDER BY metric_name ASC",
        (user_id, project),
    ).fetchall()
    metrics = _row_list(rows)
    return {"metrics": metrics, "count": len(metrics)}


def _remove(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    metric_id = (params.get("metric_id") or "").strip()
    if not metric_id:
        raise ValueError("'metric_id' is required for remove")

    record = _fetch_metric(conn, metric_id, user_id)
    conn.execute(
        "DELETE FROM ba_kpis WHERE id = ? AND user_id = ?",
        (metric_id, user_id),
    )
    conn.commit()
    return {"message": f"Metric '{record['metric_name']}' removed."}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_ACTIONS = {
    "add":    _add,
    "update": _update,
    "list":   _list,
    "remove": _remove,
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
