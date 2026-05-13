"""
Shared YAML loader for src/config/*.

Single entry point for all config YAML loading across the agent system.
Callers pass a Path; this module handles caching, error normalisation, and
import-safety. Schema mapping (dicts → dataclasses) stays in each caller.

Caching strategy
----------------
reload_on_change=False (default)
    Result is cached permanently by resolved path.  Useful for configs that
    never change at runtime.  Call ``clear_cache()`` in tests to reset.

reload_on_change=True
    Result is cached by (path, mtime).  The file is re-read automatically
    when its modification time changes — useful for hot-reload scenarios.

Missing files
-------------
Returns ``{}`` when the file does not exist.  Many config files are
optional (e.g. kb_domains.yaml is gitignored).  Callers that require the
file to be present should check the return value themselves.

Parse errors
------------
Always raises ``ConfigError`` (from ``core.exceptions``) on YAML syntax
errors or permission denied.  Parse failures must surface — never silently
return a default and hide a broken config file.

Usage::

    from config.loader import load_yaml
    from pathlib import Path

    data = load_yaml(Path(__file__).parent / "my_config.yaml")
    # data is a plain dict; caller maps it to its own schema
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.exceptions import ConfigError

# Use stdlib logger — avoids the circular import chain:
#   config/__init__ → nano_gpt_config → utils.logger → config.settings
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal caches
# ---------------------------------------------------------------------------

# Permanent cache: path → dict  (reload_on_change=False)
_permanent_cache: dict[Path, dict] = {}

# Mtime-aware cache: path → (mtime, dict)  (reload_on_change=True)
_mtime_cache: dict[Path, tuple[float, dict]] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_yaml(path: Path, *, reload_on_change: bool = False) -> dict:
    """
    Load and parse *path* as YAML, returning the top-level dict.

    Parameters
    ----------
    path:
        Absolute or relative path to the YAML file.
    reload_on_change:
        When ``False`` (default), result is cached permanently by path.
        When ``True``, result is cached by (path, mtime); the file is
        re-read when its mtime changes.

    Returns
    -------
    dict
        Parsed YAML content.  Returns ``{}`` if the file does not exist.

    Raises
    ------
    ConfigError
        If the file exists but cannot be parsed (YAML syntax error, or an
        OS-level read error such as permission denied).
    """
    path = Path(path).resolve()

    if not reload_on_change:
        if path in _permanent_cache:
            return _permanent_cache[path]
        result = _read_yaml(path)
        _permanent_cache[path] = result
        return result

    # reload_on_change=True: use mtime as secondary cache key
    try:
        current_mtime = path.stat().st_mtime
    except FileNotFoundError:
        return {}

    cached = _mtime_cache.get(path)
    if cached is not None and cached[0] == current_mtime:
        return cached[1]

    result = _read_yaml(path)
    _mtime_cache[path] = (current_mtime, result)
    return result


def clear_cache(path: Path | None = None) -> None:
    """
    Clear the loader cache.

    Parameters
    ----------
    path:
        If given, removes only the cache entries for that specific path.
        If ``None``, clears all cached entries.

    Use this in tests after patching or replacing config files, or to
    force a fresh load on the next ``load_yaml()`` call.
    """
    if path is None:
        _permanent_cache.clear()
        _mtime_cache.clear()
    else:
        resolved = Path(path).resolve()
        _permanent_cache.pop(resolved, None)
        _mtime_cache.pop(resolved, None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_yaml(path: Path) -> dict:
    """
    Read and parse *path*.  Returns ``{}`` for missing files; raises
    ``ConfigError`` for parse errors or unreadable files.
    """
    if not path.exists():
        return {}

    try:
        import yaml  # pyyaml — optional at import time
    except ImportError as exc:
        raise ConfigError(
            f"pyyaml is not installed — cannot load {path}. "
            "Install it: pip install pyyaml"
        ) from exc

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML at {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc

    return data if isinstance(data, dict) else {}
