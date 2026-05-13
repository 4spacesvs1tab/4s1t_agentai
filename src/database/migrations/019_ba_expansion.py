#!/usr/bin/env python3
"""
Database Migration 019: BA Expansion — Full BABOK v3 Coverage.

Phase KB-14: adds the data layer for the ba_agent full analysis partner.

New tables:
  ba_projects           — project anchor; all other ba_* tables reference this
  ba_decisions          — decision log with rationale + BABOK context
  ba_stakeholders       — stakeholder register per project
  ba_requirements       — requirements with lifecycle (draft → approved → implemented)
  ba_risks              — risk register with likelihood/impact/status
  ba_requirement_links  — traceability graph (requirement → requirement/objective/test/design)
  ba_business_rules     — operational / structural / derivation rules per project
  ba_glossary           — shared project vocabulary / data dictionary
  ba_kpis               — KPI definitions for success metric tracking

Design reference: KB_assistant_design_v2.md §10.3
"""
import sqlite3
from pathlib import Path
from typing import Optional

MIGRATION_ID = "019"
MIGRATION_NAME = "ba_expansion"
MIGRATION_DESCRIPTION = (
    "KB-14 BA expansion: ba_projects, ba_decisions, ba_stakeholders, "
    "ba_requirements, ba_risks, ba_requirement_links, ba_business_rules, "
    "ba_glossary, ba_kpis"
)


def get_db_path() -> str:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return str(project_root / "data" / "agent.db")


_SQL = """
-- ── ba_projects ──────────────────────────────────────────────────────────────
-- Prerequisite for all other ba_* tables.  All project-scoped skills reference
-- this table by name (TEXT match on the 'project' column in child tables).
CREATE TABLE IF NOT EXISTS ba_projects (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT    NOT NULL,
    name                TEXT    NOT NULL,
    description         TEXT,
    business_problem    TEXT,
    methodology         TEXT    DEFAULT 'agile',
    -- 'agile' | 'waterfall' | 'hybrid' | 'kanban'
    sponsor             TEXT,
    status              TEXT    DEFAULT 'active',
    -- 'active' | 'on_hold' | 'completed' | 'cancelled'
    target_completion   TEXT,
    -- ISO date (YYYY-MM-DD)
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    UNIQUE (user_id, name),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ba_projects_user
    ON ba_projects(user_id, status);

-- ── ba_decisions ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ba_decisions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT    NOT NULL,
    project         TEXT,
    -- ba_projects.name (denormalised for query convenience)
    decision        TEXT    NOT NULL,
    rationale       TEXT    NOT NULL,
    alternatives    TEXT,
    -- JSON array of strings
    owner           TEXT,
    status          TEXT    DEFAULT 'active',
    -- 'active' | 'superseded' | 'reverted'
    superseded_by   TEXT,
    -- id of the decision that supersedes this one
    babok_context   TEXT,
    -- JSON: [{title, excerpt}] from knowledge_base_search
    created_at      TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ba_decisions_user_project
    ON ba_decisions(user_id, project, created_at DESC);

-- ── ba_stakeholders ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ba_stakeholders (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT    NOT NULL,
    project             TEXT    NOT NULL,
    name                TEXT    NOT NULL,
    role                TEXT,
    interest            TEXT,
    -- free-text description of their stake
    influence           TEXT,
    -- 'high' | 'medium' | 'low'
    position            TEXT,
    -- 'champion' | 'supporter' | 'neutral' | 'blocker' | 'unknown'
    engagement_approach TEXT,
    notes               TEXT,
    last_updated        TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ba_stakeholders_user_project
    ON ba_stakeholders(user_id, project);

-- ── ba_requirements ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ba_requirements (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT    NOT NULL,
    project             TEXT,
    req_type            TEXT    NOT NULL,
    -- 'functional' | 'non_functional' | 'constraint' | 'assumption'
    title               TEXT    NOT NULL,
    description         TEXT    NOT NULL,
    acceptance_criteria TEXT,
    -- JSON array of strings
    priority            TEXT    DEFAULT 'medium',
    -- 'low' | 'medium' | 'high' | 'must_have'
    status              TEXT    DEFAULT 'draft',
    -- 'draft' | 'approved' | 'implemented' | 'deferred'
    source              TEXT,
    -- elicitation session, meeting, document
    approved_by         TEXT,
    approved_at         TEXT,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ba_requirements_user_project
    ON ba_requirements(user_id, project, status);

-- ── ba_risks ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ba_risks (
    id          TEXT PRIMARY KEY,
    user_id     TEXT    NOT NULL,
    project     TEXT,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL,
    likelihood  TEXT    DEFAULT 'medium',
    -- 'low' | 'medium' | 'high'
    impact      TEXT    DEFAULT 'medium',
    -- 'low' | 'medium' | 'high'
    risk_score  TEXT,
    -- computed: 'low' | 'medium' | 'high' | 'critical'
    mitigation  TEXT,
    contingency TEXT,
    owner       TEXT,
    status      TEXT    DEFAULT 'open',
    -- 'open' | 'mitigated' | 'accepted' | 'closed'
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ba_risks_user_project
    ON ba_risks(user_id, project, status);

-- ── ba_requirement_links ──────────────────────────────────────────────────────
-- Traceability graph.  Used by trace_requirements and assess_change_impact.
CREATE TABLE IF NOT EXISTS ba_requirement_links (
    id          TEXT PRIMARY KEY,
    user_id     TEXT    NOT NULL,
    project     TEXT    NOT NULL,
    source_id   TEXT    NOT NULL,
    -- ba_requirements.id
    source_type TEXT    DEFAULT 'requirement',
    target_id   TEXT    NOT NULL,
    target_type TEXT    NOT NULL,
    -- 'requirement' | 'objective' | 'test_case' | 'design_element'
    link_type   TEXT    NOT NULL,
    -- 'derives' | 'refines' | 'tests' | 'implements' | 'conflicts' | 'duplicates'
    created_at  TEXT    NOT NULL,
    UNIQUE (source_id, target_id, link_type),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ba_links_source
    ON ba_requirement_links(source_id);
CREATE INDEX IF NOT EXISTS idx_ba_links_target
    ON ba_requirement_links(target_id);
CREATE INDEX IF NOT EXISTS idx_ba_links_project
    ON ba_requirement_links(user_id, project);

-- ── ba_business_rules ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ba_business_rules (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT    NOT NULL,
    project                 TEXT,
    rule_name               TEXT    NOT NULL,
    rule_type               TEXT    NOT NULL,
    -- 'operational' | 'structural' | 'derivation'
    trigger_condition       TEXT,
    -- WHEN / IF condition
    condition_text          TEXT,
    -- full condition
    action_text             TEXT,
    -- THEN action
    exception_text          TEXT,
    -- UNLESS exception
    source_requirement_id   TEXT,
    -- FK to ba_requirements (soft reference)
    owner                   TEXT,
    status                  TEXT    DEFAULT 'active',
    -- 'active' | 'draft' | 'deprecated'
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ba_rules_user_project
    ON ba_business_rules(user_id, project, status);

-- ── ba_glossary ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ba_glossary (
    id          TEXT PRIMARY KEY,
    user_id     TEXT    NOT NULL,
    project     TEXT,
    -- NULL = global / cross-project
    term        TEXT    NOT NULL,
    definition  TEXT    NOT NULL,
    synonyms    TEXT,
    -- JSON array of strings
    data_type   TEXT,
    -- for data elements: 'string' | 'integer' | 'date' | 'boolean' | etc.
    source      TEXT,
    notes       TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    UNIQUE (user_id, project, term),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ba_glossary_term
    ON ba_glossary(user_id, term);

-- ── ba_kpis ───────────────────────────────────────────────────────────────────
-- KPI definitions.  Actuals are supplied at query time to analyze_kpi_performance.
CREATE TABLE IF NOT EXISTS ba_kpis (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT    NOT NULL,
    project             TEXT    NOT NULL,
    metric_name         TEXT    NOT NULL,
    description         TEXT,
    unit                TEXT,
    -- '%', 'days', 'count', '€', etc.
    baseline            REAL,
    target              REAL,
    direction           TEXT    DEFAULT 'higher_is_better',
    -- 'higher_is_better' | 'lower_is_better' | 'target_value'
    measurement_method  TEXT,
    frequency           TEXT,
    -- 'daily' | 'weekly' | 'monthly' | 'on_demand'
    owner               TEXT,
    status              TEXT    DEFAULT 'active',
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    UNIQUE (user_id, project, metric_name),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ba_kpis_project
    ON ba_kpis(user_id, project);
"""


def run(db_path: Optional[str] = None) -> None:
    path = db_path or get_db_path()
    conn = sqlite3.connect(path)
    try:
        # Check if already applied
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ba_projects'"
        ).fetchone()
        if row:
            print(f"[{MIGRATION_ID}] Already applied — ba_projects exists. Skipping.")
            return

        conn.executescript(_SQL)
        conn.commit()
        print(f"[{MIGRATION_ID}] {MIGRATION_NAME}: applied successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else get_db_path()
    run(db)
