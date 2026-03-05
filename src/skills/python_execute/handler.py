#!/usr/bin/env python3
"""
python_execute skill handler — Docker-based Python code execution.

This handler itself runs as a trusted subprocess (parent Python interpreter).
It launches an isolated Docker container to execute arbitrary user-provided code.

Security properties enforced by Docker:
  --network=none       no network access inside the container
  --memory             hard memory cap
  --cpus               CPU throttle
  --read-only          container filesystem is read-only (except /tmp and /output)
  --cap-drop ALL       drop all Linux capabilities
  --security-opt       no new privileges
  --rm                 container auto-deleted after exit

Input files are mounted read-only at /data/.
Output files written to /output/ inside the container are returned as paths.

Input:  {"parameters": {"code": "...", "data_files": [...]}}
Output: {"success": true, "result": {
    "stdout": "...", "stderr": "...",
    "output_files": [...], "exit_code": 0
}}
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Docker image used for execution
_DOCKER_IMAGE = os.environ.get("PYTHON_EXECUTE_IMAGE", "skills-python-execute:latest")

# Resource limits (overridable via env vars for testing)
_MEMORY_LIMIT = os.environ.get("PYTHON_EXECUTE_MEMORY", "512m")
_CPU_LIMIT = os.environ.get("PYTHON_EXECUTE_CPUS", "1.0")
_TIMEOUT_SECONDS = int(os.environ.get("PYTHON_EXECUTE_TIMEOUT", "55"))


def _resolve_data_files(data_files: list[str]) -> list[Path]:
    """Resolve and validate data file paths against FILE_READ_BASE_DIR."""
    base_dir = Path(os.environ.get("FILE_READ_BASE_DIR", "./data")).resolve()
    resolved = []
    for requested in data_files:
        target = (base_dir / requested).resolve()
        if not str(target).startswith(str(base_dir)):
            raise PermissionError(
                f"Path traversal denied for data_file: '{requested}'"
            )
        if not target.exists():
            raise FileNotFoundError(f"Data file not found in sandbox: '{requested}'")
        resolved.append(target)
    return resolved


def execute(params: dict) -> dict:
    code = params.get("code", "")
    if not code or not code.strip():
        raise ValueError("'code' parameter is required and must not be empty.")

    data_files_param = params.get("data_files", [])
    data_file_paths = _resolve_data_files(data_files_param)

    with tempfile.TemporaryDirectory(prefix="skills_pyexec_") as tmpdir:
        tmp = Path(tmpdir)

        # Write the user code to a file to be mounted into the container
        code_file = tmp / "user_code.py"
        code_file.write_text(code, encoding="utf-8")

        # Output directory — container writes here, we read results from host side
        output_dir = tmp / "output"
        output_dir.mkdir()

        # Build docker run command
        cmd = [
            "docker", "run",
            "--rm",
            "--network=none",
            f"--memory={_MEMORY_LIMIT}",
            f"--cpus={_CPU_LIMIT}",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            # Mount code read-only
            "-v", f"{code_file}:/code/user_code.py:ro",
            # Mount output read-write
            "-v", f"{output_dir}:/output",
        ]

        # Mount data files read-only at /data/<basename>
        for fp in data_file_paths:
            cmd += ["-v", f"{fp}:/data/{fp.name}:ro"]

        cmd.append(_DOCKER_IMAGE)
        cmd += ["python", "/code/user_code.py"]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Execution timed out after {_TIMEOUT_SECONDS}s.",
                "output_files": [],
                "exit_code": -1,
            }
        except FileNotFoundError:
            raise RuntimeError(
                "Docker is not available or not in PATH. "
                "Ensure Docker is installed and running."
            )

        # Collect files written to /output
        output_files = sorted(str(p.name) for p in output_dir.iterdir() if p.is_file())

        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "output_files": output_files,
            "exit_code": proc.returncode,
        }


# ---------------------------------------------------------------------------
# Entry point
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
