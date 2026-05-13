#!/usr/bin/env python3
"""
create_diagram skill handler — KB-14.

Validates Mermaid diagram syntax and returns corrected source + element counts.
The calling agent generates the Mermaid source; this skill validates and repairs it.

Input:  {"parameters": {"diagram_type": "state", "diagram_source": "...", "title": "..."}}
Output: {"success": true, "result": {"valid": true, "mermaid_source": "...", "element_summary": {...}, ...}}
"""
import json
import re
import sys


# ---------------------------------------------------------------------------
# Code fence stripping
# ---------------------------------------------------------------------------

def _strip_fences(source: str) -> str:
    source = source.strip()
    # Remove ```mermaid ... ``` or ``` ... ```
    source = re.sub(r"^```(?:mermaid)?\s*\n?", "", source, flags=re.IGNORECASE)
    source = re.sub(r"\n?```\s*$", "", source)
    return source.strip()


# ---------------------------------------------------------------------------
# Diagram-specific validators
# ---------------------------------------------------------------------------

def _validate_state(source: str) -> tuple[dict, list, list]:
    errors, warnings = [], []
    if not re.search(r"\bstateDiagram(?:-v2)?\b", source, re.IGNORECASE):
        errors.append("Missing 'stateDiagram' or 'stateDiagram-v2' keyword.")
    if re.search(r"\b(?:graph|flowchart|classDiagram|sequenceDiagram|erDiagram)\b", source, re.IGNORECASE):
        errors.append("Source contains a different diagram type keyword — wrong diagram_type specified?")

    states = set()
    for m in re.finditer(r"^\s*([A-Za-z_]\w*)\s*:", source, re.MULTILINE):
        states.add(m.group(1))
    for m in re.finditer(r"(?:-->|->)\s*([A-Za-z_]\w*)", source):
        states.add(m.group(1))
    for m in re.finditer(r"([A-Za-z_]\w*)\s*(?:-->|->)", source):
        states.add(m.group(1))
    transitions = len(re.findall(r"-->|->", source))

    if not states and not errors:
        warnings.append("No states detected — diagram may be empty.")

    return {"states": len(states), "transitions": transitions}, errors, warnings


def _validate_sequence(source: str) -> tuple[dict, list, list]:
    errors, warnings = [], []
    if not re.search(r"\bsequenceDiagram\b", source, re.IGNORECASE):
        errors.append("Missing 'sequenceDiagram' keyword.")

    participants = len(re.findall(r"^\s*(?:participant|actor)\s+", source, re.MULTILINE | re.IGNORECASE))
    messages = len(re.findall(r"->?>|-->>?", source))

    if messages == 0 and not errors:
        warnings.append("No messages detected.")

    return {"participants": participants, "messages": messages}, errors, warnings


def _validate_er(source: str) -> tuple[dict, list, list]:
    errors, warnings = [], []
    if not re.search(r"\berDiagram\b", source, re.IGNORECASE):
        errors.append("Missing 'erDiagram' keyword.")

    entities = set()
    for m in re.finditer(r"^\s*([A-Z_][A-Z0-9_]*)\s*\{", source, re.MULTILINE):
        entities.add(m.group(1))
    for m in re.finditer(r"([A-Z_][A-Z0-9_]*)\s*[|o{][|o}]", source):
        entities.add(m.group(1))
    relationships = len(re.findall(r"[|o{][|o}]--[|o{][|o}]|}\|--\|{|}\|--o{", source))

    return {"entities": len(entities), "relationships": relationships}, errors, warnings


def _validate_class(source: str) -> tuple[dict, list, list]:
    errors, warnings = [], []
    if not re.search(r"\bclassDiagram\b", source, re.IGNORECASE):
        errors.append("Missing 'classDiagram' keyword.")

    classes = len(re.findall(r"^\s*class\s+\w+", source, re.MULTILINE))
    relationships = len(re.findall(r"--|>|-->|\.\.>|--", source))

    if classes == 0 and not errors:
        warnings.append("No class definitions detected.")

    return {"classes": classes, "relationships": relationships}, errors, warnings


def _validate_flow(source: str, diagram_type: str) -> tuple[dict, list, list]:
    errors, warnings = [], []
    if not re.search(r"\b(?:graph|flowchart)\b", source, re.IGNORECASE):
        errors.append("Missing 'graph' or 'flowchart' keyword.")

    nodes = len(re.findall(r"\[|\(|\{", source))
    edges = len(re.findall(r"-->|---|==>|-\.-?>|~~>", source))

    if nodes == 0 and not errors:
        warnings.append("No nodes detected.")

    return {"nodes": nodes, "edges": edges}, errors, warnings


_VALIDATORS = {
    "state":      _validate_state,
    "sequence":   _validate_sequence,
    "er":         _validate_er,
    "class":      _validate_class,
    "data_flow":  _validate_flow,
    "use_case":   _validate_flow,
}


# ---------------------------------------------------------------------------
# General structural check
# ---------------------------------------------------------------------------

def _check_brackets(source: str, warnings: list) -> None:
    pairs = [("[", "]"), ("{", "}"), ("(", ")")]
    for open_c, close_c in pairs:
        diff = abs(source.count(open_c) - source.count(close_c))
        if diff > 2:
            warnings.append(
                f"Possible unmatched brackets: {diff} unmatched '{open_c}'/'{close_c}' pairs."
            )


# ---------------------------------------------------------------------------
# Core execute
# ---------------------------------------------------------------------------

_VALID_TYPES = {"state", "sequence", "er", "class", "data_flow", "use_case"}


def execute(params: dict) -> dict:
    diagram_type = (params.get("diagram_type") or "").strip().lower()
    raw_source = params.get("diagram_source") or ""
    title = params.get("title") or None

    if not diagram_type:
        raise ValueError("'diagram_type' is required")
    if diagram_type not in _VALID_TYPES:
        raise ValueError(f"Unknown diagram_type '{diagram_type}'. Valid: {', '.join(sorted(_VALID_TYPES))}")
    if not raw_source.strip():
        raise ValueError("'diagram_source' is required and must not be empty")

    source = _strip_fences(raw_source)

    validator = _VALIDATORS[diagram_type]
    # data_flow and use_case share the flow validator but need the type passed
    if diagram_type in ("data_flow", "use_case"):
        element_summary, errors, warnings = validator(source, diagram_type)
    else:
        element_summary, errors, warnings = validator(source)

    _check_brackets(source, warnings)

    valid = len(errors) == 0

    return {
        "valid": valid,
        "diagram_type": diagram_type,
        "mermaid_source": source,
        "title": title,
        "element_summary": element_summary,
        "errors": errors,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Subprocess entry point
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
