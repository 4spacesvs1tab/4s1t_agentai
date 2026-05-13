#!/usr/bin/env python3
"""
file_write skill handler.

Writes text content to a sandboxed path under FILE_READ_BASE_DIR.
Mirrors file_read's sandbox model — path traversal is blocked.

Input:  {"parameters": {"path": "...", "content": "...", "overwrite": false, "encoding": "utf-8"}}
Output: {"success": true, "result": {"path": "...", "size_bytes": N}}
"""
import json
import os
import sys
from pathlib import Path


_DEFAULT_BASE_DIR = "./data"


def execute(params: dict) -> dict:
    requested = params.get("path", "").strip()
    content = params.get("content")
    overwrite = bool(params.get("overwrite", False))
    encoding = params.get("encoding", "utf-8")

    if not requested:
        raise ValueError("'path' parameter is required and must not be empty.")
    if content is None:
        raise ValueError("'content' parameter is required.")
    if not isinstance(content, str):
        raise TypeError(f"'content' must be a string, got {type(content).__name__}.")

    base_dir = Path(os.environ.get("FILE_READ_BASE_DIR", _DEFAULT_BASE_DIR)).resolve()

    target = (base_dir / requested).resolve()

    # Sandbox check — reject traversal outside base_dir
    if not str(target).startswith(str(base_dir)):
        raise PermissionError(
            f"Path traversal denied: '{requested}' resolves outside allowed directory."
        )

    # Refuse to overwrite unless explicitly allowed
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"File already exists: '{requested}'. Set overwrite=true to replace it."
        )

    # Create parent directories if needed (within sandbox)
    target.parent.mkdir(parents=True, exist_ok=True)

    target.write_text(content, encoding=encoding)
    size_bytes = target.stat().st_size

    return {
        "path": requested,
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
