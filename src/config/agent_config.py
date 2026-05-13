"""
Agent and KB configuration loader.

Loads src/config/agent_config.yaml into a hierarchy of frozen dataclasses.
Defaults are identical to the previously hardcoded values — no behaviour
change on first deploy.

Usage::

    from config.agent_config import get_agent_config

    cfg = get_agent_config()
    threshold = cfg.orchestrator.handoff_token_threshold  # 32000

Important: uses stdlib logging.getLogger instead of setup_logger to avoid
the circular import chain:
  config/__init__ → nano_gpt_config → utils.logger → config.settings
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from config.loader import clear_cache as _loader_clear_cache
from config.loader import load_yaml

# Use stdlib logger — see module docstring for why.
logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "agent_config.yaml"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrchestratorConfig:
    handoff_token_threshold: int = 32_000
    trivial_max_len: int = 300
    max_subtasks: int = 10


@dataclass(frozen=True)
class PreprocessorConfig:
    longform_char_threshold: int = 8_000
    dedup_similarity_threshold: float = 0.97
    contradiction_sim_range_low: float = 0.65
    contradiction_sim_range_high: float = 0.95


@dataclass(frozen=True)
class SchedulerConfig:
    startup_delay_seconds: int = 10


@dataclass(frozen=True)
class ResolverConfig:
    min_confidence_auto: float = 0.72


@dataclass(frozen=True)
class KBConfig:
    preprocessor: PreprocessorConfig = PreprocessorConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    resolver: ResolverConfig = ResolverConfig()


@dataclass(frozen=True)
class AgentConfig:
    orchestrator: OrchestratorConfig = OrchestratorConfig()
    kb: KBConfig = KBConfig()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_agent_config() -> AgentConfig:
    """
    Load agent_config.yaml and return a cached AgentConfig.

    Falls back to all-default values if the file is missing or unreadable.
    """
    raw: dict = load_yaml(_CONFIG_PATH)
    if not raw:
        logger.warning(
            "agent_config.yaml not found or empty at %s — using built-in defaults",
            _CONFIG_PATH,
        )
        return AgentConfig()

    orch_raw: dict = raw.get("orchestrator", {})
    kb_raw: dict = raw.get("kb", {})
    pre_raw: dict = kb_raw.get("preprocessor", {})
    sched_raw: dict = kb_raw.get("scheduler", {})
    res_raw: dict = kb_raw.get("resolver", {})

    def _i(d: dict, key: str, default: int) -> int:
        return int(d[key]) if key in d else default

    def _f(d: dict, key: str, default: float) -> float:
        return float(d[key]) if key in d else default

    return AgentConfig(
        orchestrator=OrchestratorConfig(
            handoff_token_threshold=_i(orch_raw, "handoff_token_threshold", 32_000),
            trivial_max_len=_i(orch_raw, "trivial_max_len", 300),
            max_subtasks=_i(orch_raw, "max_subtasks", 10),
        ),
        kb=KBConfig(
            preprocessor=PreprocessorConfig(
                longform_char_threshold=_i(pre_raw, "longform_char_threshold", 8_000),
                dedup_similarity_threshold=_f(pre_raw, "dedup_similarity_threshold", 0.97),
                contradiction_sim_range_low=_f(pre_raw, "contradiction_sim_range_low", 0.65),
                contradiction_sim_range_high=_f(pre_raw, "contradiction_sim_range_high", 0.95),
            ),
            scheduler=SchedulerConfig(
                startup_delay_seconds=_i(sched_raw, "startup_delay_seconds", 10),
            ),
            resolver=ResolverConfig(
                min_confidence_auto=_f(res_raw, "min_confidence_auto", 0.72),
            ),
        ),
    )


def reload() -> None:
    """Clear the config cache and force a fresh load on next access. Useful in tests."""
    get_agent_config.cache_clear()
    _loader_clear_cache(_CONFIG_PATH)
