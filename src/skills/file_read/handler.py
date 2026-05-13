#!/usr/bin/env python3
"""
file_read skill handler.

Reads a file from FILE_READ_BASE_DIR (passed as env var).
Path traversal outside the sandbox root is blocked.

Input:  {"parameters": {"path": "...", "encoding": "utf-8", "max_bytes": 1048576}}
Output: {"success": true, "result": {"content": "...", "size_bytes": N, "truncated": false}}
"""
import json
import os
import sys
from pathlib import Path


_DEFAULT_BASE_DIR = "./data"
_DEFAULT_MAX_BYTES = 1_048_576  # 1 MB


def execute(params: dict) -> dict:
    base_dir = Path(os.environ.get("FILE_READ_BASE_DIR", _DEFAULT_BASE_DIR)).resolve()
    requested = params.get("path", "")
    encoding = params.get("encoding", "utf-8")
    max_bytes = int(params.get("max_bytes", _DEFAULT_MAX_BYTES))

    if not requested:
        raise ValueError("'path' parameter is required and must not be empty.")

    # Resolve the full path and check it stays inside the sandbox root
    target = (base_dir / requested).resolve()
    if not str(target).startswith(str(base_dir)):
        raise PermissionError(
            f"Path traversal denied: '{requested}' resolves outside allowed directory."
        )

    if not target.exists():
        raise FileNotFoundError(f"File not found: '{requested}'")
    if not target.is_file():
        raise IsADirectoryError(f"Path is a directory, not a file: '{requested}'")

    size_bytes = target.stat().st_size
    truncated = size_bytes > max_bytes

    with open(target, "r", encoding=encoding, errors="replace") as f:
        content = f.read(max_bytes)

    return {
        "content": content,
        "size_bytes": size_bytes,
        "truncated": truncated,
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
