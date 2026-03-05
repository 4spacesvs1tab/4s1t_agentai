"""
Tests for AuditLog (task 1.6).

Verifies:
- Table is created on start()
- Events are written to the DB after stop()
- Batch writing works correctly
- Event type constants exist
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

import pytest

os.environ.setdefault("SECRET_KEY", "CI_Test_S3cret_Key_64chars_long_ABCDEFGHIJK!@#$%^&*()")
os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')
os.environ.setdefault("DEBUG", "true")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.audit import AuditLog, AuditEventType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary SQLite path for each test."""
    return str(tmp_path / "audit_test.db")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_rows(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        return rows[0] if rows else 0
    finally:
        conn.close()


def _fetch_rows(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_table_created_on_start(tmp_db):
    log = AuditLog(f"sqlite:///{tmp_db}")
    await log.start()
    await log.stop()

    conn = sqlite3.connect(tmp_db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    conn.close()
    assert "audit_log" in tables


@pytest.mark.asyncio
async def test_five_events_written(tmp_db):
    log = AuditLog(f"sqlite:///{tmp_db}")
    await log.start()

    for i in range(5):
        await log.log(
            AuditEventType.SKILL_CALL,
            actor="test_agent",
            target=f"skill_{i}",
            metadata={"step": i},
        )

    await log.stop()

    assert _count_rows(tmp_db) == 5


@pytest.mark.asyncio
async def test_event_fields_persisted(tmp_db):
    log = AuditLog(f"sqlite:///{tmp_db}")
    await log.start()
    await log.log(
        AuditEventType.AGENT_SPAWN,
        actor="orchestrator",
        target="ba_agent",
        metadata={"task_id": "abc123"},
    )
    await log.stop()

    rows = _fetch_rows(tmp_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == AuditEventType.AGENT_SPAWN
    assert row["actor"] == "orchestrator"
    assert row["target"] == "ba_agent"
    assert "abc123" in row["metadata"]
    assert row["created_at"]  # not empty


@pytest.mark.asyncio
async def test_batch_of_many_events(tmp_db):
    """Write 100 events — exercises batch drain logic."""
    log = AuditLog(f"sqlite:///{tmp_db}")
    await log.start()

    for i in range(100):
        await log.log(AuditEventType.WORKFLOW_START, actor="orch", target=f"wf_{i}")

    await log.stop()
    assert _count_rows(tmp_db) == 100


@pytest.mark.asyncio
async def test_log_without_optional_fields(tmp_db):
    log = AuditLog(f"sqlite:///{tmp_db}")
    await log.start()
    await log.log(AuditEventType.AUTH_FAILURE)  # actor/target/metadata all None
    await log.stop()

    rows = _fetch_rows(tmp_db)
    assert rows[0]["event_type"] == AuditEventType.AUTH_FAILURE
    assert rows[0]["actor"] is None
    assert rows[0]["target"] is None


# ---------------------------------------------------------------------------
# Event type constants sanity check
# ---------------------------------------------------------------------------

def test_event_type_constants_exist():
    for name in (
        "AGENT_SPAWN", "SKILL_CALL", "AGENT_ERROR",
        "WORKFLOW_START", "WORKFLOW_END",
        "AUTH_SUCCESS", "AUTH_FAILURE",
    ):
        assert hasattr(AuditEventType, name), f"Missing constant: {name}"
        assert isinstance(getattr(AuditEventType, name), str)
