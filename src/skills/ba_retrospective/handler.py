#!/usr/bin/env python3
"""
ba_retrospective skill handler — KB-14.

Aggregates all ba_* tables for a project and produces a retrospective report.

Input:  {"parameters": {"user_id": "...", "project": "...", "period": null}}
Output: {"success": true, "result": {"overall_health": "Green", "retrospective_markdown": "...", ...}}
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

from core.db_path import get_db_path






def _open() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _parse_period(period: str | None) -> tuple[str | None, str | None]:
    if not period:
        return None, None
    parts = period.split("/", 1)
    start = parts[0].strip() if parts else None
    end = parts[1].strip() if len(parts) > 1 else None
    return start, end


def _period_filter(start: str | None, end: str | None, col: str = "created_at") -> tuple[str, list]:
    clauses, vals = [], []
    if start:
        clauses.append(f"{col} >= ?")
        vals.append(start)
    if end:
        clauses.append(f"{col} <= ?")
        vals.append(end)
    return (" AND " + " AND ".join(clauses)) if clauses else "", vals


def _count_by(rows, field: str) -> dict:
    counts: dict = {}
    for r in rows:
        val = r[field] or "unknown"
        counts[val] = counts.get(val, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _health_icon(h: str) -> str:
    return {"Green": "🟢", "Amber": "🟡", "Red": "🔴"}.get(h, "⚪")


def _build_markdown(project: str, period: str | None,
                    project_meta: dict,
                    dec: dict, req: dict, risk: dict,
                    stake: dict, kpi: dict,
                    open_items: list, overall_health: str) -> str:
    lines = [
        f"# BA Retrospective: {project}",
        "",
        f"**Overall Health:** {_health_icon(overall_health)} {overall_health}  ",
    ]
    if period:
        lines.append(f"**Period:** {period}  ")
    if project_meta:
        lines += [
            f"**Methodology:** {project_meta.get('methodology', '—')}  ",
            f"**Status:** {project_meta.get('status', '—')}  ",
            f"**Sponsor:** {project_meta.get('sponsor') or '—'}  ",
        ]
    lines.append("")

    # Decisions
    lines += [
        "## Decisions",
        "",
        f"- Total: **{dec['total']}** (active: {dec['active']}, "
        f"superseded: {dec['superseded']}, reverted: {dec['reverted']})",
        "",
    ]

    # Requirements
    by_type = req.get("by_type", {})
    by_status = req.get("by_status", {})
    lines += [
        "## Requirements",
        "",
        f"- Total: **{req['total']}**",
        f"- By status: draft={by_status.get('draft', 0)}, "
        f"approved={by_status.get('approved', 0)}, "
        f"implemented={by_status.get('implemented', 0)}, "
        f"deferred={by_status.get('deferred', 0)}",
        f"- By type: functional={by_type.get('functional', 0)}, "
        f"non-functional={by_type.get('non_functional', 0)}, "
        f"constraints={by_type.get('constraint', 0)}, "
        f"assumptions={by_type.get('assumption', 0)}",
        "",
    ]

    # Risks
    lines += [
        "## Risks",
        "",
        f"- Total: **{risk['total']}** (open: {risk['open']}, "
        f"mitigated: {risk['mitigated']}, accepted: {risk['accepted']}, "
        f"closed: {risk['closed']})",
    ]
    if risk.get("critical_open", 0):
        lines.append(f"- ⚠️ **{risk['critical_open']} critical risk(s) still open**")
    lines.append("")

    # Stakeholders
    by_pos = stake.get("by_position", {})
    lines += [
        "## Stakeholders",
        "",
        f"- Total: **{stake['total']}** "
        f"(champions: {by_pos.get('champion', 0)}, "
        f"supporters: {by_pos.get('supporter', 0)}, "
        f"neutral: {by_pos.get('neutral', 0)}, "
        f"blockers: {by_pos.get('blocker', 0)})",
        "",
    ]

    # KPIs
    lines += [
        "## KPIs",
        "",
        f"- Defined: **{kpi['defined']}** ({kpi['active']} active)",
        "",
    ]

    # Open items
    if open_items:
        lines += ["## Open Items", ""]
        for item in open_items:
            severity = f" [{item.get('severity', '')}]" if item.get("severity") else ""
            lines.append(f"- **{item['type'].upper()}**{severity}: {item['title']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

def _generate(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    if not project:
        raise ValueError("'project' is required")
    period = params.get("period") or None
    start, end = _parse_period(period)
    pf, pv = _period_filter(start, end)

    base = [user_id, project]

    # Project metadata
    proj_row = conn.execute(
        "SELECT methodology, status, sponsor FROM ba_projects WHERE user_id = ? AND name = ?",
        (user_id, project),
    ).fetchone()
    project_meta = dict(proj_row) if proj_row else {}

    # Decisions
    dec_rows = conn.execute(
        f"SELECT status FROM ba_decisions WHERE user_id = ? AND project = ?{pf}",
        base + pv,
    ).fetchall()
    dec_by_status = _count_by(dec_rows, "status")
    decisions = {
        "total": len(dec_rows),
        "active": dec_by_status.get("active", 0),
        "superseded": dec_by_status.get("superseded", 0),
        "reverted": dec_by_status.get("reverted", 0),
    }

    # Requirements
    req_rows = conn.execute(
        f"SELECT req_type, status FROM ba_requirements WHERE user_id = ? AND project = ?{pf}",
        base + pv,
    ).fetchall()
    requirements = {
        "total": len(req_rows),
        "by_status": _count_by(req_rows, "status"),
        "by_type": _count_by(req_rows, "req_type"),
    }

    # Risks
    risk_rows = conn.execute(
        f"SELECT status, risk_score FROM ba_risks WHERE user_id = ? AND project = ?{pf}",
        base + pv,
    ).fetchall()
    risk_by_status = _count_by(risk_rows, "status")
    critical_open = sum(
        1 for r in risk_rows
        if r["risk_score"] == "critical" and r["status"] == "open"
    )
    risks = {
        "total": len(risk_rows),
        "open": risk_by_status.get("open", 0),
        "mitigated": risk_by_status.get("mitigated", 0),
        "accepted": risk_by_status.get("accepted", 0),
        "closed": risk_by_status.get("closed", 0),
        "critical_open": critical_open,
    }

    # Stakeholders
    stake_rows = conn.execute(
        "SELECT position FROM ba_stakeholders WHERE user_id = ? AND project = ?",
        (user_id, project),
    ).fetchall()
    stakeholders = {
        "total": len(stake_rows),
        "by_position": _count_by(stake_rows, "position"),
    }

    # KPIs
    kpi_rows = conn.execute(
        "SELECT status FROM ba_kpis WHERE user_id = ? AND project = ?",
        (user_id, project),
    ).fetchall()
    kpi_by_status = _count_by(kpi_rows, "status")
    kpis = {
        "defined": len(kpi_rows),
        "active": kpi_by_status.get("active", 0),
    }

    # Open items list
    open_items = []
    for r in conn.execute(
        "SELECT id, title, risk_score FROM ba_risks "
        "WHERE user_id = ? AND project = ? AND status IN ('open', 'mitigated')"
        "ORDER BY risk_score DESC LIMIT 10",
        (user_id, project),
    ).fetchall():
        open_items.append({"type": "risk", "id": r["id"], "title": r["title"],
                           "severity": r["risk_score"]})

    for r in conn.execute(
        "SELECT id, title, req_type FROM ba_requirements "
        "WHERE user_id = ? AND project = ? AND status = 'draft' LIMIT 10",
        (user_id, project),
    ).fetchall():
        open_items.append({"type": "requirement", "id": r["id"], "title": r["title"],
                           "severity": r["req_type"]})

    # Overall health
    total_reqs = requirements["total"]
    draft_count = requirements["by_status"].get("draft", 0)
    draft_ratio = draft_count / total_reqs if total_reqs else 0

    if critical_open > 0 or draft_ratio > 0.5:
        overall_health = "Red"
    elif risks["open"] > 0 or draft_ratio > 0.25:
        overall_health = "Amber"
    else:
        overall_health = "Green"

    markdown = _build_markdown(
        project, period, project_meta,
        decisions, requirements, risks, stakeholders, kpis,
        open_items, overall_health,
    )

    return {
        "project": project,
        "period": period,
        "decisions": decisions,
        "requirements": requirements,
        "risks": risks,
        "stakeholders": stakeholders,
        "kpis": kpis,
        "open_items": open_items,
        "overall_health": overall_health,
        "retrospective_markdown": markdown,
    }


_ACTIONS = {"generate": _generate}


def execute(params: dict) -> dict:
    action = (params.get("action") or "generate").strip().lower()
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


# ---------------------------------------------------------------------------
# Subprocess entry point
# ---------------------------------------------------------------------------

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
