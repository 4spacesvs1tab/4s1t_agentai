"""
Privacy configuration loader for 4S1T Agent AI.

Loads privacy.yaml and exposes a PrivacyConfig dataclass used by:
  - ApiClient  (Tor proxy, header stripping, jitter)
  - BaseAgent  (PII detection gate, prompt obfuscation)
  - NIP17 approval flows (Tor fallback, PII multichoice)

Usage::

    from config.privacy_config import get_privacy_config

    cfg = get_privacy_config()
    if cfg.enabled and cfg.tor_proxy:
        # configure Tor transport
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config.loader import load_yaml
from utils.logger import setup_logger

logger = setup_logger(__name__)

_PRIVACY_YAML = Path(__file__).parent / "privacy.yaml"


@dataclass
class PrivacyConfig:
    """Runtime privacy settings loaded from privacy.yaml."""

    # Master switch
    enabled: bool = True

    # Tor
    tor_proxy: str = ""                 # e.g. "socks5://127.0.0.1:9050"
    tor_fallback: str = "kill"          # "kill" | "approve"
    tor_approval_timeout: float = 120.0

    # Header stripping
    strip_sdk_headers: bool = True

    # System prompt obfuscation
    prompt_obfuscation: bool = True

    # PII
    pii_scrubbing_default: bool = False
    pii_tier1_always_alert: bool = True
    pii_tier2_alert_threshold: int = 3
    pii_approval_timeout: float = 120.0

    # Timing jitter [min_ms, max_ms]
    request_jitter_ms: list[int] = field(default_factory=lambda: [50, 300])

    @property
    def tor_enabled(self) -> bool:
        return self.enabled and bool(self.tor_proxy)

    @property
    def jitter_min_ms(self) -> int:
        return self.request_jitter_ms[0] if self.request_jitter_ms else 50

    @property
    def jitter_max_ms(self) -> int:
        return self.request_jitter_ms[1] if len(self.request_jitter_ms) > 1 else 300


def get_privacy_config(yaml_path: Optional[Path] = None) -> PrivacyConfig:
    """
    Load and return a PrivacyConfig from privacy.yaml.

    Falls back to safe defaults if the file is missing or malformed.
    This function is stateless — no module-level singleton — so callers can
    override the path freely in tests.
    """
    path = yaml_path or _PRIVACY_YAML

    data = load_yaml(path)
    if not data:
        logger.warning("privacy.yaml not found or empty at %s — using defaults", path)
        return PrivacyConfig()

    raw: dict = data.get("privacy", {})
    if not isinstance(raw, dict):
        logger.warning("privacy.yaml: 'privacy' key is not a dict — using defaults")
        return PrivacyConfig()

    jitter_raw = raw.get("request_jitter_ms", [50, 300])
    if isinstance(jitter_raw, list) and len(jitter_raw) >= 2:
        jitter = [int(jitter_raw[0]), int(jitter_raw[1])]
    else:
        jitter = [50, 300]

    cfg = PrivacyConfig(
        enabled=bool(raw.get("enabled", True)),
        tor_proxy=str(raw.get("tor_proxy", "")),
        tor_fallback=str(raw.get("tor_fallback", "kill")),
        tor_approval_timeout=float(raw.get("tor_approval_timeout", 120)),
        strip_sdk_headers=bool(raw.get("strip_sdk_headers", True)),
        prompt_obfuscation=bool(raw.get("prompt_obfuscation", True)),
        pii_scrubbing_default=bool(raw.get("pii_scrubbing_default", False)),
        pii_tier1_always_alert=bool(raw.get("pii_tier1_always_alert", True)),
        pii_tier2_alert_threshold=int(raw.get("pii_tier2_alert_threshold", 3)),
        pii_approval_timeout=float(raw.get("pii_approval_timeout", 120)),
        request_jitter_ms=jitter,
    )

    logger.info(
        f"PrivacyConfig loaded: enabled={cfg.enabled} tor={bool(cfg.tor_proxy)} "
        f"strip_headers={cfg.strip_sdk_headers} obfuscation={cfg.prompt_obfuscation} "
        f"pii_default={cfg.pii_scrubbing_default}"
    )
    return cfg
