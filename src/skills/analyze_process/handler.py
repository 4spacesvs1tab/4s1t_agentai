#!/usr/bin/env python3
"""
analyze_process skill handler.

Formats structured process analysis findings (supplied by the agent) into a
clean markdown report. Sections rendered depend on analysis_type.

Input:  {"parameters": {"process_name": "...", "analysis_type": "full", "framework": "babok",
          "steps": [...], "actors": [...], "handoffs": [...], "pain_points": [...],
          "improvement_opportunities": [...], "process_description": "...", "project": "..."}}
Output: {"success": true, "result": {"process_name": "...", "analysis_type": "...",
          "framework": "...", "step_count": N, "actor_count": N, "pain_point_count": N,
          "improvement_count": N, "analysis_markdown": "..."}}
"""
import json
import sys


_FRAMEWORK_LABELS = {
    "babok": "BABOK v3",
    "lean": "Lean",
    "six_sigma": "Six Sigma / DMAIC",
}

# Which sections to include per analysis_type
_SECTIONS = {
    "full": {"summary", "steps", "actors", "handoffs", "pain_points", "improvements"},
    "gaps": {"summary", "pain_points", "improvements"},
    "actors": {"summary", "actors", "handoffs"},
    "pain_points": {"summary", "pain_points"},
    "improvements": {"summary", "improvements"},
}


def _build_markdown(
    process_name: str,
    process_description: str,
    analysis_type: str,
    framework: str,
    steps: list,
    actors: list,
    handoffs: list,
    pain_points: list,
    improvements: list,
    project: str,
) -> str:
    include = _SECTIONS.get(analysis_type, _SECTIONS["full"])
    fw_label = _FRAMEWORK_LABELS.get(framework, framework)

    lines = [f"# Process Analysis: {process_name}", ""]

    if project:
        lines += [f"**Project:** {project}  ", ""]

    if process_description:
        lines += [f"**Description:** {process_description}  ", ""]

    lines += [f"**Framework:** {fw_label}  ", f"**Analysis Type:** {analysis_type.replace('_', ' ').title()}  ", ""]

    # Summary section
    if "summary" in include:
        lines += [
            "## Summary",
            "",
            f"| Attribute | Value |",
            f"|-----------|-------|",
            f"| Process Steps | {len(steps)} |",
            f"| Actors / Systems | {len(actors)} |",
            f"| Handoff Points | {len(handoffs)} |",
            f"| Pain Points | {len(pain_points)} |",
            f"| Improvement Opportunities | {len(improvements)} |",
            "",
        ]

    # Process Steps
    if "steps" in include and steps:
        lines += ["## Process Steps", ""]
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    # Actors / Systems
    if "actors" in include and actors:
        lines += ["## Actors / Systems", ""]
        for actor in actors:
            lines.append(f"- {actor}")
        lines.append("")

    # Handoffs
    if "handoffs" in include and handoffs:
        lines += [
            "## Handoffs",
            "",
            "| From | To | Trigger |",
            "|------|----|---------|",
        ]
        for h in handoffs:
            trigger = h.get("trigger", "—")
            lines.append(f"| {h.get('from', '')} | {h.get('to', '')} | {trigger} |")
        lines.append("")

    # Pain Points
    if "pain_points" in include and pain_points:
        lines += ["## Pain Points", ""]
        for i, pp in enumerate(pain_points, 1):
            lines.append(f"{i}. {pp}")
        lines.append("")

    # Improvements
    if "improvements" in include and improvements:
        lines += ["## Improvement Opportunities", ""]
        for i, imp in enumerate(improvements, 1):
            lines.append(f"{i}. {imp}")
        lines.append("")

    lines.append("---")
    lines.append(f"_Analysis framework: {fw_label}_")

    return "\n".join(lines)


def execute(params: dict) -> dict:
    process_name = params.get("process_name", "Unnamed Process")
    process_description = params.get("process_description", "")
    analysis_type = str(params.get("analysis_type", "full")).lower()
    framework = str(params.get("framework", "babok")).lower()
    steps = params.get("steps") or []
    actors = params.get("actors") or []
    handoffs = params.get("handoffs") or []
    pain_points = params.get("pain_points") or []
    improvements = params.get("improvement_opportunities") or []
    project = params.get("project", "")

    if analysis_type not in _SECTIONS:
        raise ValueError(
            f"Unknown analysis_type '{analysis_type}'. "
            "Use: full, gaps, actors, pain_points, improvements."
        )
    if framework not in _FRAMEWORK_LABELS:
        raise ValueError(f"Unknown framework '{framework}'. Use: babok, lean, six_sigma.")

    markdown = _build_markdown(
        process_name, process_description, analysis_type, framework,
        steps, actors, handoffs, pain_points, improvements, project,
    )

    return {
        "process_name": process_name,
        "analysis_type": analysis_type,
        "framework": framework,
        "step_count": len(steps),
        "actor_count": len(actors),
        "pain_point_count": len(pain_points),
        "improvement_count": len(improvements),
        "analysis_markdown": markdown,
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
