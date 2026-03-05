"""
Subphase 2D tests — BA skills (stakeholder_analysis, process_model,
gap_analysis, requirements_template, babok_lookup).

babok_lookup is tested for graceful degradation (no BABOK data indexed yet).
All formatter skills run without external dependencies.

Run with:
    pytest src/tests/test_skills_ba.py -v
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SKILLS_DIR = Path(__file__).parent.parent / "skills"


def _run_handler(skill_name: str, params: dict) -> dict:
    handler = _SKILLS_DIR / skill_name / "handler.py"
    import os, tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        inp = Path(tmpdir) / "input.json"
        out = Path(tmpdir) / "output.json"
        inp.write_text(json.dumps({
            "skill_name": skill_name,
            "parameters": params,
            "calling_agent_type": "ba_agent",
        }))
        subprocess.run(
            [sys.executable, str(handler), str(inp), str(out)],
            env={**os.environ},
            capture_output=True,
        )
        return json.loads(out.read_text())


# ---------------------------------------------------------------------------
# stakeholder_analysis
# ---------------------------------------------------------------------------

class TestStakeholderAnalysis:
    def test_builds_raci_markdown(self):
        result = _run_handler("stakeholder_analysis", {
            "process_name": "Order Fulfillment",
            "roles": ["Operations Manager", "Warehouse Team", "Finance"],
            "tasks": ["Receive order", "Pick items", "Invoice customer"],
            "assignments": [
                {"task": "Receive order",    "R": "Operations Manager", "A": "Operations Manager", "C": [], "I": ["Finance"]},
                {"task": "Pick items",       "R": "Warehouse Team",     "A": "Operations Manager", "C": [], "I": []},
                {"task": "Invoice customer", "R": "Finance",            "A": "Finance",             "C": ["Operations Manager"], "I": []},
            ],
        })
        assert result["success"] is True
        md = result["result"]["raci_markdown"]
        assert "Order Fulfillment" in md
        assert "Warehouse Team" in md
        assert "**R**" in md
        assert "**A**" in md
        assert "Legend:" in md

    def test_returns_raci_json(self):
        result = _run_handler("stakeholder_analysis", {
            "process_name": "Test Process",
            "roles": ["Manager"],
            "tasks": ["Task 1"],
            "assignments": [{"task": "Task 1", "R": "Manager", "A": "Manager"}],
        })
        assert result["result"]["raci_json"]["process_name"] == "Test Process"

    def test_warns_on_missing_role(self):
        result = _run_handler("stakeholder_analysis", {
            "process_name": "Test",
            "roles": ["Manager"],
            "tasks": ["Task 1"],
            "assignments": [{"task": "Task 1", "R": "Unknown Role", "A": "Manager"}],
        })
        assert result["success"] is True
        assert len(result["result"]["validation_warnings"]) > 0

    def test_empty_assignments_succeeds_with_warning(self):
        result = _run_handler("stakeholder_analysis", {
            "process_name": "Empty",
            "roles": [],
            "tasks": [],
            "assignments": [],
        })
        assert result["success"] is True
        assert len(result["result"]["validation_warnings"]) > 0


# ---------------------------------------------------------------------------
# process_model
# ---------------------------------------------------------------------------

_VALID_BPMN = """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL">
  <bpmn:process id="p1" name="Test Process">
    <bpmn:startEvent id="start"/>
    <bpmn:task id="t1" name="Do Work"/>
    <bpmn:endEvent id="end"/>
    <bpmn:sequenceFlow id="sf1" sourceRef="start" targetRef="t1"/>
    <bpmn:sequenceFlow id="sf2" sourceRef="t1" targetRef="end"/>
  </bpmn:process>
</bpmn:definitions>"""

class TestProcessModel:
    def test_valid_bpmn_accepted(self):
        result = _run_handler("process_model", {
            "process_name": "Test Process",
            "bpmn_xml": _VALID_BPMN,
        })
        assert result["success"] is True
        assert result["result"]["valid"] is True
        assert result["result"]["summary"]["tasks_count"] >= 1
        assert result["result"]["summary"]["flows_count"] >= 1

    def test_malformed_xml_rejected(self):
        result = _run_handler("process_model", {
            "process_name": "Bad Process",
            "bpmn_xml": "<not valid xml <<<<<",
        })
        assert result["success"] is True  # handler succeeded
        assert result["result"]["valid"] is False
        assert len(result["result"]["errors"]) > 0

    def test_non_bpmn_xml_warns(self):
        result = _run_handler("process_model", {
            "process_name": "Not BPMN",
            "bpmn_xml": "<root><element/></root>",
        })
        assert result["success"] is True
        assert result["result"]["valid"] is False

    def test_empty_bpmn_returns_error(self):
        result = _run_handler("process_model", {
            "process_name": "Empty",
            "bpmn_xml": "",
        })
        assert result["success"] is True
        assert result["result"]["valid"] is False
        assert len(result["result"]["errors"]) > 0


# ---------------------------------------------------------------------------
# gap_analysis
# ---------------------------------------------------------------------------

class TestGapAnalysis:
    def test_formats_gap_report(self):
        result = _run_handler("gap_analysis", {
            "domain": "Order Management",
            "as_is": [{"capability": "Manual order entry", "maturity": "Initial"}],
            "to_be": [{"capability": "Automated order processing", "target_maturity": "Optimizing"}],
            "gaps": [
                {
                    "capability": "Order processing",
                    "gap_description": "Currently manual, target is automated",
                    "priority": "high",
                    "recommended_action": "Implement order management system",
                }
            ],
        })
        assert result["success"] is True
        md = result["result"]["gap_report_markdown"]
        assert "Order Management" in md
        assert "High" in md or "high" in md
        assert result["result"]["high_priority_count"] == 1
        assert result["result"]["total_gaps"] == 1

    def test_counts_priorities_correctly(self):
        result = _run_handler("gap_analysis", {
            "domain": "Test",
            "as_is": [], "to_be": [],
            "gaps": [
                {"capability": "A", "gap_description": "G1", "priority": "high"},
                {"capability": "B", "gap_description": "G2", "priority": "high"},
                {"capability": "C", "gap_description": "G3", "priority": "medium"},
                {"capability": "D", "gap_description": "G4", "priority": "low"},
            ],
        })
        assert result["result"]["high_priority_count"] == 2
        assert result["result"]["total_gaps"] == 4

    def test_empty_gaps_succeeds(self):
        result = _run_handler("gap_analysis", {
            "domain": "Test", "as_is": [], "to_be": [], "gaps": [],
        })
        assert result["success"] is True
        assert result["result"]["total_gaps"] == 0


# ---------------------------------------------------------------------------
# requirements_template
# ---------------------------------------------------------------------------

class TestRequirementsTemplate:
    @pytest.mark.parametrize("artifact_type", [
        "use_case", "brd", "urd", "stakeholder_register", "business_case", "process_narrative"
    ])
    def test_all_artifact_types(self, artifact_type):
        result = _run_handler("requirements_template", {
            "artifact_type": artifact_type,
            "project_name": "Test Project",
        })
        assert result["success"] is True
        assert "template_markdown" in result["result"]
        assert "sections" in result["result"]
        assert "guidance" in result["result"]
        assert len(result["result"]["template_markdown"]) > 100
        assert "Test Project" in result["result"]["template_markdown"]

    def test_invalid_artifact_type(self):
        result = _run_handler("requirements_template", {
            "artifact_type": "invalid_type",
        })
        assert result["success"] is False
        assert "invalid_type" in result["error"]

    def test_use_case_has_expected_sections(self):
        result = _run_handler("requirements_template", {
            "artifact_type": "use_case",
            "project_name": "Login Flow",
        })
        assert "Login Flow" in result["result"]["template_markdown"]
        assert "Main Success Scenario" in result["result"]["template_markdown"]
        assert "Preconditions" in result["result"]["template_markdown"]


# ---------------------------------------------------------------------------
# babok_lookup — graceful degradation (no data indexed yet)
# ---------------------------------------------------------------------------

class TestBabokLookup:
    def test_returns_empty_when_collection_missing(self, tmp_path):
        """Without BABOK ingestion, should return empty result with a helpful note."""
        import os, tempfile
        handler = _SKILLS_DIR / "babok_lookup" / "handler.py"

        # Create an empty ChromaDB (no babok_v3 collection)
        try:
            import chromadb as _chroma
            _chroma.PersistentClient(path=str(tmp_path / "chroma"))
            chroma_available = True
        except ImportError:
            chroma_available = False

        if not chroma_available:
            pytest.skip("chromadb not installed in this environment")

        with tempfile.TemporaryDirectory() as tmpdir:
            inp = Path(tmpdir) / "input.json"
            out = Path(tmpdir) / "output.json"
            inp.write_text(json.dumps({
                "skill_name": "babok_lookup",
                "parameters": {"query": "elicitation techniques"},
                "calling_agent_type": "ba_agent",
            }))
            env = {**os.environ, "CHROMA_PATH": str(tmp_path / "chroma")}
            subprocess.run(
                [sys.executable, str(handler), str(inp), str(out)],
                env=env, capture_output=True,
            )
            result = json.loads(out.read_text())

        assert result["success"] is True
        assert result["result"]["section_count"] == 0
        assert "_note" in result["result"]  # helpful note about running ingestion
