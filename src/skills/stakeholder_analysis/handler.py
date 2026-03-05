#!/usr/bin/env python3
"""
stakeholder_analysis skill handler — RACI matrix formatter.

The calling agent reasons about RACI assignments and passes structured data.
This handler validates the structure and produces a formatted RACI matrix.

Input:  {"parameters": {"process_name": "...", "roles": [...], "tasks": [...], "assignments": [...]}}
Output: {"success": true, "result": {"raci_markdown": "...", "raci_json": {...}, "validation_warnings": [...]}}
"""
import json
import sys


def _build_raci_markdown(process_name: str, roles: list, assignments: list) -> str:
    """Build a markdown RACI table."""
    if not roles or not assignments:
        return f"# RACI Matrix: {process_name}\n\n_No data provided._"

    header = "| Task | " + " | ".join(roles) + " |"
    separator = "|------|" + "|".join(["------"] * len(roles)) + "|"
    rows = [header, separator]

    for a in assignments:
        task = a.get("task", "")
        r_role = a.get("R", "")
        a_role = a.get("A", "")
        c_roles = a.get("C", [])
        i_roles = a.get("I", [])

        cells = []
        for role in roles:
            tags = []
            if role == r_role:
                tags.append("**R**")
            if role == a_role:
                tags.append("**A**")
            if role in c_roles:
                tags.append("C")
            if role in i_roles:
                tags.append("I")
            cells.append(", ".join(tags) if tags else "")

        rows.append(f"| {task} | " + " | ".join(cells) + " |")

    legend = (
        "\n\n**Legend:** R = Responsible, A = Accountable, C = Consulted, I = Informed"
    )
    return f"# RACI Matrix: {process_name}\n\n" + "\n".join(rows) + legend


def execute(params: dict) -> dict:
    process_name = params.get("process_name", "Unnamed Process")
    roles = params.get("roles", [])
    tasks = params.get("tasks", [])
    assignments = params.get("assignments", [])

    warnings = []

    if not roles:
        warnings.append("No roles provided — RACI matrix will be empty.")
    if not tasks:
        warnings.append("No tasks provided — RACI matrix will have no rows.")
    if not assignments:
        warnings.append("No assignments provided — all cells will be empty.")

    # Validate that each assignment task appears in the tasks list
    task_set = set(tasks)
    for a in assignments:
        t = a.get("task", "")
        if t and t not in task_set:
            warnings.append(f"Assignment task '{t}' not in tasks list — added.")
            tasks.append(t)
            task_set.add(t)

    # Validate that assigned roles exist in roles list
    role_set = set(roles)
    for a in assignments:
        for field in ["R", "A"]:
            val = a.get(field, "")
            if val and val not in role_set:
                warnings.append(f"Role '{val}' in {field} not in roles list.")
        for field in ["C", "I"]:
            for val in a.get(field, []):
                if val and val not in role_set:
                    warnings.append(f"Role '{val}' in {field} not in roles list.")

    raci_markdown = _build_raci_markdown(process_name, roles, assignments)
    raci_json = {
        "process_name": process_name,
        "roles": roles,
        "tasks": tasks,
        "assignments": assignments,
    }

    return {
        "raci_markdown": raci_markdown,
        "raci_json": raci_json,
        "validation_warnings": warnings,
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
