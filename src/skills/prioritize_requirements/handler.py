#!/usr/bin/env python3
"""
prioritize_requirements skill handler.

Applies MoSCoW, WSJF, or Weighted scoring to a list of requirements and
returns a sorted priority table plus a markdown report.

Input:  {"parameters": {"requirements": [...], "method": "moscow"|"wsjf"|"weighted", "criteria": {...}}}
Output: {"success": true, "result": {"prioritized": [...], "method_used": "...", "priority_table_markdown": "...", "summary": {...}}}
"""
import json
import sys
from itertools import product as itertools_product


# ---------------------------------------------------------------------------
# MoSCoW
# ---------------------------------------------------------------------------

_MOSCOW_ORDER = {"must": 1, "should": 2, "could": 3, "wont": 4}
_MOSCOW_LABEL = {"must": "Must Have", "should": "Should Have", "could": "Could Have", "wont": "Won't Have"}


def _prioritize_moscow(requirements: list) -> tuple:
    grouped: dict[str, list] = {"must": [], "should": [], "could": [], "wont": []}
    for req in requirements:
        cat = str(req.get("category", "could")).lower().rstrip("'")
        if cat not in grouped:
            cat = "could"
        grouped[cat].append(req)

    prioritized = []
    rank = 1
    for cat in ("must", "should", "could", "wont"):
        for req in grouped[cat]:
            prioritized.append({
                "id": req.get("id", ""),
                "title": req.get("title", ""),
                "rank": rank,
                "score": float(_MOSCOW_ORDER[cat]),
                "category_or_method_detail": _MOSCOW_LABEL[cat],
            })
            rank += 1

    # Build markdown grouped by category
    lines = [
        "# Requirements Priority Table (MoSCoW)",
        "",
    ]
    for cat in ("must", "should", "could", "wont"):
        cat_items = [p for p in prioritized if p["category_or_method_detail"] == _MOSCOW_LABEL[cat]]
        if not cat_items:
            continue
        lines += [f"## {_MOSCOW_LABEL[cat]}", "", "| Rank | ID | Title |", "|------|----|-------|"]
        for item in cat_items:
            lines.append(f"| {item['rank']} | {item['id']} | {item['title']} |")
        lines.append("")

    summary = {
        "must_count": len(grouped["must"]),
        "should_count": len(grouped["should"]),
        "could_count": len(grouped["could"]),
        "wont_count": len(grouped["wont"]),
    }
    return prioritized, "\n".join(lines), summary


# ---------------------------------------------------------------------------
# WSJF
# ---------------------------------------------------------------------------

def _prioritize_wsjf(requirements: list) -> tuple:
    scored = []
    for req in requirements:
        ubv = float(req.get("user_business_value", 1))
        tc = float(req.get("time_criticality", 1))
        rr = float(req.get("risk_reduction", 1))
        js = float(req.get("job_size", 1)) or 1.0
        score = (ubv + tc + rr) / js
        scored.append((req, round(score, 4)))

    scored.sort(key=lambda x: x[1], reverse=True)

    prioritized = []
    for rank, (req, score) in enumerate(scored, 1):
        ubv = req.get("user_business_value", "?")
        tc = req.get("time_criticality", "?")
        rr = req.get("risk_reduction", "?")
        js = req.get("job_size", "?")
        prioritized.append({
            "id": req.get("id", ""),
            "title": req.get("title", ""),
            "rank": rank,
            "score": score,
            "category_or_method_detail": f"UBV={ubv} TC={tc} RR={rr} Size={js}",
        })

    lines = [
        "# Requirements Priority Table (WSJF)",
        "",
        "| Rank | ID | Title | WSJF Score | UBV | TC | RR | Size |",
        "|------|----|-------|-----------|-----|----|----|------|",
    ]
    for item in prioritized:
        detail = item["category_or_method_detail"]
        lines.append(f"| {item['rank']} | {item['id']} | {item['title']} | {item['score']} | {detail} |")

    top3 = [p["title"] for p in prioritized[:3]]
    bottom3 = [p["title"] for p in prioritized[-3:]]
    summary = {"top_3": top3, "bottom_3": bottom3}
    return prioritized, "\n".join(lines), summary


# ---------------------------------------------------------------------------
# Weighted
# ---------------------------------------------------------------------------

def _prioritize_weighted(requirements: list, criteria: dict) -> tuple:
    if not criteria:
        raise ValueError("criteria dict must be provided and non-empty for method='weighted'")

    scored = []
    for req in requirements:
        score = sum(float(criteria[c]) * float(req.get(c, 0)) for c in criteria)
        scored.append((req, round(score, 4)))

    scored.sort(key=lambda x: x[1], reverse=True)

    crit_names = list(criteria.keys())
    header_cols = " | ".join(crit_names)
    sep_cols = " | ".join(["------"] * len(crit_names))

    lines = [
        "# Requirements Priority Table (Weighted Scoring)",
        "",
        f"**Criteria weights:** {', '.join(f'{k}={v}' for k, v in criteria.items())}",
        "",
        f"| Rank | ID | Title | Score | {header_cols} |",
        f"|------|----|-------|-------|{sep_cols}|",
    ]

    prioritized = []
    for rank, (req, score) in enumerate(scored, 1):
        crit_vals = " | ".join(str(req.get(c, 0)) for c in crit_names)
        lines.append(f"| {rank} | {req.get('id', '')} | {req.get('title', '')} | {score} | {crit_vals} |")
        prioritized.append({
            "id": req.get("id", ""),
            "title": req.get("title", ""),
            "rank": rank,
            "score": score,
            "category_or_method_detail": ", ".join(f"{c}={req.get(c, 0)}" for c in crit_names),
        })

    top3 = [p["title"] for p in prioritized[:3]]
    bottom3 = [p["title"] for p in prioritized[-3:]]
    summary = {"top_3": top3, "bottom_3": bottom3}
    return prioritized, "\n".join(lines), summary


# ---------------------------------------------------------------------------
# Main execute
# ---------------------------------------------------------------------------

def execute(params: dict) -> dict:
    requirements = params.get("requirements", [])
    method = str(params.get("method", "moscow")).lower()
    criteria = params.get("criteria", {})

    if not requirements:
        raise ValueError("requirements list is empty")

    if method == "moscow":
        prioritized, markdown, summary = _prioritize_moscow(requirements)
    elif method == "wsjf":
        prioritized, markdown, summary = _prioritize_wsjf(requirements)
    elif method == "weighted":
        prioritized, markdown, summary = _prioritize_weighted(requirements, criteria)
    else:
        raise ValueError(f"Unknown method '{method}'. Use 'moscow', 'wsjf', or 'weighted'.")

    return {
        "prioritized": prioritized,
        "method_used": method,
        "priority_table_markdown": markdown,
        "summary": summary,
    }


def main() -> None:
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
