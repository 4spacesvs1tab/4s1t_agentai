"""
i18n (internationalisation) module for 4S1T Agent AI.

Usage (Python):
    from i18n import get_t, LANGUAGES
    t = get_t("pl")
    label = t("nav.login")   # → "logowanie"

Usage (Jinja2 templates — pass t=get_t(lang) in context):
    {{ t('nav.login') }}

Adding a new language:
  1. Copy src/i18n/en.yml → src/i18n/<code>.yml
  2. Translate all values (keep all keys identical)
  3. Add the language to LANGUAGES below
  4. Add keyword set in _keywords.py
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Callable

import yaml

_I18N_DIR = os.path.dirname(__file__)

# Supported languages: code → display name shown in the UI selector
LANGUAGES: dict[str, str] = {
    "en": "English",
    "pl": "Polski",
}

DEFAULT_LANG = "en"


# ---------------------------------------------------------------------------
# Internal loader (cached per language code)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _load(lang: str) -> dict:
    """Load and cache a language YAML file. Returns {} on any error."""
    path = os.path.join(_I18N_DIR, f"{lang}.yml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        # YAML root key is the language code (e.g. "en:" or "pl:")
        return data.get(lang, data)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _get_nested(d: dict, keys: list[str]) -> Any:
    """Traverse a nested dict by a list of keys. Returns None if any key is missing."""
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_t(lang: str) -> Callable[[str], str]:
    """
    Return a translation function bound to *lang*.

    The returned callable accepts dot-separated keys and returns the
    translated string, falling back to English, then to the key itself.

    Args:
        lang: Language code (e.g. "en", "pl").  Unknown codes fall back to "en".

    Returns:
        A ``t(key: str) -> str`` callable suitable for injection into Jinja2
        template contexts.
    """
    if lang not in LANGUAGES:
        lang = DEFAULT_LANG

    translations = _load(lang)
    fallback = _load(DEFAULT_LANG)

    def t(key: str) -> str:
        parts = key.split(".")
        val = _get_nested(translations, parts)
        if isinstance(val, str):
            return val
        # Fall back to English
        val = _get_nested(fallback, parts)
        if isinstance(val, str):
            return val
        return key  # Last resort: return the raw key

    return t


def translate(key: str, lang: str = DEFAULT_LANG) -> str:
    """Convenience one-off translation without creating a closure."""
    return get_t(lang)(key)
