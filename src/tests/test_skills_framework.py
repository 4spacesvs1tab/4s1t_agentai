"""
Subphase 2A tests — Skills Framework core (models, registry, executor plumbing).

These tests use only in-process mocks and a tiny fixture skill to avoid any
real network calls or external dependencies.

Run with:
    pytest src/tests/test_skills_framework.py -v
"""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skills.models import SkillInput, SkillMeta, SkillOutput
from skills.registry import SkillRegistry, SkillRegistryError
from skills.executor import SkillExecutor, SkillScopeError, SkillTimeoutError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_skill_dir(tmp_path: Path) -> Path:
    """Create a minimal fixture skill directory with meta.json + handler.py."""
    skill_dir = tmp_path / "echo_skill"
    skill_dir.mkdir()

    meta = {
        "name": "echo_skill",
        "version": "1.0.0",
        "description": "Returns its input parameters unchanged.",
        "agent_scope": ["test_agent", "ba_agent"],
        "execution_mode": "subprocess",
        "timeout_seconds": 5,
        "max_memory_mb": 64,
        "network_allowed": False,
        "filesystem_access": "none",
        "secrets_required": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Text to echo"}
            },
            "required": ["message"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "echoed": {"type": "string"}
            },
        },
    }
    (skill_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    handler = textwrap.dedent("""\
        import sys, json
        def main():
            input_path, output_path = sys.argv[1], sys.argv[2]
            data = json.loads(open(input_path).read())
            params = data.get("parameters", {})
            result = {"echoed": params.get("message", "")}
            json.dump({"success": True, "result": result, "error": None, "logs": []},
                      open(output_path, "w"))
        if __name__ == "__main__":
            main()
    """)
    (skill_dir / "handler.py").write_text(handler, encoding="utf-8")
    return tmp_path


@pytest.fixture
def tmp_failing_skill_dir(tmp_path: Path) -> Path:
    """Skill whose handler always raises an exception."""
    skill_dir = tmp_path / "fail_skill"
    skill_dir.mkdir()
    meta = {
        "name": "fail_skill",
        "version": "1.0.0",
        "description": "Always fails.",
        "agent_scope": ["test_agent"],
        "execution_mode": "subprocess",
        "timeout_seconds": 5,
        "max_memory_mb": 64,
        "network_allowed": False,
        "filesystem_access": "none",
        "secrets_required": [],
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "output_schema": {},
    }
    (skill_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    handler = textwrap.dedent("""\
        import sys
        raise RuntimeError("Intentional failure in fail_skill")
    """)
    (skill_dir / "handler.py").write_text(handler, encoding="utf-8")
    return tmp_path


@pytest.fixture
def tmp_timeout_skill_dir(tmp_path: Path) -> Path:
    """Skill whose handler sleeps longer than its timeout."""
    skill_dir = tmp_path / "slow_skill"
    skill_dir.mkdir()
    meta = {
        "name": "slow_skill",
        "version": "1.0.0",
        "description": "Sleeps forever.",
        "agent_scope": ["test_agent"],
        "execution_mode": "subprocess",
        "timeout_seconds": 1,   # short timeout for fast test
        "max_memory_mb": 64,
        "network_allowed": False,
        "filesystem_access": "none",
        "secrets_required": [],
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "output_schema": {},
    }
    (skill_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    handler = textwrap.dedent("""\
        import time
        time.sleep(30)
    """)
    (skill_dir / "handler.py").write_text(handler, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# SkillMeta model tests
# ---------------------------------------------------------------------------

class TestSkillMeta:
    def test_valid_meta(self):
        meta = SkillMeta(
            name="web_search",
            version="1.0.0",
            description="Searches the web.",
            agent_scope=["ba_agent", "research_agent"],
            execution_mode="subprocess",
            timeout_seconds=15,
            max_memory_mb=128,
            network_allowed=True,
            filesystem_access="none",
            secrets_required=[],
        )
        assert meta.name == "web_search"
        assert meta.is_allowed_for("ba_agent")
        assert not meta.is_allowed_for("data_agent")

    def test_empty_agent_scope_raises(self):
        with pytest.raises(Exception):
            SkillMeta(
                name="bad_skill",
                version="1.0.0",
                description="No scope declared.",
                agent_scope=[],  # must not be empty
            )

    def test_to_openai_tool_format(self):
        meta = SkillMeta(
            name="get_current_datetime",
            version="1.0.0",
            description="Returns current UTC time.",
            agent_scope=["ba_agent"],
            input_schema={
                "type": "object",
                "properties": {"timezone": {"type": "string"}},
                "required": [],
            },
        )
        tool = meta.to_openai_tool()
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "get_current_datetime"
        assert "parameters" in tool["function"]
        assert tool["function"]["parameters"]["type"] == "object"

    def test_defaults(self):
        meta = SkillMeta(
            name="minimal",
            version="1.0.0",
            description="Minimal skill.",
            agent_scope=["test_agent"],
        )
        assert meta.execution_mode == "subprocess"
        assert meta.network_allowed is False
        assert meta.requires_approval is False
        assert meta.secrets_required == []


class TestSkillOutput:
    def test_from_error(self):
        out = SkillOutput.from_error("something went wrong")
        assert out.success is False
        assert out.error == "something went wrong"
        assert out.result is None

    def test_success_output(self):
        out = SkillOutput(success=True, result={"key": "value"})
        assert out.success is True
        assert out.result == {"key": "value"}
        assert out.error is None


# ---------------------------------------------------------------------------
# SkillRegistry tests
# ---------------------------------------------------------------------------

class TestSkillRegistry:
    def test_load_all(self, tmp_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        assert "echo_skill" in registry
        assert len(registry) == 1

    def test_get_existing_skill(self, tmp_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        meta = registry.get("echo_skill")
        assert meta.name == "echo_skill"
        assert meta.handler_path.endswith("handler.py")

    def test_get_missing_skill_raises(self, tmp_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        with pytest.raises(KeyError, match="not found"):
            registry.get("nonexistent_skill")

    def test_tools_for_agent_filters(self, tmp_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        tools_ba = registry.tools_for_agent("ba_agent")
        tools_data = registry.tools_for_agent("data_agent")
        assert len(tools_ba) == 1
        assert tools_ba[0]["function"]["name"] == "echo_skill"
        assert len(tools_data) == 0

    def test_tools_openai_format(self, tmp_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        tools = registry.tools_for_agent("ba_agent")
        assert tools[0]["type"] == "function"
        assert "name" in tools[0]["function"]
        assert "description" in tools[0]["function"]
        assert "parameters" in tools[0]["function"]

    def test_missing_handler_skipped(self, tmp_path):
        """A skill directory with meta.json but no handler.py is warned-and-skipped."""
        skill_dir = tmp_path / "incomplete_skill"
        skill_dir.mkdir()
        meta = {
            "name": "incomplete",
            "version": "1.0.0",
            "description": "Has no handler.",
            "agent_scope": ["test_agent"],
        }
        (skill_dir / "meta.json").write_text(json.dumps(meta))
        # No handler.py — should log a warning and skip (not raise)

        registry = SkillRegistry()
        registry.load_all(tmp_path)  # must not raise
        assert "incomplete" not in registry
        assert len(registry) == 0

    def test_invalid_meta_json_skipped(self, tmp_path):
        """A meta.json that fails schema validation is skipped; valid skills still load."""
        # Bad skill (missing required fields)
        bad_dir = tmp_path / "bad_skill"
        bad_dir.mkdir()
        (bad_dir / "meta.json").write_text('{"name": "bad"}')
        (bad_dir / "handler.py").write_text("pass")

        # Good skill (fully valid)
        good_dir = tmp_path / "echo_skill"
        good_dir.mkdir()
        good_meta = {
            "name": "echo_skill",
            "version": "1.0.0",
            "description": "Echo.",
            "agent_scope": ["test_agent"],
            "execution_mode": "subprocess",
            "timeout_seconds": 5,
            "max_memory_mb": 64,
            "network_allowed": False,
            "filesystem_access": "none",
            "secrets_required": [],
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "output_schema": {},
        }
        (good_dir / "meta.json").write_text(json.dumps(good_meta))
        (good_dir / "handler.py").write_text("pass")

        registry = SkillRegistry()
        registry.load_all(tmp_path)
        assert "echo_skill" in registry   # valid skill loaded
        assert "bad_skill" not in registry  # invalid skill skipped

    def test_empty_dir_no_skills(self, tmp_path):
        registry = SkillRegistry()
        # Should not raise — just no skills loaded
        registry.load_all(tmp_path)
        assert len(registry) == 0

    def test_nonexistent_dir_raises(self, tmp_path):
        registry = SkillRegistry()
        with pytest.raises(SkillRegistryError, match="not found"):
            registry.load_all(tmp_path / "does_not_exist")


# ---------------------------------------------------------------------------
# SkillExecutor tests
# ---------------------------------------------------------------------------

class TestSkillExecutor:
    def _make_executor(self, registry: SkillRegistry) -> SkillExecutor:
        return SkillExecutor(registry=registry, audit_log=None)

    @pytest.mark.asyncio
    async def test_scope_violation_raises(self, tmp_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        executor = self._make_executor(registry)
        with pytest.raises(SkillScopeError):
            await executor.execute(
                skill_name="echo_skill",
                parameters={"message": "hello"},
                calling_agent_type="data_agent",  # not in scope
            )

    @pytest.mark.asyncio
    async def test_missing_skill_raises(self, tmp_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        executor = self._make_executor(registry)
        with pytest.raises(KeyError):
            await executor.execute(
                skill_name="no_such_skill",
                parameters={},
                calling_agent_type="test_agent",
            )

    @pytest.mark.asyncio
    async def test_successful_execution(self, tmp_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        executor = self._make_executor(registry)
        output = await executor.execute(
            skill_name="echo_skill",
            parameters={"message": "hello world"},
            calling_agent_type="test_agent",
        )
        assert output.success is True
        assert output.result["echoed"] == "hello world"
        assert output.error is None

    @pytest.mark.asyncio
    async def test_failing_handler_returns_error_output(self, tmp_failing_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_failing_skill_dir)
        executor = self._make_executor(registry)
        output = await executor.execute(
            skill_name="fail_skill",
            parameters={},
            calling_agent_type="test_agent",
        )
        # Handler crashed → executor catches it and returns SkillOutput(success=False)
        assert output.success is False
        assert output.error is not None

    @pytest.mark.asyncio
    async def test_timeout_raises(self, tmp_timeout_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_timeout_skill_dir)
        executor = self._make_executor(registry)
        with pytest.raises(SkillTimeoutError):
            await executor.execute(
                skill_name="slow_skill",
                parameters={},
                calling_agent_type="test_agent",
            )

    @pytest.mark.asyncio
    async def test_secrets_not_passed_if_not_declared(self, tmp_skill_dir):
        """Secrets not in secrets_required must NOT appear in subprocess env."""
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        executor = self._make_executor(registry)

        # Pass a secret that echo_skill never declared — should be silently dropped
        output = await executor.execute(
            skill_name="echo_skill",
            parameters={"message": "test"},
            calling_agent_type="test_agent",
            secrets={"UNDECLARED_SECRET": "should_not_appear"},
        )
        assert output.success is True  # skill ran fine without the secret

    @pytest.mark.asyncio
    async def test_audit_log_called_on_success(self, tmp_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_skill_dir)
        mock_audit = AsyncMock()
        executor = SkillExecutor(registry=registry, audit_log=mock_audit)

        await executor.execute(
            skill_name="echo_skill",
            parameters={"message": "audit test"},
            calling_agent_type="test_agent",
        )
        mock_audit.log.assert_awaited_once()
        call_kwargs = mock_audit.log.await_args[1]
        assert call_kwargs["event_type"] == "SKILL_CALL"
        assert call_kwargs["actor"] == "test_agent"
        assert call_kwargs["target"] == "echo_skill"

    @pytest.mark.asyncio
    async def test_audit_log_called_on_failure(self, tmp_failing_skill_dir):
        registry = SkillRegistry()
        registry.load_all(tmp_failing_skill_dir)
        mock_audit = AsyncMock()
        executor = SkillExecutor(registry=registry, audit_log=mock_audit)

        await executor.execute(
            skill_name="fail_skill",
            parameters={},
            calling_agent_type="test_agent",
        )
        mock_audit.log.assert_awaited_once()
        call_kwargs = mock_audit.log.await_args[1]
        assert call_kwargs["event_type"] == "SKILL_ERROR"
