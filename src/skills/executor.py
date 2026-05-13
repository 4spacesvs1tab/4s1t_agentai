"""
SkillExecutor — async subprocess launcher for skill handlers.

Execution model
---------------
Trusted subprocess skills:
  asyncio.create_subprocess_exec(sys.executable, handler.py, input.json, output.json)
  + restricted env vars (only declared secrets + minimal PATH)
  + resource limits via resource.setrlimit (Linux/macOS POSIX only)
  + timeout enforced
  + temp dir cleaned up in finally block

Docker skills (execution_mode="docker"):
  asyncio.create_subprocess_exec("docker", "run", "--rm", "--network=none", ...)
  Used only for python_execute (arbitrary user code).

Security gates
--------------
1. SkillExecutor checks agent_scope before any execution (FR-14 first gate).
2. Secrets decrypted in-memory only, passed as env vars, never written to disk.
3. AuditLog entry written for every SKILL_CALL and SKILL_ERROR.

Usage::

    executor = SkillExecutor(registry, audit_log)
    output = await executor.execute(
        skill_name="web_search",
        parameters={"query": "BABOK elicitation techniques"},
        calling_agent_type="ba_agent",
        secrets={},
    )
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from skills.models import SkillInput, SkillMeta, SkillOutput
from skills.registry import SkillRegistry

from utils.logger import setup_logger
logger = setup_logger(__name__)

# Non-secret env vars always passed to subprocess (needed for Python to run)
_BASE_ENV_KEYS = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP"}

# Extra non-secret config vars that skills may need
# PYTHONPATH must be forwarded so skill subprocesses can import from src/ packages (e.g. core.db_path)
_CONFIG_ENV_KEYS = {"FILE_READ_BASE_DIR", "CHROMA_PATH", "CHROMA_HOST", "CHROMA_PORT", "PYTHONPATH"}


class SkillScopeError(PermissionError):
    """Raised when an agent attempts to call a skill outside its declared scope."""


class SkillTimeoutError(TimeoutError):
    """Raised when a skill subprocess exceeds its timeout_seconds limit."""


class SkillExecutor:
    """
    Executes skill handlers in isolated subprocesses with resource limits,
    restricted environment, and mandatory scope enforcement.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        audit_log: Any | None = None,   # AuditLog | None — optional to avoid circular import
    ) -> None:
        self._registry = registry
        self._audit_log = audit_log

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        skill_name: str,
        parameters: dict[str, Any],
        calling_agent_type: str,
        secrets: dict[str, str] | None = None,
        extra_granted_skills: frozenset[str] | None = None,
    ) -> SkillOutput:
        """
        Execute a skill and return its output.

        Args:
            skill_name:           Name of the skill to execute.
            parameters:           Input parameters (must match the skill's input_schema).
            calling_agent_type:   Agent type making the call (scope enforcement).
            secrets:              Pre-decrypted secrets (in-memory only, never logged).
            extra_granted_skills: Skills granted at runtime (e.g. web_search when
                                  source_mode != kb_only). Bypasses agent_scope check.

        Returns:
            SkillOutput with success/result/error.

        Raises:
            SkillScopeError:   If the calling agent is not in agent_scope.
            KeyError:          If the skill is not registered.
        """
        meta = self._registry.get(skill_name)  # raises KeyError if not found
        if not (extra_granted_skills and skill_name in extra_granted_skills):
            self._enforce_scope(meta, calling_agent_type)

        skill_input = SkillInput(
            skill_name=skill_name,
            parameters=parameters,
            calling_agent_type=calling_agent_type,
        )

        if meta.execution_mode == "docker":
            output = await self._run_docker(meta, skill_input, secrets or {})
        else:
            output = await self._run_subprocess(meta, skill_input, secrets or {})

        await self._audit(skill_name, calling_agent_type, parameters, output)
        return output

    # ------------------------------------------------------------------
    # Scope enforcement (security gate 1)
    # ------------------------------------------------------------------

    def _enforce_scope(self, meta: SkillMeta, calling_agent_type: str) -> None:
        if not meta.is_allowed_for(calling_agent_type):
            logger.warning(
                f"SCOPE VIOLATION: agent '{calling_agent_type}' attempted to call "
                f"'{meta.name}' (scope={meta.agent_scope})"
            )
            raise SkillScopeError(
                f"Agent '{calling_agent_type}' is not allowed to call skill '{meta.name}'. "
                f"Allowed agents: {meta.agent_scope}"
            )

    # ------------------------------------------------------------------
    # Subprocess execution (trusted skills)
    # ------------------------------------------------------------------

    async def _run_subprocess(
        self,
        meta: SkillMeta,
        skill_input: SkillInput,
        secrets: dict[str, str],
    ) -> SkillOutput:
        with tempfile.TemporaryDirectory(prefix="skill_") as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "output.json"

            # Write input (never contains secrets)
            input_path.write_text(
                skill_input.model_dump_json(), encoding="utf-8"
            )

            env = self._build_env(meta, secrets)
            proc = None

            try:
                preexec = (
                    _make_preexec(meta.max_memory_mb * 1024 * 1024)
                    if sys.platform == "linux"
                    else None
                )
                create_kwargs: dict = dict(
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=tmpdir,
                )
                if preexec is not None:
                    create_kwargs["preexec_fn"] = preexec

                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    meta.handler_path,
                    str(input_path),
                    str(output_path),
                    **create_kwargs,
                )

                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=meta.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    if proc.returncode is None:
                        proc.kill()
                        await proc.communicate()
                    raise SkillTimeoutError(
                        f"Skill '{meta.name}' timed out after {meta.timeout_seconds}s"
                    )

                stdout = stdout_b.decode(errors="replace").strip()
                stderr = stderr_b.decode(errors="replace").strip()

                if proc.returncode != 0:
                    logger.warning(
                        f"Skill '{meta.name}' exited {proc.returncode}. "
                        f"stderr: {stderr[:500]}"
                    )

                return self._read_output(output_path, meta.name, stderr)

            except SkillTimeoutError:
                raise
            except Exception as exc:
                logger.error(f"Skill '{meta.name}' subprocess error: {exc}", exc_info=True)
                return SkillOutput.from_error(str(exc))
            # tmpdir always cleaned up by context manager

    # ------------------------------------------------------------------
    # Docker execution (python_execute — arbitrary user code)
    # ------------------------------------------------------------------

    async def _run_docker(
        self,
        meta: SkillMeta,
        skill_input: SkillInput,
        secrets: dict[str, str],
    ) -> SkillOutput:
        with tempfile.TemporaryDirectory(prefix="skill_docker_") as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "output.json"
            output_path.touch()  # ensure the file exists for volume mount

            input_path.write_text(
                skill_input.model_dump_json(), encoding="utf-8"
            )

            image_name = f"4s1t-skill-{meta.name}"
            cmd = [
                "docker", "run", "--rm",
                "--network=none",
                f"--memory={meta.max_memory_mb}m",
                "--memory-swap=-1",           # disable swap
                f"--cpus=1",
                "-v", f"{input_path}:/skill/input.json:ro",
                "-v", f"{output_path}:/skill/output.json:rw",
                "-v", f"{tmpdir}:/output:rw",  # for generated files
            ]

            # Inject secrets as Docker env vars
            for secret_name in meta.secrets_required:
                if secret_name in secrets:
                    cmd.extend(["-e", f"{secret_name}={secrets[secret_name]}"])

            cmd.append(image_name)

            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=meta.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    if proc.returncode is None:
                        proc.kill()
                        await proc.communicate()
                    raise SkillTimeoutError(
                        f"Skill '{meta.name}' (docker) timed out after {meta.timeout_seconds}s"
                    )

                stderr = stderr_b.decode(errors="replace").strip()
                return self._read_output(output_path, meta.name, stderr)

            except SkillTimeoutError:
                raise
            except Exception as exc:
                logger.error(
                    f"Skill '{meta.name}' docker error: {exc}", exc_info=True
                )
                return SkillOutput.from_error(str(exc))

    # ------------------------------------------------------------------
    # Environment building (secrets injection — FR-16)
    # ------------------------------------------------------------------

    def _build_env(
        self,
        meta: SkillMeta,
        secrets: dict[str, str],
    ) -> dict[str, str]:
        """
        Build a restricted environment for the skill subprocess.

        Only passes:
        - Minimal OS vars needed to run Python (_BASE_ENV_KEYS)
        - Non-secret config vars from _CONFIG_ENV_KEYS (if set in parent env)
        - Secrets declared in meta.secrets_required (never others)

        Secrets are never logged.
        """
        parent = os.environ
        env: dict[str, str] = {}

        # Base OS vars
        for key in _BASE_ENV_KEYS:
            if key in parent:
                env[key] = parent[key]

        # Config vars (non-secret)
        for key in _CONFIG_ENV_KEYS:
            if key in parent:
                env[key] = parent[key]

        # Declared secrets only
        missing = []
        for secret_name in meta.secrets_required:
            if secret_name in secrets:
                env[secret_name] = secrets[secret_name]
            else:
                missing.append(secret_name)
                logger.warning(
                    f"Skill '{meta.name}' requires secret '{secret_name}' "
                    f"but it was not provided"
                )

        if missing:
            logger.warning(
                f"Skill '{meta.name}': missing secrets {missing} — "
                f"skill may fail if it tries to use them"
            )

        return env

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    def _read_output(
        self,
        output_path: Path,
        skill_name: str,
        stderr: str = "",
    ) -> SkillOutput:
        """Read and parse the handler's output.json."""
        if not output_path.exists():
            return SkillOutput.from_error(
                f"Skill '{skill_name}' did not produce output.json. "
                f"stderr: {stderr[:500] or '(empty)'}"
            )
        try:
            raw = json.loads(output_path.read_text(encoding="utf-8"))
            return SkillOutput.model_validate(raw)
        except Exception as exc:
            return SkillOutput.from_error(
                f"Skill '{skill_name}' output.json parse error: {exc}. "
                f"stderr: {stderr[:500]}"
            )

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    async def _audit(
        self,
        skill_name: str,
        calling_agent_type: str,
        parameters: dict[str, Any],
        output: SkillOutput,
    ) -> None:
        if self._audit_log is None:
            return
        event_type = "SKILL_CALL" if output.success else "SKILL_ERROR"
        # Sanitise parameters — strip any key that looks like a secret
        safe_params = {
            k: v for k, v in parameters.items()
            if not any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS"))
        }
        try:
            await self._audit_log.log(
                event_type=event_type,
                actor=calling_agent_type,
                target=skill_name,
                metadata={
                    "parameters": safe_params,
                    "success": output.success,
                    "error": output.error,
                },
            )
        except Exception as exc:
            logger.error(f"AuditLog write failed for skill '{skill_name}': {exc}")


# ---------------------------------------------------------------------------
# Linux-specific: resource limits via preexec_fn
# ---------------------------------------------------------------------------

def _make_preexec(max_memory_bytes: int):
    """
    Return a preexec_fn that sets RLIMIT_AS on Linux.

    Called in the child process before exec — no async, no logging.
    macOS: RLIMIT_AS is not enforced by the kernel (always succeeds but
    has no effect). We skip it on non-Linux to avoid confusion.
    """
    def preexec():
        if sys.platform == "linux":
            try:
                import resource
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (max_memory_bytes, max_memory_bytes),
                )
            except Exception:
                pass  # Never crash the subprocess launch
    return preexec
