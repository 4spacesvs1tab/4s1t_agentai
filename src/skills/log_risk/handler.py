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


_VALID_LEVELS = {"low", "medium", "high"}
_VALID_STATUSES = {"open", "mitigated", "accepted", "closed"}

_SCORE_MATRIX = {
    ("high",   "high"):   "critical",
    ("high",   "medium"): "high",
    ("high",   "low"):    "medium",
    ("medium", "high"):   "high",
    ("medium", "medium"): "medium",
    ("medium", "low"):    "low",
    ("low",    "high"):   "medium",
    ("low",    "medium"): "low",
    ("low",    "low"):    "low",
}


def _compute_risk_score(likelihood: str, impact: str) -> str:
    return _SCORE_MATRIX.get((likelihood, impact), "medium")


def _fetch_risk(conn: sqlite3.Connection, risk_id: str, user_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM ba_risks WHERE id = ? AND user_id = ?",
        (risk_id, user_id),
    ).fetchone()
    if row is None:
        raise ValueError(f"Risk {risk_id!r} not found for user {user_id!r}")
    return dict(row)


def _add(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    title = (params.get("title") or "").strip()
    description = (params.get("description") or "").strip()
    if not title:
        raise ValueError("'title' is required for add")
    if not description:
        raise ValueError("'description' is required for add")

    likelihood = (params.get("likelihood") or "medium").strip().lower()
    if likelihood not in _VALID_LEVELS:
        raise ValueError(f"Invalid likelihood '{likelihood}'. Valid: low, medium, high")

    impact = (params.get("impact") or "medium").strip().lower()
    if impact not in _VALID_LEVELS:
        raise ValueError(f"Invalid impact '{impact}'. Valid: low, medium, high")

    status = (params.get("status") or "open").strip().lower()
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Valid: {', '.join(sorted(_VALID_STATUSES))}")

    risk_score = _compute_risk_score(likelihood, impact)
    risk_id = str(uuid.uuid4())
    now = _now()

    conn.execute(
        """
        INSERT INTO ba_risks
            (id, user_id, project, title, description, likelihood, impact,
             risk_score, mitigation, contingency, owner, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            risk_id, user_id,
            params.get("project"),
            title, description,
            likelihood, impact, risk_score,
            params.get("mitigation"),
            params.get("contingency"),
            params.get("owner"),
            status,
            now, now,
        ),
    )
    conn.commit()
    record = _fetch_risk(conn, risk_id, user_id)
    return {
        "risk_id": risk_id,
        "record": record,
        "message": f"Risk logged: '{title}' — score: {risk_score}",
    }


def _update(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    risk_id = (params.get("risk_id") or "").strip()
    if not risk_id:
        raise ValueError("'risk_id' is required for update")

    current = _fetch_risk(conn, risk_id, user_id)

    updates: list[str] = []
    values: list = []

    for col in ("title", "description", "mitigation", "contingency", "owner", "project"):
        if col in params and params[col] is not None:
            updates.append(f"{col} = ?")
            values.append(params[col])

    likelihood = current["likelihood"]
    impact = current["impact"]

    if "likelihood" in params and params["likelihood"] is not None:
        likelihood = params["likelihood"].strip().lower()
        if likelihood not in _VALID_LEVELS:
            raise ValueError(f"Invalid likelihood '{likelihood}'. Valid: low, medium, high")
        updates.append("likelihood = ?")
        values.append(likelihood)

    if "impact" in params and params["impact"] is not None:
        impact = params["impact"].strip().lower()
        if impact not in _VALID_LEVELS:
            raise ValueError(f"Invalid impact '{impact}'. Valid: low, medium, high")
        updates.append("impact = ?")
        values.append(impact)

    if "status" in params and params["status"] is not None:
        status = params["status"].strip().lower()
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Valid: {', '.join(sorted(_VALID_STATUSES))}")
        updates.append("status = ?")
        values.append(status)

    new_score = _compute_risk_score(likelihood, impact)
    updates.append("risk_score = ?")
    values.append(new_score)

    if not updates:
        raise ValueError("No fields provided to update")

    updates.append("updated_at = ?")
    values.append(_now())
    values.extend([risk_id, user_id])

    conn.execute(
        f"UPDATE ba_risks SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
        values,
    )
    conn.commit()
    record = _fetch_risk(conn, risk_id, user_id)
    return {"risk_id": risk_id, "record": record, "message": "Risk updated."}


def _list(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    filter_status = (params.get("filter_status") or "active").strip().lower()

    base = "SELECT * FROM ba_risks WHERE user_id = ?"
    args: list = [user_id]

    if project:
        base += " AND project = ?"
        args.append(project)

    if filter_status == "active":
        base += " AND status IN ('open', 'mitigated')"
    elif filter_status in _VALID_STATUSES:
        base += " AND status = ?"
        args.append(filter_status)

    base += " ORDER BY created_at DESC"
    rows = conn.execute(base, args).fetchall()
    risks = [dict(r) for r in rows]

    open_count = sum(1 for r in risks if r["status"] == "open")
    critical_count = sum(1 for r in risks if r["risk_score"] == "critical")

    return {
        "risks": risks,
        "open_count": open_count,
        "critical_count": critical_count,
        "message": f"{len(risks)} risk(s) found — {open_count} open, {critical_count} critical.",
    }


def _close(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    risk_id = (params.get("risk_id") or "").strip()
    if not risk_id:
        raise ValueError("'risk_id' is required for close")

    _fetch_risk(conn, risk_id, user_id)
    now = _now()
    conn.execute(
        "UPDATE ba_risks SET status = 'closed', updated_at = ? WHERE id = ? AND user_id = ?",
        (now, risk_id, user_id),
    )
    conn.commit()
    return {"risk_id": risk_id, "message": f"Risk {risk_id!r} closed."}


_ACTIONS = {
    "add":    _add,
    "update": _update,
    "list":   _list,
    "close":  _close,
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
