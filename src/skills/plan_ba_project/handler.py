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


_VALID_METHODOLOGIES = {"agile", "waterfall", "hybrid", "kanban"}
_VALID_STATUSES = {"active", "on_hold", "completed", "cancelled"}


def _fetch_project(conn: sqlite3.Connection, project_id: str, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM ba_projects WHERE id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Project {project_id!r} not found for user {user_id!r}")
    return dict(row)


def _create(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    name = (params.get("name") or "").strip()
    if not name:
        raise ValueError("'name' is required for create")

    methodology = (params.get("methodology") or "agile").strip().lower()
    if methodology not in _VALID_METHODOLOGIES:
        raise ValueError(
            f"Invalid methodology '{methodology}'. Valid: {', '.join(sorted(_VALID_METHODOLOGIES))}"
        )

    status = (params.get("status") or "active").strip().lower()
    if status not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Valid: {', '.join(sorted(_VALID_STATUSES))}"
        )

    project_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        """
        INSERT INTO ba_projects
            (id, user_id, name, description, business_problem, methodology,
             sponsor, status, target_completion, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            project_id, user_id, name,
            params.get("description"),
            params.get("business_problem"),
            methodology,
            params.get("sponsor"),
            status,
            params.get("target_completion"),
            now, now,
        ),
    )
    conn.commit()
    project = _fetch_project(conn, project_id, user_id)
    return {
        "project_id": project_id,
        "project": project,
        "message": f"BA project created: '{name}'",
    }


def _update(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project_id = (params.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("'project_id' is required for update")

    _fetch_project(conn, project_id, user_id)

    updates: list[str] = []
    values: list = []

    for col in ("name", "description", "business_problem", "sponsor", "target_completion"):
        if col in params and params[col] is not None:
            updates.append(f"{col} = ?")
            values.append(params[col])

    if "methodology" in params and params["methodology"] is not None:
        methodology = params["methodology"].strip().lower()
        if methodology not in _VALID_METHODOLOGIES:
            raise ValueError(
                f"Invalid methodology '{methodology}'. Valid: {', '.join(sorted(_VALID_METHODOLOGIES))}"
            )
        updates.append("methodology = ?")
        values.append(methodology)

    if "status" in params and params["status"] is not None:
        status = params["status"].strip().lower()
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Valid: {', '.join(sorted(_VALID_STATUSES))}"
            )
        updates.append("status = ?")
        values.append(status)

    if not updates:
        raise ValueError("No fields provided to update")

    updates.append("updated_at = ?")
    values.append(_now())
    values.extend([project_id, user_id])

    conn.execute(
        f"UPDATE ba_projects SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    conn.commit()
    project = _fetch_project(conn, project_id, user_id)
    return {"project_id": project_id, "project": project, "message": "Project updated."}


def _list(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    rows = conn.execute(
        "SELECT * FROM ba_projects WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    projects = [dict(r) for r in rows]
    return {"projects": projects, "count": len(projects)}


def _get(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project_id = (params.get("project_id") or "").strip()
    name = (params.get("name") or "").strip()

    if project_id:
        row = conn.execute(
            "SELECT * FROM ba_projects WHERE id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
    elif name:
        row = conn.execute(
            "SELECT * FROM ba_projects WHERE name = ? AND user_id = ?",
            (name, user_id),
        ).fetchone()
    else:
        raise ValueError("'project_id' or 'name' is required for get")

    if row is None:
        raise ValueError("Project not found")
    return {"project": dict(row)}


_ACTIONS = {
    "create": _create,
    "update": _update,
    "list":   _list,
    "get":    _get,
}


def execute(params: dict) -> dict:
    action = (params.get("action") or "create").strip().lower()
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
