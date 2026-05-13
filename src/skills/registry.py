"""
SkillRegistry — loads and indexes all skills from the skills directory.

Responsibilities:
  1. Scan src/skills/ for subdirectories that contain meta.json
  2. Validate each meta.json against SkillMeta schema
  3. Filter skills by agent_scope for each agent type
  4. Convert to OpenAI tools format for LLM injection

Usage::

    registry = SkillRegistry()
    registry.load_all()                         # call once at startup
    tools = registry.tools_for_agent("ba_agent")  # OpenAI-format list
    meta = registry.get("web_search")           # SkillMeta
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from skills.models import SkillMeta

from utils.logger import setup_logger
logger = setup_logger(__name__)

# Default skills directory: src/skills/ (same directory as this file)
_DEFAULT_SKILLS_DIR = Path(__file__).parent


class SkillRegistryError(Exception):
    """Raised when the registry encounters an unrecoverable configuration error."""


class SkillRegistry:
    """
    Loads skill metadata from the filesystem and provides filtered views
    per agent type.

    Thread-safety: read-only after `load_all()` completes — safe for
    concurrent async use without locks.
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillMeta] = {}  # name → SkillMeta

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_all(self, skills_dir: Path | str | None = None) -> None:
        """
        Scan *skills_dir* for subdirectories that contain a ``meta.json``
        and load each one.

        Args:
            skills_dir: Directory to scan. Defaults to ``src/skills/``.

        Raises:
            SkillRegistryError: If a meta.json fails schema validation.
        """
        base = Path(skills_dir) if skills_dir else _DEFAULT_SKILLS_DIR
        if not base.is_dir():
            raise SkillRegistryError(f"Skills directory not found: {base}")

        loaded = 0
        errors = 0
        for subdir in sorted(base.iterdir()):
            if not subdir.is_dir():
                continue
            meta_path = subdir / "meta.json"
            if not meta_path.exists():
                continue  # not a skill directory
            handler_path = subdir / "handler.py"
            if not handler_path.exists():
                logger.warning(
                    f"Skipping skill '{subdir.name}': meta.json present but handler.py missing"
                )
                continue  # warning only — not counted as a config error

            try:
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                meta = SkillMeta.model_validate(raw)
                meta.handler_path = str(handler_path.resolve())
                self._skills[meta.name] = meta
                loaded += 1
                logger.debug(f"Loaded skill: {meta.name} v{meta.version} scope={meta.agent_scope}")
            except (json.JSONDecodeError, ValidationError) as exc:
                logger.warning(
                    "Skipping skill '%s': invalid meta.json — %s",
                    subdir.name,
                    exc,
                )
                errors += 1

        if errors and not loaded:
            raise SkillRegistryError(
                f"No skills loaded from {base} — {errors} error(s). Check logs."
            )
        logger.info(
            f"SkillRegistry: loaded {loaded} skill(s) from {base}"
            + (f" ({errors} error(s) skipped)" if errors else "")
        )

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get(self, skill_name: str) -> SkillMeta:
        """
        Return the SkillMeta for *skill_name*.

        Raises:
            KeyError: If the skill is not registered.
        """
        if skill_name not in self._skills:
            raise KeyError(f"Skill not found: '{skill_name}'. Registered: {list(self._skills)}")
        return self._skills[skill_name]

    def tools_for_agent(
        self,
        agent_type: str,
        extra_skill_names: frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return OpenAI-format tool schemas for *agent_type* only.

        Only skills whose ``agent_scope`` includes *agent_type* are returned,
        plus any skills explicitly granted via *extra_skill_names*.
        """
        return [
            meta.to_openai_tool()
            for meta in self._skills.values()
            if meta.is_allowed_for(agent_type)
            or (extra_skill_names and meta.name in extra_skill_names)
        ]

    def skills_for_agent(self, agent_type: str) -> list[SkillMeta]:
        """Return SkillMeta objects for *agent_type* only."""
        return [
            meta for meta in self._skills.values()
            if meta.is_allowed_for(agent_type)
        ]

    def all_skill_names(self) -> list[str]:
        """Return all registered skill names."""
        return list(self._skills.keys())

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, skill_name: str) -> bool:
        return skill_name in self._skills


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_registry: SkillRegistry | None = None


def get_skill_registry(skills_dir: Path | str | None = None) -> SkillRegistry:
    """
    Return the shared SkillRegistry singleton.

    First call triggers ``load_all()``. Subsequent calls return the cached
    instance (skills are not reloaded at runtime).
    """
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        _registry.load_all(skills_dir)
    return _registry
