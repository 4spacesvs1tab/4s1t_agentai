"""
Subphase 2C tests — Network + Search skills (web_search, knowledge_base_search).

web_search tests run against real DuckDuckGo API — skip in offline environments.
knowledge_base_search tests use an in-process ChromaDB with synthetic data.

Run with:
    pytest src/tests/test_skills_network.py -v
    pytest src/tests/test_skills_network.py -v -m "not network"   # skip live calls
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import importlib.util

import pytest

from skills.registry import SkillRegistry
from skills.executor import SkillExecutor

_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Skip live network tests unless SKILLS_TEST_NETWORK=1 is set
_network_enabled = os.environ.get("SKILLS_TEST_NETWORK", "0") == "1"
skip_offline = pytest.mark.skipif(
    not _network_enabled,
    reason="Live network test skipped. Set SKILLS_TEST_NETWORK=1 to run.",
)


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_all(_SKILLS_DIR)
    return reg


@pytest.fixture(scope="module")
def executor(registry: SkillRegistry) -> SkillExecutor:
    return SkillExecutor(registry=registry, audit_log=None)


# ---------------------------------------------------------------------------
# web_search (live network calls)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@skip_offline
async def test_web_search_returns_results(executor):
    """Live DuckDuckGo search — skipped in offline environments."""
    output = await executor.execute(
        skill_name="web_search",
        parameters={"query": "Python asyncio tutorial", "limit": 3},
        calling_agent_type="research_agent",
    )
    assert output.success is True
    assert "results" in output.result
    assert output.result["result_count"] >= 1
    first = output.result["results"][0]
    assert "title" in first
    assert "url" in first
    assert "snippet" in first
    assert first["url"].startswith("http")


@pytest.mark.asyncio
@skip_offline
async def test_web_search_respects_limit(executor):
    output = await executor.execute(
        skill_name="web_search",
        parameters={"query": "business analysis BABOK", "limit": 2},
        calling_agent_type="ba_agent",
    )
    assert output.success is True
    assert output.result["result_count"] <= 2


def test_web_search_handler_empty_query():
    """Empty query should return error, not crash."""
    handler = _SKILLS_DIR / "web_search" / "handler.py"
    with tempfile.TemporaryDirectory() as tmpdir:
        inp = Path(tmpdir) / "input.json"
        out = Path(tmpdir) / "output.json"
        inp.write_text(json.dumps({
            "skill_name": "web_search",
            "parameters": {"query": ""},
            "calling_agent_type": "research_agent",
        }))
        subprocess.run(
            [sys.executable, str(handler), str(inp), str(out)],
            env={**os.environ},
            capture_output=True,
        )
        parsed = json.loads(out.read_text())
    assert parsed["success"] is False
    assert parsed["error"] is not None


# ---------------------------------------------------------------------------
# knowledge_base_search — uses in-process ChromaDB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def chroma_with_data(tmp_path: Path):
    """Create an in-process ChromaDB with a small synthetic collection."""
    chromadb = pytest.importorskip("chromadb", reason=(
        "chromadb not installed (Python 3.14 incompatibility); "
        "handler tested on Python 3.9+ production target"
    ))

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    col = client.create_collection("test_knowledge")
    col.add(
        ids=["doc1", "doc2", "doc3"],
        documents=[
            "Business analysis is the practice of identifying business needs.",
            "Requirements elicitation involves gathering stakeholder needs.",
            "BABOK defines the Body of Knowledge for business analysis.",
        ],
        metadatas=[
            {"source": "test_doc", "chapter": "1"},
            {"source": "test_doc", "chapter": "2"},
            {"source": "test_doc", "chapter": "3"},
        ],
    )
    return tmp_path / "chroma"


def _run_kb_handler(chroma_path: Path, params: dict) -> dict:
    handler = _SKILLS_DIR / "knowledge_base_search" / "handler.py"
    with tempfile.TemporaryDirectory() as tmpdir:
        inp = Path(tmpdir) / "input.json"
        out = Path(tmpdir) / "output.json"
        inp.write_text(json.dumps({
            "skill_name": "knowledge_base_search",
            "parameters": params,
            "calling_agent_type": "research_agent",
        }))
        env = {**os.environ, "CHROMA_PATH": str(chroma_path)}
        subprocess.run(
            [sys.executable, str(handler), str(inp), str(out)],
            env=env,
            capture_output=True,
        )
        return json.loads(out.read_text())


def test_kb_search_returns_chunks(chroma_with_data):
    result = _run_kb_handler(chroma_with_data, {"query": "business analysis", "limit": 3})
    assert result["success"] is True
    assert result["result"]["chunk_count"] > 0
    assert len(result["result"]["chunks"]) > 0
    chunk = result["result"]["chunks"][0]
    assert "text" in chunk
    assert "collection" in chunk
    assert "distance" in chunk


def test_kb_search_specific_collection(chroma_with_data):
    result = _run_kb_handler(
        chroma_with_data,
        {"query": "requirements", "collection": "test_knowledge", "limit": 2},
    )
    assert result["success"] is True
    for chunk in result["result"]["chunks"]:
        assert chunk["collection"] == "test_knowledge"


def test_kb_search_invalid_collection_returns_error(chroma_with_data):
    result = _run_kb_handler(
        chroma_with_data,
        {"query": "test", "collection": "nonexistent_collection"},
    )
    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_kb_search_empty_db_returns_empty(tmp_path):
    """An empty ChromaDB should return empty results without error."""
    chromadb = pytest.importorskip("chromadb")
    chromadb.PersistentClient(path=str(tmp_path / "empty_chroma"))

    result = _run_kb_handler(tmp_path / "empty_chroma", {"query": "anything"})
    assert result["success"] is True
    assert result["result"]["chunk_count"] == 0
    assert result["result"]["chunks"] == []


def test_kb_search_respects_limit(chroma_with_data):
    result = _run_kb_handler(chroma_with_data, {"query": "analysis", "limit": 1})
    assert result["success"] is True
    assert len(result["result"]["chunks"]) <= 1
