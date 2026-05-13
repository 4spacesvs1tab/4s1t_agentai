"""
KB Cross-Domain Insights Job — Phase KB-20.

Weekly batch job that identifies entities appearing in content from multiple
KB domains and asks the LLM to surface the cross-domain connection.

Pipeline:
  1. Query kb_entities ordered by mention_count DESC (top 40).
  2. For each entity, find accounts that have a relation with it
     (via kb_entity_relations where source_entity = account_entity).
  3. Join with kb_accounts to get the domains of those accounts.
  4. Filter: entities appearing in > 1 distinct domain.
  5. One LLM call (GLM 4.7) with the top-20 cross-domain entities.
  6. Return list of insight dicts. Optionally write to brief file.

Token cost: ~15K tokens/week (one call per Monday).
Model: GLM 4.7 via nano-gpt.

Design reference: KB_assistant_design_v2.md §12.5
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)

_INSIGHTS_MODEL = "glm-4-7"
_MAX_ENTITIES = 20
_LOOKBACK_DAYS = 7


_CROSS_DOMAIN_PROMPT = """\
You are a research analyst. Below are {count} entities that appear across multiple
knowledge domains this week, along with the domains they were mentioned in.

Identify the most significant cross-domain connections and what they may signal.
Focus on non-obvious intersections — patterns that only become visible when looking
across domains simultaneously.

Entities and their domains:
{entities}

Return a JSON array (possibly empty) — no other text:

[
  {{
    "entity": "<entity name>",
    "domains": ["<domain1>", "<domain2>"],
    "insight": "<what this cross-domain presence signals, 1-2 sentences>",
    "significance": "high|medium|low"
  }},
  ...
]

Limit to the {limit} most significant insights.
"""


def _call_llm(prompt: str, api_key: str) -> str:
    """Call GLM 4.7 via nano-gpt. Returns raw text or empty string on error."""
    import httpx
    base = os.environ.get("NANO_GPT_BASE_URL", "https://nano-gpt.com/api/v1")
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            json={
                "model": _INSIGHTS_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("LLM call failed in cross_domain_insights: %s", exc)
        return ""


def _parse_insights(raw: str) -> list[dict]:
    """Parse LLM JSON response into a list of insight dicts."""
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(
            ln for ln in text.splitlines() if not ln.startswith("```")
        ).strip()
    try:
        items = json.loads(text)
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return []


def _find_cross_domain_entities(
    db_path: str, user_id: str, lookback_days: int = _LOOKBACK_DAYS
) -> list[dict]:
    """
    Find entities that appear in content from more than one domain.

    Strategy:
      1. Get top entities by mention_count from kb_entities.
      2. For each, look up kb_entity_relations where the source entity
         corresponds to a kb_accounts row (account entity has account_id
         stored as alias).
      3. From the matching accounts, collect distinct domains.
      4. Return entities with domains from more than one distinct domain.

    Falls back to a purely mention-count based approach if relations are sparse.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Step 1: Top entities by mention_count
        entities = conn.execute(
            """
            SELECT id, name, canonical_name, mention_count, first_seen
            FROM kb_entities
            ORDER BY mention_count DESC
            LIMIT 40
            """,
        ).fetchall()

        if not entities:
            conn.close()
            return []

        # Step 2: For each entity, find accounts related to it
        # Account entities have the account_id stored as an alias in kb_entities.
        # kb_entity_relations links account_entity → topic_entity via "writes_about" etc.
        # We want: entity → what accounts mention it → what domains those accounts are in.

        # Build alias → account_id lookup from kb_accounts
        acc_rows = conn.execute(
            "SELECT id, domains FROM kb_accounts WHERE active = 1 AND user_id = ?",
            (user_id,),
        ).fetchall()
        account_domains: dict[str, list[str]] = {}
        for row in acc_rows:
            domains = [d for d in (row["domains"] or "").split("|") if d.strip()]
            account_domains[row["id"]] = domains

        # For each entity, find which account entities have a relation to it
        # by checking kb_entity_relations.source_entity → kb_entities.aliases contains account_id
        cross_domain: list[dict] = []

        # Preload all entity aliases for account lookup
        acc_entity_map: dict[str, str] = {}  # entity_id → account_id
        ent_alias_rows = conn.execute(
            "SELECT id, aliases FROM kb_entities"
        ).fetchall()
        for row in ent_alias_rows:
            try:
                aliases = json.loads(row["aliases"] or "[]")
            except (json.JSONDecodeError, TypeError):
                aliases = []
            for alias in aliases:
                if alias in account_domains:
                    acc_entity_map[row["id"]] = alias

        for ent in entities:
            ent_id = ent["id"]

            # Find all relations involving this entity
            rels = conn.execute(
                """
                SELECT source_entity, target_entity
                FROM kb_entity_relations
                WHERE source_entity = ? OR target_entity = ?
                """,
                (ent_id, ent_id),
            ).fetchall()

            # Collect domains through account entities found in relations
            entity_domains: set[str] = set()
            for rel in rels:
                for side in (rel["source_entity"], rel["target_entity"]):
                    if side == ent_id:
                        continue
                    acc_id = acc_entity_map.get(side)
                    if acc_id:
                        entity_domains.update(account_domains.get(acc_id, []))

            if len(entity_domains) > 1:
                cross_domain.append({
                    "entity_id": ent_id,
                    "name": ent["canonical_name"],
                    "mention_count": ent["mention_count"],
                    "domains": sorted(entity_domains),
                })

        conn.close()

        # If relation-based lookup returned too few results, fall back to
        # using ingestion log to find accounts that ingested content mentioning
        # this entity (approximated by top mentioned entities across domains).
        if len(cross_domain) < 3:
            cross_domain = _fallback_ingestion_log(db_path, user_id, lookback_days)

        # Deduplicate and sort by domain count DESC, then mention_count DESC
        seen: set[str] = set()
        result: list[dict] = []
        for item in sorted(
            cross_domain,
            key=lambda x: (len(x.get("domains", [])), x.get("mention_count", 0)),
            reverse=True,
        ):
            if item["name"] not in seen:
                seen.add(item["name"])
                result.append(item)
            if len(result) >= _MAX_ENTITIES:
                break

        return result

    except Exception as exc:
        logger.warning("Cross-domain entity query failed: %s", exc)
        return []


def _fallback_ingestion_log(
    db_path: str, user_id: str, lookback_days: int
) -> list[dict]:
    """
    Fallback when entity relations are sparse.

    Uses kb_ingestion_log to find which domains were active recently,
    then pairs them with top-mentioned entities as potential cross-domain signals.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

        # Active domains this week
        active_domain_rows = conn.execute(
            """
            SELECT DISTINCT a.domains
            FROM kb_ingestion_log l
            JOIN kb_accounts a ON l.account_id = a.id
            WHERE l.user_id = ? AND l.created_at >= ? AND l.status = 'ok'
            """,
            (user_id, cutoff),
        ).fetchall()

        active_domains: set[str] = set()
        for row in active_domain_rows:
            for d in (row["domains"] or "").split("|"):
                if d.strip():
                    active_domains.add(d.strip())

        if len(active_domains) < 2:
            conn.close()
            return []

        # Top entities
        entities = conn.execute(
            """
            SELECT canonical_name, mention_count
            FROM kb_entities
            ORDER BY mention_count DESC
            LIMIT 20
            """,
        ).fetchall()
        conn.close()

        # Return top entities tagged with all active domains (coarse approximation)
        return [
            {
                "entity_id": "",
                "name": row["canonical_name"],
                "mention_count": row["mention_count"],
                "domains": sorted(active_domains),
            }
            for row in entities
        ]
    except Exception as exc:
        logger.debug("Fallback ingestion log query failed: %s", exc)
        return []


class CrossDomainInsightJob:
    """
    Weekly batch job for KB-20 cross-domain insight generation.

    Usage::

        job = CrossDomainInsightJob(api_key="...", db_path="...")
        insights = job.run(user_id="<uuid>")
    """

    SCHEDULE = "weekly"  # Mondays
    ESTIMATED_TOKENS = 15_000  # Per weekly run

    def __init__(
        self,
        api_key: Optional[str] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("NANO_GPT_API_KEY", "")
        self._db_path = db_path or str(get_db_path())

    def run(self, user_id: str) -> list[dict]:
        """
        Run the cross-domain insights job for *user_id*.

        Returns a list of insight dicts (possibly empty).
        Each dict: entity, domains, insight, significance.
        """
        cross_entities = _find_cross_domain_entities(self._db_path, user_id)
        if not cross_entities:
            logger.info("Cross-domain insights: no cross-domain entities found for user=%s", user_id)
            return []

        # Format entity list for the prompt
        entity_lines = "\n".join(
            f"- {e['name']} (domains: {', '.join(e['domains'])}; mentions: {e['mention_count']})"
            for e in cross_entities[:_MAX_ENTITIES]
        )
        prompt = _CROSS_DOMAIN_PROMPT.format(
            count=len(cross_entities),
            entities=entity_lines,
            limit=10,
        )

        raw = _call_llm(prompt, self._api_key)
        if not raw:
            return []

        insights = _parse_insights(raw)
        logger.info(
            "Cross-domain insights: found %d insight(s) for user=%s", len(insights), user_id
        )
        return insights

    def write_brief_section(
        self,
        insights: list[dict],
        brief_dir: Optional[str] = None,
        date_str: Optional[str] = None,
    ) -> Optional[str]:
        """
        Write cross-domain insights to a brief section file.

        Returns the file path written, or None if insights is empty.
        """
        if not insights:
            return None

        date = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        base = brief_dir or str(Path(self._db_path).parent / "briefs")
        Path(base).mkdir(parents=True, exist_ok=True)

        path = str(Path(base) / f"cross_domain_{date}.md")
        lines = [
            f"# Cross-Domain Signals — {date}\n",
            "_Entities appearing across multiple knowledge domains this week._\n\n",
        ]
        for item in insights:
            sig_badge = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(
                item.get("significance", ""), "•"
            )
            domains_str = ", ".join(item.get("domains", []))
            lines.append(
                f"## {sig_badge} {item.get('entity', 'Unknown Entity')}\n"
                f"**Domains**: {domains_str}  \n"
                f"{item.get('insight', '')}\n\n"
            )

        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            logger.info("Cross-domain brief written to %s", path)
            return path
        except Exception as exc:
            logger.warning("Failed to write cross-domain brief: %s", exc)
            return None
