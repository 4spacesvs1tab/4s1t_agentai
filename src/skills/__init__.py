"""
Skills Framework — sandboxed capability units for 4S1T Agent AI.

Each skill is a directory under src/skills/ containing:
  meta.json   — machine-readable metadata (SkillMeta schema)
  handler.py  — subprocess entrypoint

Public API::

    from skills.registry import SkillRegistry
    from skills.executor import SkillExecutor, SkillScopeError
    from skills.models import SkillMeta, SkillInput, SkillOutput
"""
