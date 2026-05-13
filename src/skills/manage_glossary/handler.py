#!/usr/bin/env python3
"""
manage_glossary skill handler.

CRUD + search for ba_glossary.

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
CREATE TABLE IF NOT EXISTS ba_glossary (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    project     TEXT,
    term        TEXT NOT NULL,
    definition  TEXT NOT NULL,
    synonyms    TEXT,
    data_type   TEXT,
    source      TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(user_id, project, term)
);
"""


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_term(conn: sqlite3.Connection, term_id: str, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM ba_glossary WHERE id = ? AND user_id = ?",
        (term_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Term {term_id!r} not found for user {user_id!r}")
    return _deserialize(dict(row))


def _deserialize(record: dict) -> dict:
    """Parse synonyms JSON array back to list."""
    raw = record.get("synonyms")
    if raw:
        try:
            record["synonyms"] = json.loads(raw)
        except (TypeError, ValueError):
            record["synonyms"] = []
    else:
        record["synonyms"] = []
    return record


def _row_list(rows) -> list:
    return [_deserialize(dict(r)) for r in rows]


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _add(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    term = (params.get("term") or "").strip()
    definition = (params.get("definition") or "").strip()
    if not term:
        raise ValueError("'term' is required for add")
    if not definition:
        raise ValueError("'definition' is required for add")

    project = params.get("project") or None  # NULL = global
    synonyms_raw = params.get("synonyms")
    synonyms_json = json.dumps(synonyms_raw) if isinstance(synonyms_raw, list) else None

    term_id = str(uuid.uuid4())
    now = _now()

    try:
        conn.execute(
            """
            INSERT INTO ba_glossary
                (id, user_id, project, term, definition, synonyms,
                 data_type, source, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                term_id, user_id, project, term, definition, synonyms_json,
                params.get("data_type"),
                params.get("source"),
                params.get("notes"),
                now, now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError(
            f"Term '{term}' already exists in project '{project}'. Use action='update'."
        )

    record = _fetch_term(conn, term_id, user_id)
    return {
        "term_id": term_id,
        "record": record,
        "message": f"Term '{term}' added.",
    }


def _update(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    term_id = (params.get("term_id") or "").strip()
    if not term_id:
        raise ValueError("'term_id' is required for update")

    _fetch_term(conn, term_id, user_id)  # ownership check

    updates: list[str] = []
    values: list = []

    for col, key in [
        ("definition", "definition"),
        ("data_type",  "data_type"),
        ("source",     "source"),
        ("notes",      "notes"),
    ]:
        if key in params and params[key] is not None:
            updates.append(f"{col} = ?")
            values.append(params[key])

    if "synonyms" in params and params["synonyms"] is not None:
        updates.append("synonyms = ?")
        raw = params["synonyms"]
        values.append(json.dumps(raw) if isinstance(raw, list) else raw)

    if not updates:
        raise ValueError("No fields provided to update")

    updates.append("updated_at = ?")
    values.append(_now())
    values.extend([term_id, user_id])

    conn.execute(
        f"UPDATE ba_glossary SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    conn.commit()
    record = _fetch_term(conn, term_id, user_id)
    return {
        "term_id": term_id,
        "record": record,
        "message": f"Term '{record['term']}' updated.",
    }


def _remove(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    term_id = (params.get("term_id") or "").strip()
    if not term_id:
        raise ValueError("'term_id' is required for remove")

    record = _fetch_term(conn, term_id, user_id)
    conn.execute(
        "DELETE FROM ba_glossary WHERE id = ? AND user_id = ?",
        (term_id, user_id),
    )
    conn.commit()
    return {"message": f"Term '{record['term']}' removed."}


def _list(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = params.get("project") or None

    if project:
        rows = conn.execute(
            "SELECT * FROM ba_glossary WHERE user_id = ? AND project = ? ORDER BY term ASC",
            (user_id, project),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ba_glossary WHERE user_id = ? ORDER BY project ASC, term ASC",
            (user_id,),
        ).fetchall()

    terms = _row_list(rows)
    return {"terms": terms, "count": len(terms)}


def _search(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    query = (params.get("query") or "").strip()
    if not query:
        raise ValueError("'query' is required for search")

    project = params.get("project") or None
    like = f"%{query}%"

    if project:
        rows = conn.execute(
            """
            SELECT * FROM ba_glossary
            WHERE user_id = ? AND project = ?
              AND (term LIKE ? OR definition LIKE ? OR synonyms LIKE ?)
            ORDER BY term ASC
            """,
            (user_id, project, like, like, like),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM ba_glossary
            WHERE user_id = ?
              AND (term LIKE ? OR definition LIKE ? OR synonyms LIKE ?)
            ORDER BY term ASC
            """,
            (user_id, like, like, like),
        ).fetchall()

    matches = _row_list(rows)
    return {"matches": matches, "count": len(matches)}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_ACTIONS = {
    "add":    _add,
    "update": _update,
    "remove": _remove,
    "list":   _list,
    "search": _search,
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
