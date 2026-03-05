"""
Subphase 2B tests — Primitive skills (get_current_datetime, file_read).

Tests run against real handler subprocesses via SkillExecutor.

Run with:
    pytest src/tests/test_skills_primitive.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from skills.registry import SkillRegistry
from skills.executor import SkillExecutor, SkillScopeError

# Skills directory = src/skills/
_SKILLS_DIR = Path(__file__).parent.parent / "skills"


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.load_all(_SKILLS_DIR)
    return reg


@pytest.fixture(scope="module")
def executor(registry: SkillRegistry) -> SkillExecutor:
    return SkillExecutor(registry=registry, audit_log=None)


# ---------------------------------------------------------------------------
# get_current_datetime
# ---------------------------------------------------------------------------

class TestGetCurrentDatetime:
    @pytest.mark.asyncio
    async def test_returns_iso_timestamp(self, executor):
        output = await executor.execute(
            skill_name="get_current_datetime",
            parameters={},
            calling_agent_type="ba_agent",
        )
        assert output.success is True
        assert "iso" in output.result
        assert "unix" in output.result
        assert "2026" in output.result["iso"] or "T" in output.result["iso"]

    @pytest.mark.asyncio
    async def test_unix_timestamp_is_integer(self, executor):
        output = await executor.execute(
            skill_name="get_current_datetime",
            parameters={},
            calling_agent_type="research_agent",
        )
        assert isinstance(output.result["unix"], int)
        assert output.result["unix"] > 1_700_000_000  # sanity check (post-2023)

    @pytest.mark.asyncio
    async def test_custom_format(self, executor):
        output = await executor.execute(
            skill_name="get_current_datetime",
            parameters={"format": "%Y-%m-%d"},
            calling_agent_type="data_agent",
        )
        assert output.success is True
        formatted = output.result["formatted"]
        assert len(formatted) == 10  # YYYY-MM-DD
        assert formatted[4] == "-" and formatted[7] == "-"

    @pytest.mark.asyncio
    async def test_scope_blocks_unknown_agent(self, executor):
        with pytest.raises(SkillScopeError):
            await executor.execute(
                skill_name="get_current_datetime",
                parameters={},
                calling_agent_type="unknown_agent_type",
            )


# ---------------------------------------------------------------------------
# file_read
# ---------------------------------------------------------------------------

class TestFileRead:
    @pytest.fixture
    def sandbox_dir(self, tmp_path: Path) -> Path:
        """Create a temporary sandbox with a test file."""
        (tmp_path / "hello.txt").write_text("Hello, Skills!", encoding="utf-8")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "nested.txt").write_text("nested content")
        return tmp_path

    @pytest.mark.asyncio
    async def test_reads_file_in_sandbox(self, executor, sandbox_dir):
        output = await executor.execute(
            skill_name="file_read",
            parameters={"path": "hello.txt"},
            calling_agent_type="ba_agent",
            secrets={},
        )
        # Override FILE_READ_BASE_DIR via env — executor passes _CONFIG_ENV_KEYS
        # We test handler.py directly here to avoid needing global env set
        import json, subprocess, sys, tempfile
        from pathlib import Path

        handler = _SKILLS_DIR / "file_read" / "handler.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            inp = Path(tmpdir) / "input.json"
            out = Path(tmpdir) / "output.json"
            inp.write_text(json.dumps({
                "skill_name": "file_read",
                "parameters": {"path": "hello.txt"},
                "calling_agent_type": "ba_agent",
            }))
            env = {**os.environ, "FILE_READ_BASE_DIR": str(sandbox_dir)}
            result = subprocess.run(
                [sys.executable, str(handler), str(inp), str(out)],
                env=env,
                capture_output=True,
                text=True,
            )
            parsed = json.loads(out.read_text())
        assert parsed["success"] is True
        assert parsed["result"]["content"] == "Hello, Skills!"
        assert parsed["result"]["size_bytes"] == 14
        assert parsed["result"]["truncated"] is False

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, sandbox_dir):
        """Path traversal outside sandbox must be blocked."""
        import json, subprocess, sys, tempfile
        handler = _SKILLS_DIR / "file_read" / "handler.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            inp = Path(tmpdir) / "input.json"
            out = Path(tmpdir) / "output.json"
            inp.write_text(json.dumps({
                "skill_name": "file_read",
                "parameters": {"path": "../../../etc/passwd"},
                "calling_agent_type": "ba_agent",
            }))
            env = {**os.environ, "FILE_READ_BASE_DIR": str(sandbox_dir)}
            subprocess.run(
                [sys.executable, str(handler), str(inp), str(out)],
                env=env,
                capture_output=True,
            )
            parsed = json.loads(out.read_text())
        assert parsed["success"] is False
        assert "traversal" in parsed["error"].lower() or "denied" in parsed["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(self, sandbox_dir):
        import json, subprocess, sys, tempfile
        handler = _SKILLS_DIR / "file_read" / "handler.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            inp = Path(tmpdir) / "input.json"
            out = Path(tmpdir) / "output.json"
            inp.write_text(json.dumps({
                "skill_name": "file_read",
                "parameters": {"path": "does_not_exist.txt"},
                "calling_agent_type": "ba_agent",
            }))
            env = {**os.environ, "FILE_READ_BASE_DIR": str(sandbox_dir)}
            subprocess.run(
                [sys.executable, str(handler), str(inp), str(out)],
                env=env,
                capture_output=True,
            )
            parsed = json.loads(out.read_text())
        assert parsed["success"] is False
        assert parsed["error"] is not None

    @pytest.mark.asyncio
    async def test_reads_nested_file(self, sandbox_dir):
        import json, subprocess, sys, tempfile
        handler = _SKILLS_DIR / "file_read" / "handler.py"
        with tempfile.TemporaryDirectory() as tmpdir:
            inp = Path(tmpdir) / "input.json"
            out = Path(tmpdir) / "output.json"
            inp.write_text(json.dumps({
                "skill_name": "file_read",
                "parameters": {"path": "subdir/nested.txt"},
                "calling_agent_type": "ba_agent",
            }))
            env = {**os.environ, "FILE_READ_BASE_DIR": str(sandbox_dir)}
            subprocess.run(
                [sys.executable, str(handler), str(inp), str(out)],
                env=env,
                capture_output=True,
            )
            parsed = json.loads(out.read_text())
        assert parsed["success"] is True
        assert "nested content" in parsed["result"]["content"]
