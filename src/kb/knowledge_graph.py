"""
KB Knowledge Graph Service — Phase KB-19.

Manages kb_entities and kb_entity_relations tables. Provides:
  - upsert_entity()           — create or update an entity, merging aliases
  - upsert_relation()         — create or increment a typed relation edge
  - ensure_account_entity()   — reflect a kb_account row as a graph entity
  - query_graph()             — BFS traversal up to max_hops

Design reference: KB_assistant_design_v2.md §14
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Optional

from utils.logger import setup_logger
logger = setup_logger(__name__)



# Relation types accepted from the entity extractor + manual
VALID_RELATION_TYPES = frozenset({
    "mentioned", "cited", "endorsed", "criticized", "replied",
    "writes_about", "appeared_on", "co_authored", "contradicts",
    "predicts", "works_for",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class KnowledgeGraphService:
    """
    Thread-safe service for the KB entity graph.

    Uses one SQLite connection per call (WAL mode) — safe for the
    concurrent access patterns in the scheduler + API routes.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or str(get_db_path())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def upsert_entity(
        self,
        name: str,
        entity_type: str = "person",
        canonical_name: str | None = None,
        aliases: list[str] | None = None,
    ) -> str:
        """
        Insert or update an entity. Returns the entity id.

        If an entity with the same canonical_name exists:
          - increments mention_count
          - updates last_seen
          - merges new aliases into existing alias list
        Otherwise inserts a new row.
        """
        canonical = (canonical_name or name).strip()
        now = _now_iso()
        new_aliases = list(aliases or [])
        # Include the bare name as alias if it differs from canonical
        if name != canonical and name not in new_aliases:
            new_aliases.append(name)

        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, aliases, mention_count FROM kb_entities "
                "WHERE canonical_name = ?",
                (canonical,),
            ).fetchone()

            if row:
                existing_id = row["id"]
                existing_aliases: list[str] = json.loads(row["aliases"] or "[]")
                merged = sorted(set(existing_aliases) | set(new_aliases))
                conn.execute(
                    "UPDATE kb_entities "
                    "SET last_seen = ?, mention_count = mention_count + 1, aliases = ? "
                    "WHERE id = ?",
                    (now, json.dumps(merged), existing_id),
                )
                conn.commit()
                return existing_id

            entity_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO kb_entities "
                "(id, name, entity_type, canonical_name, aliases, "
                " first_seen, last_seen, mention_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    entity_id,
                    name,
                    entity_type,
                    canonical,
                    json.dumps(new_aliases),
                    now,
                    now,
                ),
            )
            conn.commit()
            return entity_id
        except Exception as exc:
            conn.rollback()
            logger.debug("upsert_entity failed for %r: %s", name, exc)
            raise
        finally:
            conn.close()

    def get_entity_by_name(self, name: str) -> dict | None:
        """
        Find entity by exact name, canonical_name, or alias.
        Case-insensitive.
        """
        name_lower = name.lower().strip()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM kb_entities "
                "WHERE LOWER(canonical_name) = ? OR LOWER(name) = ? "
                "ORDER BY mention_count DESC LIMIT 1",
                (name_lower, name_lower),
            ).fetchone()
            if row:
                return dict(row)

            # Alias scan
            rows = conn.execute(
                "SELECT * FROM kb_entities ORDER BY mention_count DESC"
            ).fetchall()
            for r in rows:
                aliases: list[str] = json.loads(r["aliases"] or "[]")
                if any(a.lower() == name_lower for a in aliases):
                    return dict(r)
            return None
        finally:
            conn.close()

    def get_entity_by_id(self, entity_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM kb_entities WHERE id = ?", (entity_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def fuzzy_resolve_entity(self, name: str) -> tuple[dict | None, str | None]:
        """
        Attempt substring match if exact lookup fails.

        Returns (entity_dict, resolved_from_label).
        resolved_from_label is a human-readable string like '"Fed"→"Federal Reserve"'
        if a fuzzy match was used, or None for an exact match.
        """
        exact = self.get_entity_by_name(name)
        if exact:
            return exact, None

        name_lower = name.lower().strip()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM kb_entities "
                "WHERE LOWER(canonical_name) LIKE ? OR LOWER(name) LIKE ? "
                "ORDER BY mention_count DESC LIMIT 1",
                (f"%{name_lower}%", f"%{name_lower}%"),
            ).fetchall()
        finally:
            conn.close()

        if rows:
            entity = dict(rows[0])
            label = f'"{name}"→"{entity["canonical_name"]}"'
            return entity, label
        return None, None

    # ------------------------------------------------------------------
    # Relation CRUD
    # ------------------------------------------------------------------

    def upsert_relation(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relation_type: str,
        weight: float = 1.0,
    ) -> str:
        """
        Insert or update a typed relation edge. Returns the relation id.

        If the same (source, target, relation_type) triple exists:
          - increments evidence_count
          - updates weight as a running average
          - updates last_seen
        """
        if relation_type not in VALID_RELATION_TYPES:
            relation_type = "mentioned"
        now = _now_iso()

        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, evidence_count, weight FROM kb_entity_relations "
                "WHERE source_entity = ? AND target_entity = ? AND relation_type = ?",
                (source_entity_id, target_entity_id, relation_type),
            ).fetchone()

            if row:
                existing_id = row["id"]
                old_n = row["evidence_count"]
                old_w = row["weight"]
                new_w = (old_w * old_n + weight) / (old_n + 1)
                conn.execute(
                    "UPDATE kb_entity_relations "
                    "SET evidence_count = evidence_count + 1, weight = ?, last_seen = ? "
                    "WHERE id = ?",
                    (new_w, now, existing_id),
                )
                conn.commit()
                return existing_id

            rel_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO kb_entity_relations "
                "(id, source_entity, target_entity, relation_type, "
                " weight, evidence_count, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (rel_id, source_entity_id, target_entity_id, relation_type,
                 weight, now, now),
            )
            conn.commit()
            return rel_id
        except Exception as exc:
            conn.rollback()
            logger.debug("upsert_relation failed: %s", exc)
            raise
        finally:
            conn.close()

    def get_relations(
        self,
        entity_id: str,
        relation_type: str | None = None,
        direction: str = "both",  # "out" | "in" | "both"
    ) -> list[dict]:
        """Return all relations touching entity_id."""
        conn = self._connect()
        try:
            conditions: list[str] = []
            params: list = []
            if direction in ("out", "both"):
                conditions.append("source_entity = ?")
                params.append(entity_id)
            if direction in ("in", "both"):
                conditions.append("target_entity = ?")
                params.append(entity_id)
            where = f"({' OR '.join(conditions)})"
            if relation_type:
                where += " AND relation_type = ?"
                params.append(relation_type)
            rows = conn.execute(
                f"SELECT * FROM kb_entity_relations "
                f"WHERE {where} ORDER BY evidence_count DESC",
                params,
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Account → entity bridge
    # ------------------------------------------------------------------

    def ensure_account_entity(self, account_id: str) -> Optional[str]:
        """
        Ensure a kb_account is represented as a graph entity.

        Looks up display_name in kb_accounts, then upserts it as an entity
        with the account_id as an alias. Returns entity_id or None if the
        account row doesn't exist.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT display_name FROM kb_accounts WHERE id = ? LIMIT 1",
                (account_id,),
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return None
        display_name: str = row["display_name"]
        return self.upsert_entity(
            name=display_name,
            entity_type="person",
            canonical_name=display_name,
            aliases=[account_id],
        )

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def query_graph(
        self,
        entity_name: str,
        relation_type: str | None = None,
        max_hops: int = 2,
    ) -> dict:
        """
        BFS traversal from entity_name up to max_hops.

        Returns::

            {
              "root_entity":    dict | None,
              "entities":       list[dict],
              "relations":      list[dict],
              "total_entities": int,
              "total_relations": int,
              "resolved_from":  str | None,   # set if fuzzy-resolved
              "error":          str | None,   # set if root not found
            }
        """
        root, resolved_from = self.fuzzy_resolve_entity(entity_name)
        if root is None:
            return {
                "root_entity": None,
                "entities": [],
                "relations": [],
                "total_entities": 0,
                "total_relations": 0,
                "resolved_from": None,
                "error": f"Entity '{entity_name}' not found in knowledge graph",
            }

        visited_entities: dict[str, dict] = {root["id"]: root}
        visited_relations: dict[str, dict] = {}
        frontier: deque[str] = deque([root["id"]])

        for _ in range(max_hops):
            next_frontier: deque[str] = deque()
            while frontier:
                eid = frontier.popleft()
                for rel in self.get_relations(eid, relation_type=relation_type):
                    if rel["id"] not in visited_relations:
                        visited_relations[rel["id"]] = rel
                    neighbor_id = (
                        rel["target_entity"]
                        if rel["source_entity"] == eid
                        else rel["source_entity"]
                    )
                    if neighbor_id not in visited_entities:
                        neighbor = self.get_entity_by_id(neighbor_id)
                        if neighbor:
                            visited_entities[neighbor_id] = neighbor
                            next_frontier.append(neighbor_id)
            frontier = next_frontier
            if not frontier:
                break

        entities = list(visited_entities.values())
        relations = list(visited_relations.values())

        # Annotate relations with human-readable names
        id_to_name = {e["id"]: e["canonical_name"] for e in entities}
        for rel in relations:
            rel["source_name"] = id_to_name.get(rel["source_entity"], rel["source_entity"])
            rel["target_name"] = id_to_name.get(rel["target_entity"], rel["target_entity"])

        return {
            "root_entity": root,
            "entities": entities,
            "relations": relations,
            "total_entities": len(entities),
            "total_relations": len(relations),
            "resolved_from": resolved_from,
            "error": None,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: KnowledgeGraphService | None = None


def get_knowledge_graph_service(db_path: str | None = None) -> KnowledgeGraphService:
    global _instance
    if _instance is None or (db_path and _instance._db_path != db_path):
        _instance = KnowledgeGraphService(db_path)
    return _instance
