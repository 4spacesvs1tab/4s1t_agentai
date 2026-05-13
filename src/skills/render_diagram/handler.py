#!/usr/bin/env python3
"""
render_diagram skill handler.

Renders PlantUML or Mermaid source to SVG/PNG using the appropriate CLI tool.

PlantUML:  java -jar /opt/plantuml/plantuml.jar -tsvg|-tpng input.puml
Mermaid:   mmdc -i input.mmd -o output.svg

Output files are saved to data/documents/.

Input:  {"parameters": {"source": "...", "diagram_type": "plantuml", "output_format": "svg", "filename": "optional_stem"}}
Output: {"success": true, "result": {"file_id": "...", "download_path": "/api/v1/documents/...", "output_format": "svg", "size_bytes": 1234}}
"""
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DOCUMENTS_DIR = _PROJECT_ROOT / "data" / "documents"

# PlantUML JAR locations to try (in order)
_PLANTUML_JAR_CANDIDATES = [
    "/opt/plantuml/plantuml.jar",
    "/usr/share/plantuml/plantuml.jar",
    "/usr/local/lib/plantuml.jar",
]

# Mermaid CLI executable
_MMDC_CANDIDATES = [
    "/usr/local/bin/mmdc",
    "/usr/bin/mmdc",
    "mmdc",
]


def _find_plantuml_jar() -> str | None:
    env_jar = os.environ.get("PLANTUML_JAR")
    if env_jar and Path(env_jar).exists():
        return env_jar
    for p in _PLANTUML_JAR_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _find_mmdc() -> str | None:
    import shutil
    env_mmdc = os.environ.get("MMDC_PATH")
    if env_mmdc:
        return env_mmdc
    for p in _MMDC_CANDIDATES:
        if shutil.which(p):
            return p
    return None


def _render_plantuml(source: str, output_format: str, out_path: Path) -> None:
    jar = _find_plantuml_jar()
    if not jar:
        raise RuntimeError(
            "PlantUML JAR not found. Install with: apt-get install default-jre-headless "
            "and place plantuml.jar at /opt/plantuml/plantuml.jar, or set PLANTUML_JAR env var."
        )
    fmt_flag = f"-t{output_format}"  # -tsvg or -tpng
    with tempfile.TemporaryDirectory() as tmpdir:
        src_file = Path(tmpdir) / "diagram.puml"
        src_file.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [
                "java",
                "-DPLANTUML_SECURITY_PROFILE=SANDBOX",
                "-DPLANTUML_LIMIT_SIZE=8192",
                "-jar", jar,
                fmt_flag,
                "-o", str(out_path.parent),
                str(src_file),
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )
        # PlantUML exits with rc=200 on syntax errors (still writes an error diagram file).
        # Clean up the bad output and raise a helpful message.
        expected = out_path.parent / (src_file.stem + f".{output_format}")
        if result.returncode != 0:
            if expected.exists():
                expected.unlink(missing_ok=True)
            msg = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise RuntimeError(
                f"PlantUML syntax error: {msg}. "
                "Tip: use multi-line class/component bodies (opening {{ on its own line), "
                "not inline {{ field }} syntax."
            )
        if expected.exists() and expected != out_path:
            expected.rename(out_path)


def _render_mermaid(source: str, output_format: str, out_path: Path) -> None:
    mmdc = _find_mmdc()
    if not mmdc:
        raise RuntimeError(
            "Mermaid CLI (mmdc) not found. Install with: npm install -g @mermaid-js/mermaid-cli, "
            "or set MMDC_PATH env var."
        )
    with tempfile.TemporaryDirectory() as tmpdir:
        src_file = Path(tmpdir) / "diagram.mmd"
        src_file.write_text(source, encoding="utf-8")
        result = subprocess.run(
            [mmdc, "-i", str(src_file), "-o", str(out_path), "--backgroundColor", "white"],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Mermaid CLI failed: {result.stderr.strip() or result.stdout.strip()}")


def execute(params: dict) -> dict:
    source = (params.get("source") or "").strip()
    if not source:
        raise ValueError("'source' is required")

    diagram_type = (params.get("diagram_type") or "plantuml").strip().lower()
    output_format = (params.get("output_format") or "svg").strip().lower()
    if diagram_type not in ("plantuml", "mermaid"):
        raise ValueError(f"Unknown diagram_type '{diagram_type}'. Valid: plantuml, mermaid")
    if output_format not in ("svg", "png"):
        raise ValueError(f"Unknown output_format '{output_format}'. Valid: svg, png")

    stem = (params.get("filename") or "").strip()
    if not stem:
        stem = str(uuid.uuid4())[:8]
    # Sanitise stem: keep alphanumeric, hyphens, underscores only
    import re
    stem = re.sub(r"[^\w\-]", "_", stem)[:64].strip("_")

    _DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    file_id = f"{stem}.{output_format}"
    out_path = _DOCUMENTS_DIR / file_id

    if diagram_type == "plantuml":
        _render_plantuml(source, output_format, out_path)
    else:
        _render_mermaid(source, output_format, out_path)

    if not out_path.exists():
        raise RuntimeError(f"Renderer produced no output file at {out_path}")

    size = out_path.stat().st_size
    return {
        "file_id": file_id,
        "download_path": f"/api/v1/documents/{file_id}",
        "output_format": output_format,
        "size_bytes": size,
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
