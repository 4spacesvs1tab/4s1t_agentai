#!/usr/bin/env python3
"""
report_generate skill handler.

Assembles a structured report from a title and ordered sections.
Optionally saves to FILE_READ_BASE_DIR. Pure stdlib — no external deps.

Input:  {"parameters": {"title": "...", "sections": [{"heading": "...", "content": "..."}],
                         "format": "markdown", "output_filename": "..."}}
Output: {"success": true, "result": {"report": "...", "word_count": N, "file_path": "..."}}
"""
import json
import os
import re
import sys
from pathlib import Path

_DEFAULT_BASE_DIR = "./data"


def _build_markdown(title: str, sections: list[dict]) -> str:
    lines = [f"# {title}", ""]
    for section in sections:
        heading = section.get("heading", "").strip()
        content = section.get("content", "").strip()
        if heading:
            lines.append(f"## {heading}")
            lines.append("")
        if content:
            lines.append(content)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_text(title: str, sections: list[dict]) -> str:
    bar = "=" * max(len(title), 40)
    lines = [bar, title, bar, ""]
    for section in sections:
        heading = section.get("heading", "").strip()
        content = section.get("content", "").strip()
        if heading:
            lines.append(heading)
            lines.append("-" * len(heading))
            lines.append("")
        if content:
            lines.append(content)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _sanitise_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]", "_", name).strip(". ")
    if not name:
        raise ValueError("'output_filename' is empty after sanitisation.")
    return name


def execute(params: dict) -> dict:
    title = params.get("title", "").strip()
    sections = params.get("sections", [])
    fmt = params.get("format", "markdown").strip().lower()
    output_filename = params.get("output_filename", "").strip()

    if not title:
        raise ValueError("'title' is required.")
    if not sections:
        raise ValueError("'sections' must be a non-empty list.")
    if fmt not in ("markdown", "text"):
        raise ValueError(f"'format' must be 'markdown' or 'text'. Got: '{fmt}'")

    # Validate sections
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            raise ValueError(f"sections[{i}] must be an object with 'heading' and 'content' keys.")

    # Build report
    if fmt == "markdown":
        report = _build_markdown(title, sections)
    else:
        report = _build_text(title, sections)

    word_count = len(report.split())
    file_path = ""

    # Optionally save to disk
    if output_filename:
        base_dir = Path(os.environ.get("FILE_READ_BASE_DIR", _DEFAULT_BASE_DIR)).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _sanitise_filename(output_filename)
        out_path = (base_dir / safe_name).resolve()
        if not str(out_path).startswith(str(base_dir)):
            raise PermissionError("Path traversal denied in 'output_filename'.")

        out_path.write_text(report, encoding="utf-8")
        file_path = safe_name

    return {
        "report": report,
        "word_count": word_count,
        "file_path": file_path,
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
