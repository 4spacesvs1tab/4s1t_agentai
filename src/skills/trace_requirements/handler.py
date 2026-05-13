#!/usr/bin/env python3
"""
trace_requirements skill handler.

Manages requirement traceability links and coverage reports.

Input:  {"parameters": {"action": ..., "user_id": ..., ...}}
Output: {"success": true, "result": {...}}
"""
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


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

def _link(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    source_id = (params.get("source_id") or "").strip()
    target_id = (params.get("target_id") or "").strip()
    link_type = (params.get("link_type") or "").strip()

    if not project:
        raise ValueError("'project' is required for link")
    if not source_id:
        raise ValueError("'source_id' is required for link")
    if not target_id:
        raise ValueError("'target_id' is required for link")
    if not link_type:
        raise ValueError("'link_type' is required for link")

    source_type = (params.get("source_type") or "requirement").strip()
    target_type = (params.get("target_type") or "requirement").strip()

    link_id = str(uuid.uuid4())
    now = _now()

    try:
        conn.execute(
            """
            INSERT INTO ba_requirement_links
                (id, user_id, project, source_id, source_type, target_id, target_type,
                 link_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (link_id, user_id, project, source_id, source_type,
             target_id, target_type, link_type, now),
        )
        conn.commit()
        message = f"Linked {source_id} --[{link_type}]--> {target_id}"
    except sqlite3.IntegrityError:
        # UNIQUE conflict — already linked
        row = conn.execute(
            """
            SELECT id FROM ba_requirement_links
             WHERE source_id = ? AND target_id = ? AND link_type = ?
            """,
            (source_id, target_id, link_type),
        ).fetchone()
        link_id = row["id"] if row else link_id
        message = f"Link already exists between {source_id} and {target_id} ({link_type})"

    return {"link_id": link_id, "message": message}


def _unlink(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    source_id = (params.get("source_id") or "").strip()
    target_id = (params.get("target_id") or "").strip()
    link_type = (params.get("link_type") or "").strip()

    if not source_id:
        raise ValueError("'source_id' is required for unlink")
    if not target_id:
        raise ValueError("'target_id' is required for unlink")
    if not link_type:
        raise ValueError("'link_type' is required for unlink")

    cursor = conn.execute(
        """
        DELETE FROM ba_requirement_links
         WHERE source_id = ? AND target_id = ? AND link_type = ? AND user_id = ?
        """,
        (source_id, target_id, link_type, user_id),
    )
    conn.commit()
    if cursor.rowcount > 0:
        message = f"Removed link {source_id} --[{link_type}]--> {target_id}"
    else:
        message = f"No matching link found for {source_id} --[{link_type}]--> {target_id}"
    return {"message": message}


def _parents(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    source_id = (params.get("source_id") or "").strip()
    if not source_id:
        raise ValueError("'source_id' is required for parents")

    # "parents" = things that source_id derives FROM: rows where target_id = source_id
    rows = conn.execute(
        """
        SELECT source_id, source_type, link_type, target_id, target_type
          FROM ba_requirement_links
         WHERE target_id = ? AND user_id = ?
         ORDER BY created_at ASC
        """,
        (source_id, user_id),
    ).fetchall()

    parents = [dict(r) for r in rows]
    return {"parents": parents, "count": len(parents)}


def _children(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    source_id = (params.get("source_id") or "").strip()
    if not source_id:
        raise ValueError("'source_id' is required for children")

    rows = conn.execute(
        """
        SELECT source_id, source_type, link_type, target_id, target_type
          FROM ba_requirement_links
         WHERE source_id = ? AND user_id = ?
         ORDER BY created_at ASC
        """,
        (source_id, user_id),
    ).fetchall()

    children = [dict(r) for r in rows]
    return {"children": children, "count": len(children)}


def _matrix(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    if not project:
        raise ValueError("'project' is required for matrix")

    rows = conn.execute(
        """
        SELECT source_id, source_type, link_type, target_id, target_type
          FROM ba_requirement_links
         WHERE project = ? AND user_id = ?
         ORDER BY source_id, link_type, target_id
        """,
        (project, user_id),
    ).fetchall()

    links = [dict(r) for r in rows]

    # Build markdown table
    lines = [
        f"# Traceability Matrix: {project}",
        "",
        "| Source | Source Type | Link Type | Target | Target Type |",
        "|--------|-------------|-----------|--------|-------------|",
    ]
    for lnk in links:
        lines.append(
            f"| {lnk['source_id']} | {lnk['source_type']} | {lnk['link_type']}"
            f" | {lnk['target_id']} | {lnk['target_type']} |"
        )

    if not links:
        lines.append("| _(no links defined)_ | | | | |")

    matrix_markdown = "\n".join(lines)
    return {"links": links, "count": len(links), "matrix_markdown": matrix_markdown}


def _coverage(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    if not project:
        raise ValueError("'project' is required for coverage")

    # Total requirements for this user+project
    total_row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM ba_requirements WHERE user_id = ? AND project = ?",
        (user_id, project),
    ).fetchone()
    total = total_row["cnt"] if total_row else 0

    # All requirement IDs for this project
    req_rows = conn.execute(
        "SELECT id FROM ba_requirements WHERE user_id = ? AND project = ?",
        (user_id, project),
    ).fetchall()
    all_ids = {r["id"] for r in req_rows}

    # IDs that appear in at least one link (as source or target)
    linked_rows = conn.execute(
        """
        SELECT DISTINCT source_id AS id FROM ba_requirement_links
         WHERE user_id = ? AND project = ?
        UNION
        SELECT DISTINCT target_id AS id FROM ba_requirement_links
         WHERE user_id = ? AND project = ?
        """,
        (user_id, project, user_id, project),
    ).fetchall()
    linked_ids = {r["id"] for r in linked_rows} & all_ids

    linked = len(linked_ids)
    unlinked_ids = sorted(all_ids - linked_ids)
    unlinked = len(unlinked_ids)

    linked_pct = round(linked / total * 100, 1) if total > 0 else 0.0
    unlinked_pct = round(unlinked / total * 100, 1) if total > 0 else 0.0

    return {
        "total_requirements": total,
        "linked_requirements": linked,
        "unlinked_pct": unlinked_pct,
        "linked_pct": linked_pct,
        "unlinked_ids": unlinked_ids,
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_ACTIONS = {
    "link":     _link,
    "unlink":   _unlink,
    "parents":  _parents,
    "children": _children,
    "matrix":   _matrix,
    "coverage": _coverage,
}


def execute(params: dict) -> dict:
    action = (params.get("action") or "matrix").strip().lower()
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
        result = execute(data.get("parameters", {}))
        output = {"success": True, "result": result, "error": None, "logs": []}
    except Exception as exc:
        output = {"success": False, "result": None, "error": str(exc), "logs": []}
    with open(output_path, "w") as f:
        json.dump(output, f)


if __name__ == "__main__":
    main()
