#!/usr/bin/env python3
"""
Database Migration 025: Knowledge Graph Entity Layer.

Phase KB-19: formal kb_entities + kb_entity_relations tables.
The entity extractor already feeds the L2 discovery queue; this migration
adds the structured relational layer so entity mentions are persisted,
counted, and queryable via the knowledge_graph_query skill.

New tables:
  kb_entities         — canonical named entities (person/org/concept/event/product)
  kb_entity_relations — typed, weighted edges between entities

Design reference: KB_assistant_design_v2.md §14
"""
import sqlite3
import sys
from pathlib import Path

MIGRATION_ID = "025"
MIGRATION_NAME = "kb_knowledge_graph"
MIGRATION_DESCRIPTION = (
    "KB-19 Knowledge Graph: kb_entities and kb_entity_relations tables "
    "with indexes for fast BFS traversal"
)


def run(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kb_entities (
                id             TEXT PRIMARY KEY,
                name           TEXT NOT NULL,
                entity_type    TEXT NOT NULL DEFAULT 'person',
                    -- 'person' | 'organization' | 'concept' | 'event' | 'product'
                canonical_name TEXT NOT NULL,
                aliases        TEXT NOT NULL DEFAULT '[]',  -- JSON array of alternate names
                first_seen     TEXT NOT NULL,               -- ISO-8601 UTC
                last_seen      TEXT NOT NULL,               -- ISO-8601 UTC
                mention_count  INTEGER NOT NULL DEFAULT 1,
                UNIQUE(canonical_name)
            );

            CREATE TABLE IF NOT EXISTS kb_entity_relations (
                id             TEXT PRIMARY KEY,
                source_entity  TEXT NOT NULL,
                target_entity  TEXT NOT NULL,
                relation_type  TEXT NOT NULL,
                    -- 'mentioned' | 'cited' | 'endorsed' | 'criticized' | 'replied'
                    -- 'writes_about' | 'appeared_on' | 'co_authored'
                    -- 'contradicts' | 'predicts' | 'works_for'
                weight         REAL NOT NULL DEFAULT 1.0,
                evidence_count INTEGER NOT NULL DEFAULT 1,
                first_seen     TEXT NOT NULL,
                last_seen      TEXT NOT NULL,
                FOREIGN KEY (source_entity) REFERENCES kb_entities(id),
                FOREIGN KEY (target_entity) REFERENCES kb_entities(id),
                UNIQUE(source_entity, target_entity, relation_type)
            );

            CREATE INDEX IF NOT EXISTS idx_entity_relations_source
                ON kb_entity_relations(source_entity);

            CREATE INDEX IF NOT EXISTS idx_entity_relations_target
                ON kb_entity_relations(target_entity);

            CREATE INDEX IF NOT EXISTS idx_entities_name
                ON kb_entities(name);

            CREATE INDEX IF NOT EXISTS idx_entities_canonical
                ON kb_entities(canonical_name);
        """)
        conn.commit()
        print(f"[{MIGRATION_ID}] {MIGRATION_NAME}: OK")
    except Exception as exc:
        conn.rollback()
        print(f"[{MIGRATION_ID}] {MIGRATION_NAME}: FAILED — {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        default = Path(__file__).resolve().parent.parent.parent.parent / "data" / "agent.db"
        db_path = str(default)
    else:
        db_path = sys.argv[1]
    run(db_path)
