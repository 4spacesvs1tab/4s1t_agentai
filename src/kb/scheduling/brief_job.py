"""
KB scheduling — brief generation job.

Runs kb_monitor_agent directly (without orchestrator decomposition) to
generate per-domain intelligence briefs for a given user.

Phase KB-4 — kb_monitor_agent NIP-17 delivery wiring.
Sprint 8 E5 — BriefGenerationPort severs KB→agents/ cross-context dependency.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from utils.logger import setup_logger

if TYPE_CHECKING:
    from kb.ports.brief_generation_port import BriefGenerationPort

logger = setup_logger(__name__)


async def generate_briefs_for_user(
    user_id: str,
    db_path: str,
    brief_port: "BriefGenerationPort",
) -> None:
    """
    Generate per-domain briefs via BriefGenerationPort.

    Using a single BaseAgent with the kb_monitor_agent persona avoids the
    orchestrator decomposing the task and assigning file_write steps to
    synthesis_agent (which is blocked from file_write by design).

    The agent writes brief files to data/briefs/{domain}_{date}.md;
    dispatch_delivery() picks them up in the same tick.

    Parameters
    ----------
    user_id :
        The user for whom briefs are generated.
    db_path :
        Path to the SQLite database (used to locate the briefs/ directory).
    brief_port :
        BriefGenerationPort implementation (required).
    """
    try:
        from datetime import date
        from config.kb_config import get_domain_ids
        today = date.today().isoformat()
        domain_ids = get_domain_ids()

        # Skip if all brief files already exist for today — avoids re-running
        # the LLM every 5 minutes when the scheduler keeps dispatching empty
        # domains (ai/ba with no accounts or 0-item feeds).
        briefs_dir = Path(db_path).parent / "briefs"
        missing_domains = [
            d for d in domain_ids
            if not (briefs_dir / f"{d}_{today}.md").exists()
        ]
        if not missing_domains:
            logger.debug(
                "All domain briefs already exist for %s — skipping generation", today
            )
            return

        # Generate one brief per domain in a separate agent session.
        # A single all-domains session accumulates 20 search results × N domains
        # in one conversation, making each LLM call progressively more expensive.
        # Per-domain sessions cap context to one domain's data.
        total_chars = 0
        for domain in missing_domains:
            try:
                output = await brief_port.generate_domain_brief(domain, user_id, today)
                total_chars += len(output)
                logger.info(
                    "kb_monitor_agent brief for domain=%s user=%s (%d chars)",
                    domain, user_id, len(output),
                )
            except Exception as exc:
                logger.warning(
                    "Brief generation failed for domain=%s user=%s: %s", domain, user_id, exc
                )
        logger.info(
            "kb_monitor_agent all briefs done for user=%s total_chars=%d",
            user_id, total_chars,
        )
    except Exception as exc:
        logger.warning("Brief generation failed for user=%s: %s", user_id, exc)
