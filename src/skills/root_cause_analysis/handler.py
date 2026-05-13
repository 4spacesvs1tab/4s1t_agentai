#!/usr/bin/env python3
"""
root_cause_analysis skill handler — KB-14.

Structures RCA output (5-whys or fishbone) and generates a Mermaid diagram.

Input:  {"parameters": {"problem_statement": "...", "method": "fishbone", "causes": {...}, ...}}
Output: {"success": true, "result": {"mermaid_diagram": "...", "analysis_markdown": "...", ...}}
"""
import json
import re
import sys


# ---------------------------------------------------------------------------
# Mermaid helpers
# ---------------------------------------------------------------------------

def _safe_id(text: str) -> str:
    """Convert text to a safe Mermaid node ID."""
    return re.sub(r"[^A-Za-z0-9_]", "_", text)[:30]


def _safe_label(text: str) -> str:
    """Escape double quotes in Mermaid labels."""
    return text.replace('"', "'")


def _build_5whys_mermaid(problem_statement: str, why_chain: list, root_causes: list) -> str:
    lines = ["flowchart TD"]
    prev_id = "P0"
    lines.append(f'    P0["{_safe_label(problem_statement[:60])}"]')

    for i, step in enumerate(why_chain):
        nid = f"W{i + 1}"
        label = _safe_label(step.get("because", f"Why {i + 1}")[:60])
        lines.append(f'    {nid}["{label}"]')
        lines.append(f"    {prev_id} --> {nid}")
        prev_id = nid

    for i, rc in enumerate(root_causes or []):
        rid = f"RC{i}"
        lines.append(f'    {rid}[/"Root Cause: {_safe_label(rc[:50])}"/]')
        lines.append(f"    {prev_id} --> {rid}")

    return "\n".join(lines)


def _build_fishbone_mermaid(problem_statement: str, causes: dict) -> str:
    lines = ["flowchart LR"]
    effect_id = "EFFECT"
    lines.append(f'    {effect_id}["{_safe_label(problem_statement[:60])}"]')

    for cat_idx, (category, cause_list) in enumerate(causes.items()):
        cat_id = f"CAT{cat_idx}"
        lines.append(f'    {cat_id}(("{_safe_label(category)}"))')
        lines.append(f"    {cat_id} --> {effect_id}")
        for c_idx, cause in enumerate(cause_list or []):
            cid = f"C{cat_idx}_{c_idx}"
            lines.append(f'    {cid}["{_safe_label(cause[:50])}"]')
            lines.append(f"    {cid} --> {cat_id}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown report builders
# ---------------------------------------------------------------------------

def _build_5whys_markdown(problem_statement: str, why_chain: list,
                           root_causes: list, recommended_actions: list) -> str:
    lines = [
        "# Root Cause Analysis",
        "",
        "## Method: 5 Whys",
        "",
        f"**Problem:** {problem_statement}",
        "",
        "## Why Chain",
        "",
    ]
    for i, step in enumerate(why_chain):
        lines.append(f"**Why {i + 1}:** {step.get('why', '')}  ")
        lines.append(f"**Because:** {step.get('because', '')}  ")
        lines.append("")

    if root_causes:
        lines += ["## Root Causes", ""]
        for rc in root_causes:
            lines.append(f"- {rc}")
        lines.append("")

    if recommended_actions:
        lines += ["## Recommended Actions", ""]
        for action in recommended_actions:
            lines.append(f"- [ ] {action}")
        lines.append("")

    return "\n".join(lines)


def _build_fishbone_markdown(problem_statement: str, causes: dict,
                              root_causes: list, recommended_actions: list) -> str:
    lines = [
        "# Root Cause Analysis",
        "",
        "## Method: Fishbone (Ishikawa)",
        "",
        f"**Problem / Effect:** {problem_statement}",
        "",
        "## Cause Categories",
        "",
    ]
    total = 0
    for category, cause_list in causes.items():
        lines.append(f"### {category}")
        for cause in (cause_list or []):
            lines.append(f"- {cause}")
            total += 1
        lines.append("")

    if root_causes:
        lines += ["## Root Causes", ""]
        for rc in root_causes:
            lines.append(f"- **{rc}**")
        lines.append("")

    if recommended_actions:
        lines += ["## Recommended Actions", ""]
        for action in recommended_actions:
            lines.append(f"- [ ] {action}")
        lines.append("")

    return "\n".join(lines), total


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

def execute(params: dict) -> dict:
    problem_statement = (params.get("problem_statement") or "").strip()
    if not problem_statement:
        raise ValueError("'problem_statement' is required")

    method = (params.get("method") or "fishbone").strip().lower()
    if method not in ("5_whys", "fishbone"):
        raise ValueError("'method' must be '5_whys' or 'fishbone'")

    why_chain = params.get("why_chain") or []
    causes = params.get("causes") or {}
    root_causes = params.get("root_causes") or []
    recommended_actions = params.get("recommended_actions") or []

    if method == "5_whys":
        mermaid = _build_5whys_mermaid(problem_statement, why_chain, root_causes)
        markdown = _build_5whys_markdown(problem_statement, why_chain, root_causes, recommended_actions)
        cause_count = len(why_chain)
    else:
        mermaid = _build_fishbone_mermaid(problem_statement, causes)
        markdown, cause_count = _build_fishbone_markdown(
            problem_statement, causes, root_causes, recommended_actions
        )

    return {
        "method_used": method,
        "problem_statement": problem_statement,
        "root_causes": root_causes,
        "recommended_actions": recommended_actions,
        "cause_count": cause_count,
        "mermaid_diagram": mermaid,
        "analysis_markdown": markdown,
    }


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
