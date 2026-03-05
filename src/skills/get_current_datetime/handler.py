#!/usr/bin/env python3
"""
get_current_datetime skill handler.

Stdlib-only — no external dependencies.
Input:  {"skill_name": ..., "parameters": {"format": ""}, "calling_agent_type": ...}
Output: {"success": true, "result": {"iso": "...", "unix": N, "formatted": "..."}}
"""
import json
import sys
import time
from datetime import datetime, timezone


def execute(params: dict) -> dict:
    fmt = params.get("format", "")
    now = datetime.now(timezone.utc)
    iso = now.isoformat()
    unix = int(time.time())
    try:
        formatted = now.strftime(fmt) if fmt else iso
    except Exception:
        formatted = iso
    return {"iso": iso, "unix": unix, "formatted": formatted}


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
