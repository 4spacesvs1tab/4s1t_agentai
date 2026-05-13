#!/usr/bin/env python3
"""
gap_analysis skill handler — AS-IS vs TO-BE gap report formatter.

Input:  {"parameters": {"domain": "...", "as_is": [...], "to_be": [...], "gaps": [...]}}
Output: {"success": true, "result": {"gap_report_markdown": "...", "gap_summary_json": {...}, ...}}
"""
import json
import sys


def _build_markdown(domain: str, as_is: list, to_be: list, gaps: list) -> str:
    lines = [
        f"# Gap Analysis Report: {domain}",
        "",
        "## Summary",
        "",
        f"- **Domain:** {domain}",
        f"- **Total Gaps Identified:** {len(gaps)}",
        f"- **High Priority:** {sum(1 for g in gaps if g.get('priority') == 'high')}",
        f"- **Medium Priority:** {sum(1 for g in gaps if g.get('priority') == 'medium')}",
        f"- **Low Priority:** {sum(1 for g in gaps if g.get('priority') == 'low')}",
        "",
        "## AS-IS State",
        "",
        "| Capability | Current Maturity |",
        "|------------|-----------------|",
    ]
    for item in as_is:
        lines.append(f"| {item.get('capability', '')} | {item.get('maturity', 'Not assessed')} |")

    lines += [
        "",
        "## TO-BE State",
        "",
        "| Capability | Target Maturity |",
        "|------------|----------------|",
    ]
    for item in to_be:
        lines.append(f"| {item.get('capability', '')} | {item.get('target_maturity', 'Not specified')} |")

    lines += [
        "",
        "## Gap Register",
        "",
        "| # | Capability | Gap Description | Priority | Recommended Action |",
        "|---|------------|----------------|----------|-------------------|",
    ]
    for i, gap in enumerate(gaps, 1):
        priority_fmt = {
            "high": "🔴 High",
            "medium": "🟡 Medium",
            "low": "🟢 Low",
        }.get(gap.get("priority", ""), gap.get("priority", ""))
        lines.append(
            f"| {i} | {gap.get('capability', '')} | {gap.get('gap_description', '')} "
            f"| {priority_fmt} | {gap.get('recommended_action', '—')} |"
        )

    return "\n".join(lines)


def execute(params: dict) -> dict:
    domain = params.get("domain", "Unnamed Domain")
    as_is = params.get("as_is", [])
    to_be = params.get("to_be", [])
    gaps = params.get("gaps", [])

    markdown = _build_markdown(domain, as_is, to_be, gaps)

    high = sum(1 for g in gaps if g.get("priority") == "high")
    medium = sum(1 for g in gaps if g.get("priority") == "medium")
    low = sum(1 for g in gaps if g.get("priority") == "low")

    return {
        "gap_report_markdown": markdown,
        "gap_summary_json": {
            "domain": domain,
            "as_is": as_is,
            "to_be": to_be,
            "gaps": gaps,
            "counts": {"high": high, "medium": medium, "low": low, "total": len(gaps)},
        },
        "high_priority_count": high,
        "total_gaps": len(gaps),
    }


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
