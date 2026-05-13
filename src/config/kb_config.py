"""
KB Domain Configuration Loader.

Single source of truth for domain metadata loaded from kb_domains.yaml.
All other modules (orchestrator, personas, brief_config, scheduler, etc.) read
domain information through this module — no module hardcodes domain names.

kb_domains.yaml is gitignored (it contains personal interests and account data).
See kb_domains.yaml.example for the full schema and documentation.

Usage::

    from config.kb_config import get_domain_ids, get_routing_rules_text

    domains = get_domain_ids()          # ["ba", "macroeconomics", ...]
    rules   = get_routing_rules_text()  # G1 routing block for orchestrator prompt
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from config.loader import load_yaml
from utils.logger import setup_logger

logger = setup_logger(__name__)

_CONFIG_PATH = Path(__file__).parent / "kb_domains.yaml"


# ---------------------------------------------------------------------------
# Raw loader
# ---------------------------------------------------------------------------

def _load_yaml() -> dict:
    """Load and cache kb_domains.yaml. Returns empty dict if file is missing."""
    data = load_yaml(_CONFIG_PATH)
    if not data and not _CONFIG_PATH.exists():
        logger.warning(
            "kb_domains.yaml not found at %s — KB domain features will be disabled. "
            "Copy kb_domains.yaml.example to kb_domains.yaml and configure your domains.",
            _CONFIG_PATH,
        )
    return data


def _domains() -> dict[str, dict]:
    """Return the raw domains mapping {id: domain_config}."""
    return _load_yaml().get("domains", {})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_domain_ids() -> list[str]:
    """
    Return the list of all configured domain IDs.

    Used by brief_config._ALL_DOMAINS, scheduler brief prompt, API validators.
    Returns an empty list if kb_domains.yaml is missing or has no domains.
    """
    return list(_domains().keys())


def get_domain_display_names() -> dict[str, str]:
    """Return {domain_id: display_name} mapping for all configured domains."""
    return {
        k: v.get("display_name", k)
        for k, v in _domains().items()
    }


def get_domains_for_ui() -> list[dict]:
    """
    Return list of domain dicts for use in Jinja2 templates.

    Each dict contains:
      - ``id``           : domain slug (e.g. 'finance')
      - ``display_name`` : human-readable label (e.g. 'Finance')
      - ``color``        : hex color string (e.g. '#f59e0b'), fallback '#607090'

    Used by web_routes.py to inject ``kb_domains`` into template contexts.
    """
    return [
        {
            "id": k,
            "display_name": v.get("display_name", k),
            "color": v.get("color", "#607090"),
        }
        for k, v in _domains().items()
    ]


def get_routing_rules_text() -> str:
    """
    Build the G1 KB routing rules block for the orchestrator decompose prompt.

    Format (one line per domain):
        - "keyword one", "keyword two", ... → domain: domain_id

    Returns empty string if no domains are configured.
    """
    lines: list[str] = []
    for domain_id, cfg in _domains().items():
        keywords: list[str] = cfg.get("keywords", [])
        if not keywords:
            continue
        kw_str = ", ".join(f'"{kw}"' for kw in keywords)
        lines.append(f'- {kw_str} → domain: {domain_id}')
    return "\n".join(lines)


def get_bootstrap_sources() -> list[dict[str, Any]]:
    """
    Return all bootstrap_sources entries across all domains.

    Used by document_loader.py --all mode.
    Each entry is the raw YAML dict with an injected 'domain' key if not present.
    """
    sources: list[dict] = []
    for domain_id, cfg in _domains().items():
        for src in cfg.get("bootstrap_sources", []):
            entry = dict(src)
            entry.setdefault("domain", domain_id)
            sources.append(entry)
    return sources


def get_domain_agent_configs() -> dict[str, dict[str, Any]]:
    """
    Return domain-agent persona configs keyed by agent_type.

    Only domains that define an agent_persona block are included.
    Used by personas.py to register domain-specific expert agents dynamically.

    Returns {agent_type: {domain_id: str, agent_persona: dict, ...}}
    """
    result: dict[str, dict] = {}
    for domain_id, cfg in _domains().items():
        persona_cfg = cfg.get("agent_persona")
        if not persona_cfg:
            continue
        agent_type = persona_cfg.get("agent_type")
        if not agent_type:
            logger.warning(
                "Domain '%s' has agent_persona block but no agent_type — skipping",
                domain_id,
            )
            continue
        result[agent_type] = {
            "domain_id": domain_id,
            "domain_display_name": cfg.get("display_name", domain_id),
            **persona_cfg,
        }
    return result


def get_nip17_send_domains() -> set[str]:
    """
    Return the set of domain IDs where nip17_send=True (default: True if unset).

    Used by brief_dispatcher to decide which briefs to deliver via NIP-17 DM.
    Domains with nip17_send=False are generated and available in the web UI but
    not pushed to the user's Nostr client.
    """
    return {
        domain_id
        for domain_id, cfg in _domains().items()
        if cfg.get("nip17_send", True)
    }


_SCHEDULE_PATH = Path(__file__).parent / "kb_schedule.yaml"


def get_brief_hour_utc() -> int:
    """
    Return the UTC hour at which daily briefs should be generated and delivered.

    Reads brief_hour_utc from kb_schedule.yaml.  Defaults to 6 (06:00 UTC) if
    the file is missing or the key is absent.
    """
    try:
        data = load_yaml(_SCHEDULE_PATH)
        hour = int(data.get("brief_hour_utc", 6))
        return max(0, min(23, hour))
    except Exception as exc:
        logger.warning("Could not read kb_schedule.yaml: %s — defaulting to 06:00 UTC", exc)
        return 6


def reload() -> None:
    """Clear the YAML cache and force a fresh load on next access. Useful in tests."""
    from config.loader import clear_cache
    clear_cache(_CONFIG_PATH)
    clear_cache(_SCHEDULE_PATH)
