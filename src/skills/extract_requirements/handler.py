#!/usr/bin/env python3
"""
extract_requirements skill handler.

Persist and manage structured requirements in ba_requirements.
The calling LLM performs the extraction; this skill stores and manages results.

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
CREATE TABLE IF NOT EXISTS ba_requirements (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    project              TEXT,
    req_type             TEXT NOT NULL,
    title                TEXT NOT NULL,
    description          TEXT NOT NULL,
    acceptance_criteria  TEXT,
    priority             TEXT NOT NULL DEFAULT 'medium',
    status               TEXT NOT NULL DEFAULT 'draft',
    source               TEXT,
    approved_by          TEXT,
    approved_at          TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
"""

_VALID_REQ_TYPES = {"functional", "non_functional", "constraint", "assumption"}
_VALID_STATUSES  = {"draft", "approved", "implemented", "deferred"}
_VALID_PRIORITIES = {"low", "medium", "high", "must_have"}


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_req(conn: sqlite3.Connection, req_id: str, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM ba_requirements WHERE id = ? AND user_id = ?",
        (req_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Requirement {req_id!r} not found for user {user_id!r}")
    return _deserialize(dict(row))


def _deserialize(record: dict) -> dict:
    """Parse acceptance_criteria JSON back to list."""
    raw = record.get("acceptance_criteria")
    if raw:
        try:
            record["acceptance_criteria"] = json.loads(raw)
        except (TypeError, ValueError):
            record["acceptance_criteria"] = []
    else:
        record["acceptance_criteria"] = []
    return record


def _row_list(rows) -> list:
    return [_deserialize(dict(r)) for r in rows]


def _serialize_ac(acceptance_criteria) -> str | None:
    """Encode acceptance_criteria to JSON string for storage."""
    if acceptance_criteria is None:
        return None
    if isinstance(acceptance_criteria, list):
        return json.dumps(acceptance_criteria)
    return acceptance_criteria  # already a string (passthrough)


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _add(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    requirements = params.get("requirements")
    if not requirements or not isinstance(requirements, list):
        raise ValueError("'requirements' must be a non-empty list for add")

    project = params.get("project") or None
    now = _now()
    inserted = []

    for idx, req in enumerate(requirements):
        title       = (req.get("title") or "").strip()
        description = (req.get("description") or "").strip()
        req_type    = (req.get("req_type") or "").strip().lower()

        if not title:
            raise ValueError(f"requirements[{idx}]: 'title' is required")
        if not description:
            raise ValueError(f"requirements[{idx}]: 'description' is required")
        if not req_type:
            raise ValueError(f"requirements[{idx}]: 'req_type' is required")
        if req_type not in _VALID_REQ_TYPES:
            raise ValueError(
                f"requirements[{idx}]: invalid req_type '{req_type}'. "
                f"Must be one of: {', '.join(sorted(_VALID_REQ_TYPES))}"
            )

        priority = (req.get("priority") or "medium").lower()
        if priority not in _VALID_PRIORITIES:
            priority = "medium"

        req_id = str(uuid.uuid4())
        ac_json = _serialize_ac(req.get("acceptance_criteria"))

        conn.execute(
            """
            INSERT INTO ba_requirements
                (id, user_id, project, req_type, title, description,
                 acceptance_criteria, priority, status, source,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
            """,
            (
                req_id, user_id, project, req_type, title, description,
                ac_json, priority,
                req.get("source"),
                now, now,
            ),
        )
        inserted.append({"req_id": req_id, "title": title, "req_type": req_type})

    conn.commit()
    return {
        "inserted": inserted,
        "count": len(inserted),
        "message": f"{len(inserted)} requirement(s) added.",
    }


def _list(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project  = params.get("project")  or None
    req_type = params.get("req_type") or None
    status   = params.get("status")   or None

    if req_type and req_type.lower() not in _VALID_REQ_TYPES:
        raise ValueError(
            f"Invalid req_type '{req_type}'. Must be one of: {', '.join(sorted(_VALID_REQ_TYPES))}"
        )
    if status and status.lower() not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}"
        )

    clauses = ["user_id = ?"]
    args: list = [user_id]

    if project:
        clauses.append("project = ?")
        args.append(project)
    if req_type:
        clauses.append("req_type = ?")
        args.append(req_type.lower())
    if status:
        clauses.append("status = ?")
        args.append(status.lower())

    sql = (
        f"SELECT * FROM ba_requirements WHERE {' AND '.join(clauses)} "
        "ORDER BY req_type ASC, priority DESC, title ASC"
    )
    rows = conn.execute(sql, args).fetchall()
    requirements = _row_list(rows)
    return {"requirements": requirements, "count": len(requirements)}


def _update(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    req_id = (params.get("req_id") or "").strip()
    if not req_id:
        raise ValueError("'req_id' is required for update")

    _fetch_req(conn, req_id, user_id)  # ownership check

    updatable = [
        ("title",       "title"),
        ("description", "description"),
        ("source",      "source"),
    ]
    updates: list[str] = []
    values: list = []

    for col, key in updatable:
        if key in params and params[key] is not None:
            updates.append(f"{col} = ?")
            values.append(params[key])

    if "req_type" in params and params["req_type"] is not None:
        rt = params["req_type"].lower()
        if rt not in _VALID_REQ_TYPES:
            raise ValueError(
                f"Invalid req_type '{rt}'. Must be one of: {', '.join(sorted(_VALID_REQ_TYPES))}"
            )
        updates.append("req_type = ?")
        values.append(rt)

    if "priority" in params and params["priority"] is not None:
        p = params["priority"].lower()
        if p not in _VALID_PRIORITIES:
            raise ValueError(
                f"Invalid priority '{p}'. Must be one of: {', '.join(sorted(_VALID_PRIORITIES))}"
            )
        updates.append("priority = ?")
        values.append(p)

    if "status" in params and params["status"] is not None:
        s = params["status"].lower()
        if s not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{s}'. Must be one of: {', '.join(sorted(_VALID_STATUSES))}"
            )
        updates.append("status = ?")
        values.append(s)

    if "acceptance_criteria" in params and params["acceptance_criteria"] is not None:
        updates.append("acceptance_criteria = ?")
        values.append(_serialize_ac(params["acceptance_criteria"]))

    if not updates:
        raise ValueError("No fields provided to update")

    updates.append("updated_at = ?")
    values.append(_now())
    values.extend([req_id, user_id])

    conn.execute(
        f"UPDATE ba_requirements SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    conn.commit()
    record = _fetch_req(conn, req_id, user_id)
    return {
        "req_id": req_id,
        "record": record,
        "message": f"Requirement '{record['title']}' updated.",
    }


def _approve(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    req_id   = (params.get("req_id")   or "").strip()
    approver = (params.get("approver") or "").strip()
    if not req_id:
        raise ValueError("'req_id' is required for approve")
    if not approver:
        raise ValueError("'approver' is required for approve")

    record = _fetch_req(conn, req_id, user_id)
    now = _now()
    conn.execute(
        """
        UPDATE ba_requirements
           SET status = 'approved', approved_by = ?, approved_at = ?, updated_at = ?
         WHERE id = ? AND user_id = ?
        """,
        (approver, now, now, req_id, user_id),
    )
    conn.commit()
    return {
        "req_id": req_id,
        "message": f"Requirement '{record['title']}' approved by {approver}.",
    }


def _get(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    req_id = (params.get("req_id") or "").strip()
    if not req_id:
        raise ValueError("'req_id' is required for get")
    record = _fetch_req(conn, req_id, user_id)
    return {"requirement": record}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_ACTIONS = {
    "add":     _add,
    "list":    _list,
    "update":  _update,
    "approve": _approve,
    "get":     _get,
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
