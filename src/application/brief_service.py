"""
BriefService — application service for KB brief operations.

Orchestrates brief listing, retrieval, and generation.
Route handlers call this service; they never touch filesystem or
brief_dispatcher directly.

DDD Rule 3: No FastAPI / HTTPException / Request imports here.
No sqlite3.connect() — uses get_db_connection() from infrastructure.
No os.environ reads — db_path injected at construction.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from infrastructure.sqlite._connection import get_db_connection
from utils.logger import setup_logger

logger = setup_logger(__name__)


def _briefs_dir() -> Path:
    # src/application/brief_service.py → 3 parents = repo root
    return Path(__file__).resolve().parent.parent.parent / "data" / "briefs"


class BriefService:
    """Application service for KB brief file operations and generation.

    Constructor receives db_path so unit tests can supply an in-memory path
    without touching the live database.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # list_briefs
    # ------------------------------------------------------------------

    async def list_briefs(self, domain: Optional[str] = None) -> list[dict]:
        """Return available brief files with delivered status and preview.

        Mirrors ingest_routes.list_briefs verbatim.
        Returns [{"domain", "date", "filename", "size", "preview", "delivered"}]
        sorted newest-first.
        """
        briefs_dir = _briefs_dir()
        if not briefs_dir.exists():
            return []

        delivered_set: set = set()
        try:
            with get_db_connection(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT domain, DATE(window_end) FROM kb_briefs WHERE delivered = 1"
                )
                for row in cur.fetchall():
                    delivered_set.add((row[0], row[1]))
        except Exception as exc:
            logger.warning("list_briefs: could not query delivered status: %s", exc)

        results = []
        for f in sorted(briefs_dir.glob("*.md"), reverse=True):
            parts = f.stem.split("_", 1)
            if len(parts) != 2:
                continue
            d, date = parts[0], parts[1]
            if domain and d != domain:
                continue
            preview = ""
            try:
                preview = f.read_text(encoding="utf-8", errors="ignore")[:300].strip()
            except Exception:
                pass
            results.append({
                "domain": d,
                "date": date,
                "filename": f.name,
                "size": f.stat().st_size,
                "preview": preview,
                "delivered": (d, date) in delivered_set,
            })
        return results

    # ------------------------------------------------------------------
    # get_brief
    # ------------------------------------------------------------------

    async def get_brief(self, domain: str, date: str) -> dict:
        """Return markdown content of a specific brief.

        Mirrors ingest_routes.read_brief verbatim.
        Raises FileNotFoundError if the brief file does not exist.
        """
        briefs_dir = _briefs_dir()
        brief_path = briefs_dir / f"{domain}_{date}.md"
        if not brief_path.exists():
            raise FileNotFoundError(f"Brief not found: {domain}/{date}")
        content = brief_path.read_text(encoding="utf-8")
        return {"domain": domain, "date": date, "content": content}

    # ------------------------------------------------------------------
    # generate_briefs
    # ------------------------------------------------------------------

    async def generate_briefs(
        self,
        user_id: str,
        domain_filter: Optional[str],
        infra: object,
    ) -> dict:
        """Schedule background brief generation + NIP-17 delivery.

        Returns {"status": "accepted", "message": ...} immediately.
        The actual work runs in a background asyncio task.

        `infra` is the app-level agent infrastructure object (not a FastAPI type).
        It must expose: skill_registry, skill_executor, api_client, audit_log.

        Mirrors ingest_routes.trigger_brief_generation verbatim.
        """
        db_path = self._db_path

        async def _run() -> None:
            try:
                from datetime import date

                today = date.today().isoformat()
                from config.kb_config import get_domain_ids
                all_domains = get_domain_ids()
                domains = [domain_filter] if domain_filter else all_domains

                from agents.base_agent import BaseAgent
                from agents.personas import get_persona
                from core.model_selector import ModelSelector
                from config.provider_config import get_active_provider

                persona = get_persona("kb_monitor_agent")
                selector = ModelSelector(provider_config=get_active_provider())
                briefs_dir = _briefs_dir()
                briefs_dir.mkdir(parents=True, exist_ok=True)

                for domain in domains:
                    brief_path = briefs_dir / f"{domain}_{today}.md"
                    if brief_path.exists() and domain_filter:
                        brief_path.unlink()
                        logger.info("Deleted existing brief for regeneration: %s", brief_path)
                    elif brief_path.exists():
                        logger.info("Brief already exists, skipping: %s", brief_path)
                        continue
                    logger.info("Generating brief for domain=%s", domain)
                    agent = BaseAgent(
                        persona=persona,
                        skill_registry=infra.skill_registry,
                        skill_executor=infra.skill_executor,
                        api_client=infra.api_client,
                        model_selector=selector,
                        audit_log=infra.audit_log,
                    )
                    task = (
                        f"Generate an intelligence brief for domain '{domain}' for today ({today}). "
                        f"MANDATORY: pass user_id='{user_id}' to every knowledge_base_search call — "
                        "without it you will get zero results due to user isolation. "
                        f"Call knowledge_base_search with: query=<relevant topic>, domain='{domain}', "
                        f"since='24h', user_id='{user_id}'. "
                        f"Write the structured brief to briefs/{domain}_{today}.md using file_write. "
                        "Format: Top Stories → Key Signals → Expert Predictions. "
                        "Cite each item with author, published_age, and source_url. "
                        "If fewer than 3 items found in 24h, extend to since='7d' and note this in the brief."
                    )
                    await agent.run(task=task)
                    if brief_path.exists():
                        logger.info("Brief written: %s (%d bytes)", brief_path, brief_path.stat().st_size)
                    else:
                        logger.warning("Brief not written for domain=%s", domain)

                # Short sleep lets WAL writes finish before brief_dispatcher opens its connection.
                await asyncio.sleep(1)
                from kb.brief_dispatcher import run_dispatch
                summary = await run_dispatch(user_id=user_id, db_path=db_path)
                logger.info("Brief dispatch complete: %s", summary)

            except Exception as exc:
                logger.error("Brief generation failed for user=%s: %s", user_id, exc, exc_info=True)

        asyncio.create_task(_run())
        scope = f"domain={domain_filter!r}" if domain_filter else "all domains"
        return {
            "status": "accepted",
            "message": f"Brief generation started for user={user_id!r} scope={scope}.",
        }
