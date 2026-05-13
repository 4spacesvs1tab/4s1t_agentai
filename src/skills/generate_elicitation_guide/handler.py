#!/usr/bin/env python3
"""
generate_elicitation_guide skill handler — KB-14.

Reads existing project data from the DB and returns a structured elicitation
guide template with gap inventory.  The calling agent fills in the actual
questions using the returned context.

Input:  {"parameters": {"action": "generate", "user_id": "...", "project": "...", ...}}
Output: {"success": true, "result": {"guide_markdown": "...", "context_used": {...}, ...}}
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
# DB reads
# ---------------------------------------------------------------------------

def _load_context(conn: sqlite3.Connection, user_id: str, project: str) -> dict:
    # Requirements by type and status
    req_rows = conn.execute(
        "SELECT req_type, status FROM ba_requirements WHERE user_id = ? AND project = ?",
        (user_id, project),
    ).fetchall()
    by_type: dict = {"functional": 0, "non_functional": 0, "constraint": 0, "assumption": 0}
    by_status: dict = {"draft": 0, "approved": 0, "implemented": 0, "deferred": 0}
    draft_titles: list = []
    for r in req_rows:
        rt = r["req_type"]
        st = r["status"]
        by_type[rt] = by_type.get(rt, 0) + 1
        by_status[st] = by_status.get(st, 0) + 1

    draft_rows = conn.execute(
        "SELECT title, req_type FROM ba_requirements "
        "WHERE user_id = ? AND project = ? AND status = 'draft' LIMIT 10",
        (user_id, project),
    ).fetchall()
    draft_titles = [f"{r['title']} ({r['req_type']})" for r in draft_rows]

    # Stakeholders
    stake_rows = conn.execute(
        "SELECT name, role, position FROM ba_stakeholders WHERE user_id = ? AND project = ?",
        (user_id, project),
    ).fetchall()
    stakeholders = [dict(r) for r in stake_rows]
    suggested = [
        f"{r['name']}{' (' + r['role'] + ')' if r['role'] else ''}"
        for r in stake_rows
        if r["position"] in ("champion", "supporter")
    ]

    # Decisions and risks
    decision_count = conn.execute(
        "SELECT COUNT(*) FROM ba_decisions WHERE user_id = ? AND project = ? AND status = 'active'",
        (user_id, project),
    ).fetchone()[0]

    open_risks = conn.execute(
        "SELECT title, risk_score FROM ba_risks "
        "WHERE user_id = ? AND project = ? AND status IN ('open','mitigated')",
        (user_id, project),
    ).fetchall()

    return {
        "requirement_count": len(req_rows),
        "req_by_type": by_type,
        "req_by_status": by_status,
        "draft_requirements": draft_titles,
        "stakeholder_count": len(stakeholders),
        "stakeholders": stakeholders,
        "suggested_participants": suggested,
        "decision_count": decision_count,
        "open_risk_count": len(open_risks),
        "open_risks": [{"title": r["title"], "score": r["risk_score"]} for r in open_risks],
    }


# ---------------------------------------------------------------------------
# Gap identification
# ---------------------------------------------------------------------------

def _identify_gaps(ctx: dict, focus_area: str) -> list:
    gaps = []
    bt = ctx["req_by_type"]
    if focus_area in ("requirements", "all"):
        if bt.get("functional", 0) == 0:
            gaps.append("No functional requirements documented yet")
        if bt.get("non_functional", 0) == 0:
            gaps.append("No non-functional requirements documented (performance, security, reliability)")
        if bt.get("constraint", 0) == 0:
            gaps.append("No constraints documented (technology, regulatory, budget)")
        if bt.get("assumption", 0) == 0:
            gaps.append("No assumptions documented")
        if ctx["req_by_status"].get("draft", 0) > 0:
            gaps.append(f"{ctx['req_by_status']['draft']} requirement(s) still in draft — need approval")
    if focus_area in ("risks", "all"):
        if ctx["open_risk_count"] == 0:
            gaps.append("No risks identified — risk assessment may not have been conducted")
    if focus_area in ("stakeholders", "all"):
        if ctx["stakeholder_count"] == 0:
            gaps.append("No stakeholders mapped — stakeholder register is empty")
    if focus_area in ("all",):
        if ctx["decision_count"] == 0:
            gaps.append("No decisions logged — consider capturing key decisions made so far")
    return gaps


# ---------------------------------------------------------------------------
# Guide template builder
# ---------------------------------------------------------------------------

_SESSION_INTROS = {
    "interview":    "one-on-one structured interview",
    "workshop":     "collaborative group workshop",
    "survey":       "written questionnaire",
    "focus_group":  "moderated focus group discussion",
}

_FOCUS_SECTIONS = {
    "requirements": ["Current State", "Functional Needs", "Non-Functional Needs",
                     "Constraints & Assumptions"],
    "risks":        ["Known Issues", "Risk Identification", "Impact & Likelihood"],
    "process":      ["Current Process Walkthrough", "Pain Points", "Desired Future State"],
    "stakeholders": ["Role & Responsibilities", "Influence & Interests",
                     "Communication Preferences"],
    "all":          ["Current State", "Business Needs", "Functional Requirements",
                     "Non-Functional Requirements", "Constraints", "Risks & Issues",
                     "Stakeholder Concerns"],
}


def _build_guide(project: str, session_type: str, focus_area: str,
                 target_stakeholder: str, duration_minutes: int,
                 ctx: dict, gaps: list) -> str:
    intro = _SESSION_INTROS.get(session_type, session_type)
    lines = [
        f"# Elicitation Guide: {project}",
        "",
        f"**Session type:** {session_type.replace('_', ' ').title()} ({intro})  ",
        f"**Project:** {project}  ",
        f"**Planned duration:** {duration_minutes} min  ",
    ]
    if target_stakeholder:
        lines.append(f"**Target stakeholder:** {target_stakeholder}  ")
    lines.append("")

    # Context summary
    lines += [
        "## Context Summary (from project records)",
        "",
        f"- Requirements documented: {ctx['requirement_count']} "
        f"(functional: {ctx['req_by_type'].get('functional', 0)}, "
        f"non-functional: {ctx['req_by_type'].get('non_functional', 0)}, "
        f"constraints: {ctx['req_by_type'].get('constraint', 0)}, "
        f"assumptions: {ctx['req_by_type'].get('assumption', 0)})",
        f"- Stakeholders mapped: {ctx['stakeholder_count']}",
        f"- Active decisions logged: {ctx['decision_count']}",
        f"- Open risks: {ctx['open_risk_count']}",
        "",
    ]

    # Gap inventory
    if gaps:
        lines += ["## Identified Gaps (areas to cover in this session)", ""]
        for gap in gaps:
            lines.append(f"- ⚠️ {gap}")
        lines.append("")

    # Draft requirements needing clarification
    if ctx["draft_requirements"]:
        lines += ["## Requirements Needing Clarification", ""]
        for t in ctx["draft_requirements"]:
            lines.append(f"- {t}")
        lines.append("")

    # Session guide
    lines += [
        "## Session Guide",
        "",
        "### Opening (~5 min)",
        "",
        "- Introduce session purpose and scope",
        "- Confirm recording/note-taking consent",
        "- *[AGENT: add any specific opening questions here]*",
        "",
    ]

    sections = _FOCUS_SECTIONS.get(focus_area, _FOCUS_SECTIONS["all"])
    time_per_section = max(5, (duration_minutes - 10) // max(len(sections), 1))

    for section in sections:
        lines += [
            f"### {section} (~{time_per_section} min)",
            "",
            "*[AGENT: insert targeted questions for this section here]*",
            "",
        ]

    lines += [
        "### Closing (~5 min)",
        "",
        "- Summarise key points heard",
        "- Confirm next steps and follow-up items",
        "- *[AGENT: add closing questions here]*",
        "",
    ]

    # Suggested participants
    if ctx["suggested_participants"]:
        lines += ["## Suggested Participants", ""]
        for p in ctx["suggested_participants"]:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

def _generate(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    if not project:
        raise ValueError("'project' is required")

    session_type = (params.get("session_type") or "interview").strip().lower()
    focus_area = (params.get("focus_area") or "all").strip().lower()
    target_stakeholder = (params.get("target_stakeholder") or "").strip() or None
    duration_minutes = int(params.get("duration_minutes") or 60)

    ctx = _load_context(conn, user_id, project)
    gaps = _identify_gaps(ctx, focus_area)
    guide = _build_guide(project, session_type, focus_area, target_stakeholder,
                         duration_minutes, ctx, gaps)

    context_used = {
        "requirement_count": ctx["requirement_count"],
        "req_by_type": ctx["req_by_type"],
        "req_by_status": ctx["req_by_status"],
        "stakeholder_count": ctx["stakeholder_count"],
        "decision_count": ctx["decision_count"],
        "open_risk_count": ctx["open_risk_count"],
    }

    return {
        "project": project,
        "session_type": session_type,
        "focus_area": focus_area,
        "context_used": context_used,
        "gaps_identified": gaps,
        "suggested_participants": ctx["suggested_participants"],
        "guide_markdown": guide,
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
