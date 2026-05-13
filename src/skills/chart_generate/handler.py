#!/usr/bin/env python3
"""
chart_generate skill handler.

Generates a chart PNG using matplotlib (Agg backend — no display required).
Saves the file to FILE_READ_BASE_DIR (the shared data directory).

Input:  {"parameters": {"chart_type": "bar", "labels": [...], "values": [...],
                         "title": "...", "output_filename": "...",
                         "x_label": "", "y_label": ""}}
Output: {"success": true, "result": {"file_path": "...", "format": "png"}}
"""
import json
import os
import sys
from pathlib import Path

_DEFAULT_BASE_DIR = "./data"


def _sanitise_filename(name: str) -> str:
    """Allow only safe filename characters."""
    import re
    name = re.sub(r"[^\w\-. ]", "_", name)
    name = name.strip(". ")
    if not name:
        raise ValueError("output_filename is empty after sanitisation.")
    if not name.lower().endswith(".png"):
        name += ".png"
    return name


def execute(params: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend — must be set before pyplot import
    import matplotlib.pyplot as plt

    chart_type = params.get("chart_type", "").strip().lower()
    if chart_type not in ("bar", "line", "pie", "scatter"):
        raise ValueError(f"chart_type must be one of: bar, line, pie, scatter. Got: '{chart_type}'")

    labels = params.get("labels", [])
    values = params.get("values", [])
    title = params.get("title", "").strip()
    output_filename = params.get("output_filename", "").strip()

    if not labels:
        raise ValueError("'labels' must be a non-empty list.")
    if not values:
        raise ValueError("'values' must be a non-empty list.")
    if len(labels) != len(values):
        raise ValueError(
            f"'labels' and 'values' must have the same length "
            f"(got {len(labels)} labels, {len(values)} values)."
        )
    if not output_filename:
        raise ValueError("'output_filename' is required.")

    x_label = params.get("x_label", "")
    y_label = params.get("y_label", "")

    # Resolve output path (sandboxed to FILE_READ_BASE_DIR)
    base_dir = Path(os.environ.get("FILE_READ_BASE_DIR", _DEFAULT_BASE_DIR)).resolve()
    safe_name = _sanitise_filename(output_filename)
    out_path = (base_dir / safe_name).resolve()
    if not str(out_path).startswith(str(base_dir)):
        raise PermissionError("Path traversal denied in output_filename.")
    base_dir.mkdir(parents=True, exist_ok=True)

    # Generate chart
    fig, ax = plt.subplots(figsize=(10, 6))

    if chart_type == "bar":
        ax.bar(labels, values)
        if x_label:
            ax.set_xlabel(x_label)
        if y_label:
            ax.set_ylabel(y_label)
    elif chart_type == "line":
        ax.plot(labels, values, marker="o")
        if x_label:
            ax.set_xlabel(x_label)
        if y_label:
            ax.set_ylabel(y_label)
    elif chart_type == "pie":
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
    elif chart_type == "scatter":
        # For scatter: treat label index as x, values as y (labels shown as tick labels)
        x_vals = list(range(len(labels)))
        ax.scatter(x_vals, values)
        ax.set_xticks(x_vals)
        ax.set_xticklabels(labels, rotation=45, ha="right")
        if x_label:
            ax.set_xlabel(x_label)
        if y_label:
            ax.set_ylabel(y_label)

    if title:
        ax.set_title(title)

    plt.tight_layout()
    plt.savefig(str(out_path), format="png", dpi=100)
    plt.close(fig)

    return {
        "file_path": safe_name,
        "format": "png",
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
