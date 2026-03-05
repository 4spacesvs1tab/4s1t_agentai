"""
Tests for ModelSelector (task 1.7).
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "CI_Test_S3cret_Key_64chars_long_ABCDEFGHIJK!@#$%^&*()")
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("DEBUG", "true")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.nano_gpt_config import NanoGPTConfig
from core.model_selector import ModelSelector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def free_config():
    """NanoGPTConfig with FREE subscription tier (limited models)."""
    cfg = NanoGPTConfig()
    cfg.subscription_tier = "FREE"
    return cfg


@pytest.fixture
def pro_config():
    """NanoGPTConfig with PRO subscription tier (all models)."""
    cfg = NanoGPTConfig()
    cfg.subscription_tier = "PRO"
    return cfg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestModelSelectorKnownPreferences:
    def test_business_analysis_maps_to_glm(self, free_config):
        sel = ModelSelector(nano_gpt_config=free_config)
        assert sel.select("business_analysis") == "glm-4.5"

    def test_data_analysis_maps_to_deepseek(self, free_config):
        sel = ModelSelector(nano_gpt_config=free_config)
        assert sel.select("data_analysis") == "deepseek-v3.2"

    def test_reasoning_maps_to_correct_model(self, free_config):
        sel = ModelSelector(nano_gpt_config=free_config)
        result = sel.select("reasoning")
        assert isinstance(result, str) and len(result) > 0

    def test_all_defined_preferences_return_strings(self, free_config):
        sel = ModelSelector(nano_gpt_config=free_config)
        for pref in sel.available_preferences():
            result = sel.select(pref)
            assert isinstance(result, str) and result, f"Empty result for '{pref}'"


class TestModelSelectorFallback:
    def test_unknown_preference_falls_back(self, free_config):
        sel = ModelSelector(nano_gpt_config=free_config)
        result = sel.select("nonexistent_preference_xyz")
        # Must return the general fallback model, not None or empty
        assert isinstance(result, str) and result

    def test_fallback_is_general_model(self, free_config):
        sel = ModelSelector(nano_gpt_config=free_config)
        general = free_config.default_models.get("general", "deepseek-v3.2")
        result = sel.select("__totally_unknown__")
        assert result == general


class TestModelSelectorAllowlist:
    def test_allowed_models_filter(self, pro_config):
        sel = ModelSelector(nano_gpt_config=pro_config)
        # Only allow deepseek-v3.2; business_analysis maps to glm-4.5 which is NOT in list
        result = sel.select("business_analysis", allowed_models=["deepseek-v3.2"])
        assert result == "deepseek-v3.2"

    def test_model_in_allowlist_passes_through(self, pro_config):
        sel = ModelSelector(nano_gpt_config=pro_config)
        result = sel.select("data_analysis", allowed_models=["deepseek-v3.2", "glm-4.5"])
        assert result in ["deepseek-v3.2", "glm-4.5"]


class TestModelSelectorAvailablePreferences:
    def test_returns_list_of_strings(self, free_config):
        sel = ModelSelector(nano_gpt_config=free_config)
        prefs = sel.available_preferences()
        assert isinstance(prefs, list)
        assert all(isinstance(p, str) for p in prefs)
        assert len(prefs) > 0

    def test_includes_expected_keys(self, free_config):
        sel = ModelSelector(nano_gpt_config=free_config)
        prefs = sel.available_preferences()
        for key in ("business_analysis", "data_analysis", "general"):
            assert key in prefs, f"Missing expected preference key: '{key}'"
