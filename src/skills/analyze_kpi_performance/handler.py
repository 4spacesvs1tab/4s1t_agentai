#!/usr/bin/env python3
"""
analyze_kpi_performance skill handler — KB-14.

Reads KPI definitions from ba_kpis, computes variance and RAG status,
and returns a formatted scorecard.

Input:  {"parameters": {"user_id": "...", "project": "...", "actuals": [...]}}
Output: {"success": true, "result": {"overall_rag": "Green", "scorecard": [...], ...}}
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
# RAG computation
# ---------------------------------------------------------------------------

_RAG_ORDER = {"Red": 0, "Amber": 1, "Green": 2, "Unknown": 1}


def _compute_rag(actual: float, target: float, direction: str) -> str:
    if target is None or target == 0:
        return "Unknown"

    if direction == "higher_is_better":
        if actual >= target:
            return "Green"
        if actual >= target * 0.8:
            return "Amber"
        return "Red"

    if direction == "lower_is_better":
        if actual <= target:
            return "Green"
        if actual <= target * 1.2:
            return "Amber"
        return "Red"

    # target_value — within ±5% = Green, ±15% = Amber
    deviation = abs(actual - target) / target
    if deviation <= 0.05:
        return "Green"
    if deviation <= 0.15:
        return "Amber"
    return "Red"


def _worst_rag(rags: list) -> str:
    if not rags:
        return "Unknown"
    return min(rags, key=lambda r: _RAG_ORDER.get(r, 1))


# ---------------------------------------------------------------------------
# Scorecard markdown
# ---------------------------------------------------------------------------

_RAG_ICON = {"Green": "🟢", "Amber": "🟡", "Red": "🔴", "Unknown": "⚪"}


def _build_scorecard_markdown(project: str, scorecard: list) -> str:
    lines = [
        f"# KPI Scorecard: {project}",
        "",
        "| Metric | Unit | Baseline | Target | Actual | Variance % | Status |",
        "|--------|------|----------|--------|--------|------------|--------|",
    ]
    for row in scorecard:
        unit = row.get("unit") or ""
        baseline = f"{row['baseline']:.2f}" if row.get("baseline") is not None else "—"
        target = f"{row['target']:.2f}" if row.get("target") is not None else "—"
        actual = f"{row['actual_value']:.2f}"
        var = f"{row['variance_pct']:+.1f}%" if row.get("variance_pct") is not None else "—"
        rag = row.get("rag", "Unknown")
        icon = _RAG_ICON.get(rag, "⚪")
        period = f" ({row['period']})" if row.get("period") else ""
        lines.append(
            f"| {row['metric_name']}{period} | {unit} | {baseline} | {target} | {actual} | {var} | {icon} {rag} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

def _analyze(conn: sqlite3.Connection, user_id: str, params: dict) -> dict:
    project = (params.get("project") or "").strip()
    if not project:
        raise ValueError("'project' is required")
    actuals = params.get("actuals") or []
    if not actuals:
        raise ValueError("'actuals' is required and must not be empty")

    # Load KPI definitions
    kpi_rows = conn.execute(
        "SELECT metric_name, unit, baseline, target, direction FROM ba_kpis "
        "WHERE user_id = ? AND project = ? AND status = 'active'",
        (user_id, project),
    ).fetchall()
    kpi_map = {r["metric_name"]: dict(r) for r in kpi_rows}

    scorecard = []
    rags = []

    for entry in actuals:
        metric_name = entry.get("metric_name", "")
        actual_value = float(entry.get("actual_value", 0))
        period = entry.get("period") or None

        kpi_def = kpi_map.get(metric_name)
        if kpi_def:
            target = kpi_def["target"]
            baseline = kpi_def["baseline"]
            direction = kpi_def.get("direction") or "higher_is_better"
            unit = kpi_def.get("unit") or ""
            rag = _compute_rag(actual_value, target, direction)
            variance = round(actual_value - target, 4) if target is not None else None
            variance_pct = (
                round((actual_value - target) / target * 100, 2)
                if target and target != 0
                else None
            )
        else:
            target = None
            baseline = None
            direction = None
            unit = ""
            rag = "Unknown"
            variance = None
            variance_pct = None

        rags.append(rag)
        scorecard.append({
            "metric_name": metric_name,
            "unit": unit,
            "baseline": baseline,
            "target": target,
            "actual_value": actual_value,
            "variance": variance,
            "variance_pct": variance_pct,
            "rag": rag,
            "period": period,
            "direction": direction,
        })

    overall_rag = _worst_rag(rags)
    at_risk = [r["metric_name"] for r in scorecard if r["rag"] in ("Red", "Amber")]
    markdown = _build_scorecard_markdown(project, scorecard)

    return {
        "project": project,
        "overall_rag": overall_rag,
        "scorecard": scorecard,
        "at_risk_metrics": at_risk,
        "scorecard_markdown": markdown,
    }


_ACTIONS = {"analyze": _analyze}


def execute(params: dict) -> dict:
    action = (params.get("action") or "analyze").strip().lower()
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
