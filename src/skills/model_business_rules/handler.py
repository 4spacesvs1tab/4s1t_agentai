#!/usr/bin/env python3
"""
model_business_rules skill handler.

CRUD + search + deprecation for ba_business_rules.

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
CREATE TABLE IF NOT EXISTS ba_business_rules (
    id                    TEXT PRIMARY KEY,
    user_id               TEXT NOT NULL,
    project               TEXT,
    rule_name             TEXT NOT NULL,
    rule_type             TEXT NOT NULL,
    trigger_condition     TEXT,
    condition_text        TEXT,
    action_text           TEXT,
    exception_text        TEXT,
    source_requirement_id TEXT,
    owner                 TEXT,
    status                TEXT NOT NULL DEFAULT 'active',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
"""

_VALID_RULE_TYPES = {"operational", "structural", "derivation"}
_VALID_STATUSES   = {"active", "draft", "deprecated"}


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_rule(conn: sqlite3.Connection, rule_id: str, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM ba_business_rules WHERE id = ? AND user_id = ?",
        (rule_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Rule {rule_id!r} not found for user {user_id!r}")
    return dict(row)


def _row_list(rows) -> list:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _add(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    rule_name = (params.get("rule_name") or "").strip()
    rule_type = (params.get("rule_type") or "").strip().lower()
    if not rule_name:
        raise ValueError("'rule_name' is required for add")
    if not rule_type:
        raise ValueError("'rule_type' is required for add")
    if rule_type not in _VALID_RULE_TYPES:
        raise ValueError(
            f"Invalid rule_type '{rule_type}'. Must be one of: {', '.join(sorted(_VALID_RULE_TYPES))}"
        )

    rule_id = str(uuid.uuid4())
    now = _now()
    status = (params.get("status") or "active").lower()
    if status not in _VALID_STATUSES:
        status = "active"

    conn.execute(
        """
        INSERT INTO ba_business_rules
            (id, user_id, project, rule_name, rule_type,
             trigger_condition, condition_text, action_text, exception_text,
             source_requirement_id, owner, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rule_id, user_id,
            params.get("project") or None,
            rule_name, rule_type,
            params.get("trigger_condition"),
            params.get("condition_text"),
            params.get("action_text"),
            params.get("exception_text"),
            params.get("source_requirement_id"),
            params.get("owner"),
            status,
            now, now,
        ),
    )
    conn.commit()
    record = _fetch_rule(conn, rule_id, user_id)
    return {
        "rule_id": rule_id,
        "record": record,
        "message": f"Business rule '{rule_name}' ({rule_type}) added.",
    }


def _update(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    rule_id = (params.get("rule_id") or "").strip()
    if not rule_id:
        raise ValueError("'rule_id' is required for update")

    _fetch_rule(conn, rule_id, user_id)  # ownership check

    updatable = [
        ("rule_name",             "rule_name"),
        ("rule_type",             "rule_type"),
        ("trigger_condition",     "trigger_condition"),
        ("condition_text",        "condition_text"),
        ("action_text",           "action_text"),
        ("exception_text",        "exception_text"),
        ("source_requirement_id", "source_requirement_id"),
        ("owner",                 "owner"),
        ("status",                "status"),
        ("project",               "project"),
    ]
    updates: list[str] = []
    values: list = []

    for col, key in updatable:
        if key in params and params[key] is not None:
            val = params[key]
            if key == "rule_type" and val.lower() not in _VALID_RULE_TYPES:
                raise ValueError(
                    f"Invalid rule_type '{val}'. Must be one of: {', '.join(sorted(_VALID_RULE_TYPES))}"
                )
            if key == "status" and val.lower() not in _VALID_STATUSES:
                raise ValueError(
                    f"Invalid status '{val}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}"
                )
            updates.append(f"{col} = ?")
            values.append(val)

    if not updates:
        raise ValueError("No fields provided to update")

    updates.append("updated_at = ?")
    values.append(_now())
    values.extend([rule_id, user_id])

    conn.execute(
        f"UPDATE ba_business_rules SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    conn.commit()
    record = _fetch_rule(conn, rule_id, user_id)
    return {
        "rule_id": rule_id,
        "record": record,
        "message": f"Rule '{record['rule_name']}' updated.",
    }


def _list(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project   = params.get("project")   or None
    rule_type = params.get("rule_type") or None
    status    = (params.get("status") or "active").lower()
    if status not in _VALID_STATUSES:
        status = "active"

    clauses = ["user_id = ?", "status = ?"]
    args: list = [user_id, status]

    if project:
        clauses.append("project = ?")
        args.append(project)
    if rule_type:
        if rule_type.lower() not in _VALID_RULE_TYPES:
            raise ValueError(
                f"Invalid rule_type '{rule_type}'. Must be one of: {', '.join(sorted(_VALID_RULE_TYPES))}"
            )
        clauses.append("rule_type = ?")
        args.append(rule_type.lower())

    sql = (
        f"SELECT * FROM ba_business_rules WHERE {' AND '.join(clauses)} "
        "ORDER BY rule_type ASC, rule_name ASC"
    )
    rows = conn.execute(sql, args).fetchall()
    rules = _row_list(rows)
    return {"rules": rules, "count": len(rules)}


def _search(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    query = (params.get("query") or "").strip()
    if not query:
        raise ValueError("'query' is required for search")

    project = params.get("project") or None
    like = f"%{query}%"

    if project:
        rows = conn.execute(
            """
            SELECT * FROM ba_business_rules
            WHERE user_id = ? AND project = ?
              AND (rule_name LIKE ? OR condition_text LIKE ? OR action_text LIKE ?)
            ORDER BY rule_name ASC
            """,
            (user_id, project, like, like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM ba_business_rules
            WHERE user_id = ?
              AND (rule_name LIKE ? OR condition_text LIKE ? OR action_text LIKE ?)
            ORDER BY rule_name ASC
            """,
            (user_id, like, like, like),
        ).fetchall()

    matches = _row_list(rows)
    return {"matches": matches, "count": len(matches)}


def _deprecate(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    rule_id = (params.get("rule_id") or "").strip()
    if not rule_id:
        raise ValueError("'rule_id' is required for deprecate")

    record = _fetch_rule(conn, rule_id, user_id)
    conn.execute(
        "UPDATE ba_business_rules SET status = 'deprecated', updated_at = ? WHERE id = ? AND user_id = ?",
        (_now(), rule_id, user_id),
    )
    conn.commit()
    return {"message": f"Rule '{record['rule_name']}' deprecated."}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_ACTIONS = {
    "add":       _add,
    "update":    _update,
    "list":      _list,
    "search":    _search,
    "deprecate": _deprecate,
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
