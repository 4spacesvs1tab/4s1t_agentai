#!/usr/bin/env python3
"""
export_results skill handler.

Writes structured data to a file in CSV, JSON, or plain-text format.
Sandboxed to FILE_READ_BASE_DIR.

Input:  {"parameters": {"data": [...], "format": "csv", "filename": "...", "headers": [...]}}
Output: {"success": true, "result": {"file_path": "...", "row_count": N, "size_bytes": N}}
"""
import csv
import io
import json
import os
import re
import sys
from pathlib import Path

_DEFAULT_BASE_DIR = "./data"

_FORMAT_EXTENSIONS = {
    "csv": ".csv",
    "json": ".json",
    "text": ".txt",
}


def _sanitise_filename(name: str, expected_ext: str) -> str:
    name = re.sub(r"[^\w\-. ]", "_", name).strip(". ")
    if not name:
        raise ValueError("'filename' is empty after sanitisation.")
    # Ensure correct extension
    if not name.lower().endswith(expected_ext):
        name += expected_ext
    return name


def _resolve_output_path(base_dir: Path, filename: str) -> Path:
    out_path = (base_dir / filename).resolve()
    if not str(out_path).startswith(str(base_dir)):
        raise PermissionError("Path traversal denied in 'filename'.")
    return out_path


def execute(params: dict) -> dict:
    data = params.get("data")
    fmt = params.get("format", "").strip().lower()
    filename = params.get("filename", "").strip()
    headers = params.get("headers", [])

    if data is None:
        raise ValueError("'data' parameter is required.")
    if fmt not in ("csv", "json", "text"):
        raise ValueError(f"'format' must be 'csv', 'json', or 'text'. Got: '{fmt}'")
    if not filename:
        raise ValueError("'filename' is required.")

    base_dir = Path(os.environ.get("FILE_READ_BASE_DIR", _DEFAULT_BASE_DIR)).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _sanitise_filename(filename, _FORMAT_EXTENSIONS[fmt])
    out_path = _resolve_output_path(base_dir, safe_name)

    row_count = 0

    if fmt == "csv":
        if isinstance(data, str):
            # Already formatted CSV string
            out_path.write_text(data, encoding="utf-8")
            row_count = data.count("\n")
        elif isinstance(data, list):
            buf = io.StringIO()
            if data and isinstance(data[0], dict):
                # List of dicts — keys are headers
                writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
                writer.writeheader()
                writer.writerows(data)
                row_count = len(data)
            else:
                # List of lists
                writer = csv.writer(buf)
                if headers:
                    writer.writerow(headers)
                for row in data:
                    writer.writerow(row if isinstance(row, (list, tuple)) else [row])
                row_count = len(data)
            out_path.write_text(buf.getvalue(), encoding="utf-8")
        else:
            raise ValueError("For CSV format, 'data' must be a list or a string.")

    elif fmt == "json":
        text = json.dumps(data, indent=2, default=str, ensure_ascii=False)
        out_path.write_text(text, encoding="utf-8")
        row_count = len(data) if isinstance(data, list) else 0

    elif fmt == "text":
        if not isinstance(data, str):
            data = json.dumps(data, indent=2, default=str, ensure_ascii=False)
        out_path.write_text(data, encoding="utf-8")
        row_count = 0

    size_bytes = out_path.stat().st_size

    return {
        "file_path": safe_name,
        "row_count": row_count,
        "size_bytes": size_bytes,
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
