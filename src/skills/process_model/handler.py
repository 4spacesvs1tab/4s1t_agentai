#!/usr/bin/env python3
"""
process_model skill handler — BPMN 2.0 XML validator.

The calling agent generates BPMN XML; this handler validates and counts elements.

Input:  {"parameters": {"process_name": "...", "bpmn_xml": "..."}}
Output: {"success": true, "result": {"valid": bool, "bpmn_xml": "...", "summary": {...}, "errors": [...]}}
"""
import json
import sys
import xml.etree.ElementTree as ET

_BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
_BPMN_NS_PREFIXES = [
    "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "http://www.omg.org/bpmn20",
    "http://schema.omg.org/spec/BPMN/2.0",
]


def _count_bpmn_elements(root: ET.Element) -> dict:
    """Count common BPMN elements (namespace-aware)."""
    def count_tag(local: str) -> int:
        total = 0
        for ns in _BPMN_NS_PREFIXES:
            total += len(root.findall(f".//{{{ns}}}{local}"))
        if total == 0:
            # Try without namespace (lax matching for generated XML)
            total = sum(
                1 for el in root.iter()
                if el.tag.split("}")[-1].lower() in (local.lower(), local)
            )
        return total

    return {
        "tasks_count": count_tag("task") + count_tag("serviceTask") + count_tag("userTask"),
        "gateways_count": (
            count_tag("exclusiveGateway") + count_tag("parallelGateway") +
            count_tag("inclusiveGateway") + count_tag("gateway")
        ),
        "events_count": (
            count_tag("startEvent") + count_tag("endEvent") +
            count_tag("intermediateThrowEvent") + count_tag("intermediateCatchEvent")
        ),
        "flows_count": count_tag("sequenceFlow"),
    }


def execute(params: dict) -> dict:
    process_name = params.get("process_name", "Process")
    bpmn_xml = params.get("bpmn_xml", "").strip()

    errors = []

    if not bpmn_xml:
        return {
            "valid": False,
            "bpmn_xml": "",
            "summary": {"process_name": process_name},
            "errors": ["bpmn_xml parameter is required and must not be empty."],
        }

    # Parse XML
    try:
        root = ET.fromstring(bpmn_xml)
    except ET.ParseError as exc:
        return {
            "valid": False,
            "bpmn_xml": bpmn_xml,
            "summary": {"process_name": process_name},
            "errors": [f"XML parse error: {exc}"],
        }

    # Check for BPMN namespace
    tag = root.tag
    has_bpmn_ns = any(ns in tag for ns in _BPMN_NS_PREFIXES)
    if not has_bpmn_ns:
        errors.append(
            "Root element does not use a recognised BPMN 2.0 namespace. "
            "Expected one of: " + ", ".join(_BPMN_NS_PREFIXES)
        )

    counts = _count_bpmn_elements(root)
    counts["process_name"] = process_name

    if counts["tasks_count"] == 0 and counts["flows_count"] == 0:
        errors.append("No tasks or sequence flows found — this may not be a valid process model.")

    # Pretty-print the XML back
    try:
        ET.indent(root, space="  ")
        formatted_xml = ET.tostring(root, encoding="unicode", xml_declaration=False)
    except Exception:
        formatted_xml = bpmn_xml

    return {
        "valid": len(errors) == 0,
        "bpmn_xml": formatted_xml,
        "summary": counts,
        "errors": errors,
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
