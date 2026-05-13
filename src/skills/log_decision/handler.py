#!/usr/bin/env python3
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path






def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_decision(row: sqlite3.Row) -> dict:
    record = dict(row)
    for field in ("alternatives", "babok_context"):
        raw = record.get(field)
        if raw:
            try:
                record[field] = json.loads(raw)
            except (TypeError, ValueError):
                record[field] = []
        else:
            record[field] = []
    return record


def _fetch_decision(conn: sqlite3.Connection, decision_id: str, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM ba_decisions WHERE id = ? AND user_id = ?",
        (decision_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Decision {decision_id!r} not found for user {user_id!r}")
    return _parse_decision(row)


def _add(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    decision = (params.get("decision") or "").strip()
    rationale = (params.get("rationale") or "").strip()
    if not decision:
        raise ValueError("'decision' is required for add")
    if not rationale:
        raise ValueError("'rationale' is required for add")

    decision_id = str(uuid.uuid4())
    now = _now()

    alternatives = params.get("alternatives") or []
    babok_context = params.get("babok_context") or []

    conn.execute(
        """
        INSERT INTO ba_decisions
            (id, user_id, project, decision, rationale, alternatives,
             owner, status, superseded_by, babok_context, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?, ?)
        """,
        (
            decision_id, user_id,
            params.get("project"),
            decision,
            rationale,
            json.dumps(alternatives),
            params.get("owner"),
            json.dumps(babok_context),
            now,
        ),
    )
    conn.commit()
    record = _fetch_decision(conn, decision_id, user_id)
    return {
        "decision_id": decision_id,
        "record": record,
        "message": f"Decision logged: '{decision[:80]}'",
    }


def _list(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    filter_status = (params.get("filter_status") or "active").strip().lower()

    base = "SELECT * FROM ba_decisions WHERE user_id = ?"
    args: list = [user_id]

    if project:
        base += " AND project = ?"
        args.append(project)

    if filter_status == "active":
        base += " AND status = 'active'"
    elif filter_status != "all":
        base += " AND status = ?"
        args.append(filter_status)

    base += " ORDER BY created_at DESC"
    rows = conn.execute(base, args).fetchall()
    decisions = [_parse_decision(r) for r in rows]
    return {"decisions": decisions, "count": len(decisions)}


def _supersede(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    decision_id = (params.get("decision_id") or "").strip()
    new_decision_id = (
        params.get("new_decision_id") or params.get("superseded_by") or ""
    ).strip()
    if not decision_id:
        raise ValueError("'decision_id' is required for supersede")
    if not new_decision_id:
        raise ValueError("'new_decision_id' (or 'superseded_by') is required for supersede")

    _fetch_decision(conn, decision_id, user_id)
    conn.execute(
        "UPDATE ba_decisions SET status = 'superseded', superseded_by = ? WHERE id = ? AND user_id = ?",
        (new_decision_id, decision_id, user_id),
    )
    conn.commit()
    return {"message": f"Decision {decision_id!r} superseded by {new_decision_id!r}."}


def _get(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    decision_id = (params.get("decision_id") or "").strip()
    if not decision_id:
        raise ValueError("'decision_id' is required for get")
    return {"decision": _fetch_decision(conn, decision_id, user_id)}


_ACTIONS = {
    "add":       _add,
    "list":      _list,
    "supersede": _supersede,
    "get":       _get,
}


def execute(params: dict) -> dict:
    action = (params.get("action") or "add").strip().lower()
    user_id = (params.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("'user_id' is required")
    if action not in _ACTIONS:
        raise ValueError(f"Unknown action '{action}'. Valid: {', '.join(_ACTIONS)}")
    conn = _open()
    try:
        return _ACTIONS[action](conn, user_id, params)
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
