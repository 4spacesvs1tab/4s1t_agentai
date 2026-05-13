#!/usr/bin/env python3
"""
assess_change_impact skill handler.

BFS through the requirement link graph to enumerate all artefacts affected by
a proposed change, then surface related decisions, open risks and business rules.

Input:  {"parameters": {"action": "assess", "user_id": ..., ...}}
Output: {"success": true, "result": {...}}
"""
import json
import os
import sqlite3
import sys
from collections import deque
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
# Action handler
# ---------------------------------------------------------------------------

def _assess(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    requirement_id = (params.get("requirement_id") or "").strip()
    project = (params.get("project") or "").strip()
    proposed_change = (params.get("proposed_change") or "").strip()

    if not requirement_id:
        raise ValueError("'requirement_id' is required for assess")
    if not project:
        raise ValueError("'project' is required for assess")
    if not proposed_change:
        raise ValueError("'proposed_change' is required for assess")

    depth = int(params.get("depth") or 2)
    if depth < 1:
        depth = 1
    if depth > 10:
        depth = 10

    # 1. Get changed requirement title
    req_row = conn.execute(
        "SELECT title, status FROM ba_requirements WHERE id = ? AND user_id = ?",
        (requirement_id, user_id),
    ).fetchone()
    requirement_title = req_row["title"] if req_row else requirement_id

    # 2. BFS through ba_requirement_links (source_id → target_id edges)
    visited: dict[str, str] = {}  # id → link_type used to reach it
    queue: deque = deque()

    # Seed with direct neighbours
    seed_rows = conn.execute(
        """
        SELECT target_id, link_type FROM ba_requirement_links
         WHERE source_id = ? AND user_id = ?
        """,
        (requirement_id, user_id),
    ).fetchall()
    for row in seed_rows:
        tid = row["target_id"]
        if tid != requirement_id and tid not in visited:
            visited[tid] = row["link_type"]
            if depth > 1:
                queue.append((tid, 1))

    current_depth = 1
    while queue and current_depth < depth:
        node, node_depth = queue.popleft()
        if node_depth >= depth:
            continue
        next_rows = conn.execute(
            """
            SELECT target_id, link_type FROM ba_requirement_links
             WHERE source_id = ? AND user_id = ?
            """,
            (node, user_id),
        ).fetchall()
        for row in next_rows:
            tid = row["target_id"]
            if tid != requirement_id and tid not in visited:
                visited[tid] = row["link_type"]
                queue.append((tid, node_depth + 1))

    # 3. Fetch titles/status for affected reqs
    affected_requirements = []
    for req_id, link_type in visited.items():
        ar_row = conn.execute(
            "SELECT title, status FROM ba_requirements WHERE id = ? AND user_id = ?",
            (req_id, user_id),
        ).fetchone()
        affected_requirements.append({
            "id": req_id,
            "title": ar_row["title"] if ar_row else req_id,
            "status": ar_row["status"] if ar_row else "unknown",
            "link_type": link_type,
        })

    # 4. Find related decisions (babok_context contains req ID OR decision text LIKE title)
    decision_rows = conn.execute(
        """
        SELECT id, decision FROM ba_decisions
         WHERE user_id = ? AND project = ?
           AND (babok_context LIKE ? OR decision LIKE ?)
        """,
        (user_id, project, f"%{requirement_id}%", f"%{requirement_title}%"),
    ).fetchall()
    affected_decisions = [{"id": r["id"], "decision": r["decision"]} for r in decision_rows]

    # 5. Open risks for this project
    risk_rows = conn.execute(
        """
        SELECT id, title, status, risk_score FROM ba_risks
         WHERE user_id = ? AND project = ? AND status = 'open'
         ORDER BY created_at DESC
        """,
        (user_id, project),
    ).fetchall()
    related_risks = [
        {"id": r["id"], "title": r["title"], "status": r["status"], "risk_score": r["risk_score"]}
        for r in risk_rows
    ]

    # 6. Business rules referencing this requirement
    rule_rows = conn.execute(
        """
        SELECT id, rule_name FROM ba_business_rules
         WHERE user_id = ? AND source_requirement_id = ?
        """,
        (user_id, requirement_id),
    ).fetchall()
    affected_rules = [{"id": r["id"], "rule_name": r["rule_name"]} for r in rule_rows]

    # 7. Compute impact_score
    n = len(affected_requirements)
    if n > 5:
        impact_score = "critical"
    elif n > 2:
        impact_score = "high"
    elif n > 0:
        impact_score = "medium"
    else:
        impact_score = "low"

    # 8. Build markdown report
    lines = [
        f"# Change Impact Analysis",
        f"",
        f"**Project**: {project}  ",
        f"**Requirement**: {requirement_id} — {requirement_title}  ",
        f"**Proposed Change**: {proposed_change}  ",
        f"**Impact Score**: {impact_score.upper()}",
        f"",
        f"## Affected Requirements ({len(affected_requirements)})",
    ]
    if affected_requirements:
        lines += [
            "| ID | Title | Status | Relation |",
            "|----|-------|--------|----------|",
        ]
        for ar in affected_requirements:
            lines.append(f"| {ar['id']} | {ar['title']} | {ar['status']} | {ar['link_type']} |")
    else:
        lines.append("_No linked requirements affected._")

    lines += [
        "",
        f"## Related Decisions ({len(affected_decisions)})",
    ]
    if affected_decisions:
        for d in affected_decisions:
            lines.append(f"- [{d['id']}] {d['decision'][:120]}")
    else:
        lines.append("_No decisions reference this requirement._")

    lines += [
        "",
        f"## Open Risks ({len(related_risks)})",
    ]
    if related_risks:
        for r in related_risks:
            lines.append(f"- [{r['id']}] **{r['risk_score'].upper()}** — {r['title']}")
    else:
        lines.append("_No open risks for this project._")

    lines += [
        "",
        f"## Affected Business Rules ({len(affected_rules)})",
    ]
    if affected_rules:
        for br in affected_rules:
            lines.append(f"- [{br['id']}] {br['rule_name']}")
    else:
        lines.append("_No business rules reference this requirement._")

    lines += [
        "",
        "---",
        f"_Report generated {_now()}_",
    ]

    return {
        "requirement_id": requirement_id,
        "requirement_title": requirement_title,
        "proposed_change": proposed_change,
        "impact_score": impact_score,
        "affected_requirements": affected_requirements,
        "affected_decisions": affected_decisions,
        "related_risks": related_risks,
        "affected_rules": affected_rules,
        "impact_report_markdown": "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_ACTIONS = {
    "assess": _assess,
}


def execute(params: dict) -> dict:
    action = (params.get("action") or "assess").strip().lower()
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
