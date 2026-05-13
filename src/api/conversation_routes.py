"""
Conversation Memory + Chat Graph API — Phases KB-10, KB-25.

Endpoints for:
  - Syncing conversations from client localStorage to the server
  - Conversation graph: listing, linking, spawning, add-to-KB
  - Message sync / cross-device read / GDPR wipe (KB-25)
  - Conversation forking — "continue from answer N" (KB-25-G)
  - User memory settings (scope, retention)
  - User facts CRUD

All write routes require a fully 2FA-verified session (require_2fa).
Read-only routes (GET) use require_2fa for consistency.

Route prefix: /api/v1/conversations
Memory prefix: /api/v1/user/memory

Design reference: KB_assistant_design_v2.md §11.1a, §4.2, §4.4, §21
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.db_path import get_db_path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.security_dependencies import require_2fa
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(tags=["conversations"])





def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Relation type validation ─────────────────────────────────────────────────

VALID_RELATION_TYPES = frozenset({
    "continues",
    "spawned_from",
    "references",
    "shares_context",
    "contradicts",
})


# ===========================================================================
# Pydantic models
# ===========================================================================

# ── content_ctx stripping (KB-25, §21.5) ─────────────────────────────────────

_RE_PLANTUML  = re.compile(r'@startuml.*?@enduml', re.DOTALL | re.IGNORECASE)
_RE_BPMN      = re.compile(r'<definitions[^>]*>.*?</definitions>', re.DOTALL | re.IGNORECASE)
_RE_CODELONG  = re.compile(r'```[a-z]*\n(.*?)```', re.DOTALL)
_MAX_CTX_CHARS = 8_000  # ~2 K tokens per message


def compute_content_ctx(content: str) -> str:
    """Return a token-stripped version of message content for LLM context.

    Removes PlantUML/BPMN diagram source (already rendered and saved),
    collapses code blocks >30 lines, and hard-caps at 8 000 chars.
    The result is stored in conversation_messages.content_ctx.
    """
    ctx = _RE_PLANTUML.sub('[PlantUML diagram]', content)
    ctx = _RE_BPMN.sub('[BPMN diagram]', ctx)

    def _shorten_code(m: re.Match) -> str:
        lines = m.group(1).count('\n')
        if lines > 30:
            return f'```[code block, {lines} lines]```'
        return m.group(0)

    ctx = _RE_CODELONG.sub(_shorten_code, ctx)
    if len(ctx) > _MAX_CTX_CHARS:
        ctx = ctx[:_MAX_CTX_CHARS] + '…[truncated]'
    return ctx


# ── Pydantic models ───────────────────────────────────────────────────────────


class ConvSyncBody(BaseModel):
    """Payload sent by chat.html on every saveConversationToStorage() call."""
    conversation_id: str = Field(..., description="Client-generated conv_{ts}_{rand}")
    title: Optional[str] = Field(None, max_length=120)
    model: Optional[str] = None
    message_count: int = Field(0, ge=0)
    started_at: Optional[str] = None   # ISO timestamp from client
    last_active: Optional[str] = None  # ISO timestamp from client


class ConvLinkBody(BaseModel):
    target_id: str
    relation_type: str = Field(..., description=(
        "One of: continues, spawned_from, references, shares_context, contradicts"
    ))


class ConvAddToKBBody(BaseModel):
    domains: List[str] = Field(..., min_items=1, description="KB domain IDs")
    tags: List[str] = Field(default_factory=list)
    scope: str = Field("private", description="'private' | 'family' | 'team'")


class ConvSpawnBody(BaseModel):
    """Spawn a new conversation linked to an existing one."""
    title: Optional[str] = None   # optional override; client sets it later on first message


class MemorySettingsBody(BaseModel):
    memory_scope: str = Field(..., description="'off' | 'private' | 'family' | 'team'")
    memory_retention_days: int = Field(90, ge=1, le=3650)


class FactDeleteBody(BaseModel):
    fact_id: str


class FactEditBody(BaseModel):
    fact_value: str = Field(..., min_length=1, max_length=2000)
    fact_key: Optional[str] = None


# ===========================================================================
# Conversation sync + list
# ===========================================================================

def _maybe_generate_summary(conv_id: str, user_id: str) -> None:
    """Background task: generate a one-sentence summary if not already set."""
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT summary, message_count FROM conversations WHERE id = ? AND user_id = ?",
                (conv_id, user_id),
            ).fetchone()
            if not row or row["summary"] or (row["message_count"] or 0) < 3:
                return

        from kb.fact_extraction import generate_session_summary
        # We don't store message content server-side, so we generate a title-based summary.
        # Pass the title as a minimal "turn" so the LLM has something to work with.
        with _conn() as conn2:
            title_row = conn2.execute(
                "SELECT title FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
        title = title_row["title"] if title_row else ""
        if not title:
            return

        summary = generate_session_summary([{"role": "user", "content": title}])
        if summary:
            with _conn() as conn3:
                conn3.execute(
                    "UPDATE conversations SET summary = ? WHERE id = ? AND user_id = ?",
                    (summary[:200], conv_id, user_id),
                )
            logger.debug("Auto-summary generated for conversation %s", conv_id)
    except Exception as exc:
        logger.debug("Auto-summary failed for %s: %s", conv_id, exc)


@router.post(
    "/api/v1/conversations/sync",
    summary="Sync conversation metadata from client localStorage to server",
    status_code=status.HTTP_200_OK,
)
async def sync_conversation(
    body: ConvSyncBody,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    """
    Called by chat.html every time saveConversationToStorage() runs.
    Upserts lightweight metadata (no message content) into the conversations table.
    Triggers async auto-summary (KB-12) when message_count >= 3 and no summary yet.
    """
    user_id = current_user["id"]
    now = _utcnow()
    started = body.started_at or now
    last_active = body.last_active or now

    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO conversations
                (id, user_id, title, model, message_count, started_at, last_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title         = COALESCE(excluded.title, title),
                model         = COALESCE(excluded.model, model),
                message_count = excluded.message_count,
                last_active   = excluded.last_active
            """,
            (
                body.conversation_id,
                user_id,
                body.title,
                body.model,
                body.message_count,
                started,
                last_active,
            ),
        )

    # KB-12: trigger async summary generation when conversation is long enough
    if (body.message_count or 0) >= 3:
        background_tasks.add_task(_maybe_generate_summary, body.conversation_id, user_id)

    return {"ok": True, "id": body.conversation_id}


@router.get(
    "/api/v1/conversations/links",
    summary="List all conv_links for the current user's conversations",
)
async def list_conversation_links(
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    user_id = current_user["id"]
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT cl.id, cl.source_id, cl.target_id, cl.relation_type, cl.created_at
            FROM conv_links cl
            JOIN conversations c ON c.id = cl.source_id
            WHERE c.user_id = ?
            """,
            (user_id,),
        ).fetchall()
    return {"links": [dict(r) for r in rows]}


@router.get(
    "/api/v1/conversations",
    summary="List server-synced conversations for the current user",
)
async def list_conversations(
    current_user: dict = Depends(require_2fa),
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    user_id = current_user["id"]
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, title, summary, model, message_count,
                   memory_scope, domains, tags, is_root, in_kb,
                   started_at, last_active,
                   COALESCE(source, 'webui') AS source,
                   nostr_npub
            FROM conversations
            WHERE user_id = ?
            ORDER BY last_active DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()

        total = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]

    convs = []
    for r in rows:
        c = dict(r)
        c["domains"] = json.loads(c["domains"]) if c["domains"] else []
        c["tags"] = json.loads(c["tags"]) if c["tags"] else []
        convs.append(c)

    return {"conversations": convs, "total": total}


@router.get(
    "/api/v1/conversations/{conv_id}",
    summary="Get a single conversation with its graph links",
)
async def get_conversation(
    conv_id: str,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    user_id = current_user["id"]
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")

        links = conn.execute(
            """
            SELECT cl.id, cl.source_id, cl.target_id, cl.relation_type,
                   cl.created_by, cl.created_at,
                   c.title AS target_title
            FROM conv_links cl
            LEFT JOIN conversations c ON c.id = cl.target_id
            WHERE cl.source_id = ? OR cl.target_id = ?
            """,
            (conv_id, conv_id),
        ).fetchall()

    result = dict(row)
    result["domains"] = json.loads(result["domains"]) if result["domains"] else []
    result["tags"] = json.loads(result["tags"]) if result["tags"] else []
    result["links"] = [dict(l) for l in links]
    return result


@router.delete(
    "/api/v1/conversations/{conv_id}",
    summary="Delete a conversation from the server (client must also clear localStorage)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    conv_id: str,
    current_user: dict = Depends(require_2fa),
) -> None:
    user_id = current_user["id"]
    with _conn() as conn:
        result = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conversation not found")


@router.delete(
    "/api/v1/conversations",
    summary="Bulk-delete conversations by ID list",
    status_code=status.HTTP_200_OK,
)
async def bulk_delete_conversations(
    ids: List[str] = Query(default=[]),
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    if not ids:
        return {"deleted": 0}
    user_id = current_user["id"]
    placeholders = ",".join("?" * len(ids))
    with _conn() as conn:
        result = conn.execute(
            f"DELETE FROM conversations WHERE user_id = ? AND id IN ({placeholders})",
            [user_id, *ids],
        )
    return {"deleted": result.rowcount}


# ===========================================================================
# Graph links
# ===========================================================================

@router.post(
    "/api/v1/conversations/{conv_id}/link",
    summary="Create a directed graph link between two conversations",
    status_code=status.HTTP_201_CREATED,
)
async def create_conv_link(
    conv_id: str,
    body: ConvLinkBody,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    user_id = current_user["id"]

    if body.relation_type not in VALID_RELATION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid relation_type. Must be one of: {sorted(VALID_RELATION_TYPES)}",
        )

    if conv_id == body.target_id:
        raise HTTPException(status_code=400, detail="Cannot link a conversation to itself")

    with _conn() as conn:
        # Verify both conversations belong to this user
        for cid in (conv_id, body.target_id):
            row = conn.execute(
                "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
                (cid, user_id),
            ).fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Conversation not found: {cid}",
                )

        link_id = str(uuid.uuid4())
        try:
            conn.execute(
                """
                INSERT INTO conv_links (id, source_id, target_id, relation_type, created_by, created_at)
                VALUES (?, ?, ?, ?, 'user', ?)
                """,
                (link_id, conv_id, body.target_id, body.relation_type, _utcnow()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="Link already exists with this relation type",
            )

    return {
        "id": link_id,
        "source_id": conv_id,
        "target_id": body.target_id,
        "relation_type": body.relation_type,
    }


@router.delete(
    "/api/v1/conversations/{conv_id}/link/{target_id}",
    summary="Remove a graph link between two conversations",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conv_link(
    conv_id: str,
    target_id: str,
    current_user: dict = Depends(require_2fa),
) -> None:
    user_id = current_user["id"]
    with _conn() as conn:
        # Security: verify ownership via source conversation
        row = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")

        result = conn.execute(
            "DELETE FROM conv_links WHERE source_id = ? AND target_id = ?",
            (conv_id, target_id),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Link not found")


# ===========================================================================
# Add to KB + Spawn
# ===========================================================================

@router.post(
    "/api/v1/conversations/{conv_id}/add-to-kb",
    summary="Tag a conversation for KB ingestion (sets domain, tags, in_kb flag)",
)
async def add_conversation_to_kb(
    conv_id: str,
    body: ConvAddToKBBody,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    user_id = current_user["id"]
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")

        conn.execute(
            """
            UPDATE conversations
            SET in_kb        = 1,
                domains      = ?,
                tags         = ?,
                memory_scope = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                json.dumps(body.domains),
                json.dumps(body.tags),
                body.scope,
                conv_id,
                user_id,
            ),
        )

    logger.info("Conversation %s marked for KB ingestion by user %s", conv_id, user_id)
    return {"ok": True, "conv_id": conv_id, "domains": body.domains, "tags": body.tags}


@router.post(
    "/api/v1/conversations/{conv_id}/spawn",
    summary="Spawn a new conversation linked to an existing one (spawned_from)",
    status_code=status.HTTP_201_CREATED,
)
async def spawn_conversation(
    conv_id: str,
    body: ConvSpawnBody,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    """
    Creates a new empty conversation record on the server pre-linked to conv_id.
    The client then opens /chat?conv={new_id} and the user continues from there.
    """
    user_id = current_user["id"]

    with _conn() as conn:
        parent = conn.execute(
            "SELECT id, domains, tags FROM conversations WHERE id = ? AND user_id = ?",
            (conv_id, user_id),
        ).fetchone()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent conversation not found")

        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        rnd = uuid.uuid4().hex[:9]
        new_id = f"conv_{ts}_{rnd}"
        now = _utcnow()

        conn.execute(
            """
            INSERT INTO conversations
                (id, user_id, title, domains, tags, is_root, started_at, last_active)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                new_id,
                user_id,
                body.title,
                parent["domains"],  # inherit parent domains
                parent["tags"],     # inherit parent tags
                now,
                now,
            ),
        )

        link_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO conv_links (id, source_id, target_id, relation_type, created_by, created_at)
            VALUES (?, ?, ?, 'spawned_from', 'system', ?)
            """,
            (link_id, new_id, conv_id, now),
        )

    return {
        "new_conversation_id": new_id,
        "parent_id": conv_id,
        "relation_type": "spawned_from",
        "link_id": link_id,
        "chat_url": f"/chat?conv={new_id}",
    }


# ===========================================================================
# Memory settings
# ===========================================================================

VALID_MEMORY_SCOPES = frozenset({"off", "private", "family", "team"})


class SessionConsentBody(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    scope: str = Field("private", description="'private' | 'family' | 'team'")


@router.get(
    "/api/v1/user/memory-settings",
    summary="Get current memory scope and retention settings",
)
async def get_memory_settings(
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    user_id = current_user["id"]
    with _conn() as conn:
        row = conn.execute(
            "SELECT memory_scope, memory_retention_days FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

    return {
        "memory_scope": row["memory_scope"] or "off",
        "memory_retention_days": row["memory_retention_days"] or 90,
        "valid_scopes": sorted(VALID_MEMORY_SCOPES),
    }


@router.put(
    "/api/v1/user/memory-settings",
    summary="Update memory scope and retention",
)
async def update_memory_settings(
    body: MemorySettingsBody,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    if body.memory_scope not in VALID_MEMORY_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid memory_scope. Must be one of: {sorted(VALID_MEMORY_SCOPES)}",
        )

    user_id = current_user["id"]
    with _conn() as conn:
        conn.execute(
            """
            UPDATE users
            SET memory_scope = ?, memory_retention_days = ?
            WHERE id = ?
            """,
            (body.memory_scope, body.memory_retention_days, user_id),
        )

    logger.info(
        "User %s updated memory: scope=%s retention=%dd",
        user_id, body.memory_scope, body.memory_retention_days,
    )
    return {
        "ok": True,
        "memory_scope": body.memory_scope,
        "memory_retention_days": body.memory_retention_days,
    }


@router.post(
    "/api/v1/user/memory-session",
    summary="Enable memory consent for a specific session (used by web UI and API clients)",
    status_code=status.HTTP_200_OK,
)
async def enable_session_memory(
    body: SessionConsentBody,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    """
    Stores a kb_session_memory_consent row for the given session_id.
    The web UI calls this when the memory toggle is switched on for a conversation.
    The external API can call it with session_id = 'api_{conversation_id}'.
    """
    if body.scope not in ("private", "family", "team"):
        raise HTTPException(status_code=400, detail="scope must be 'private', 'family', or 'team'")
    user_id = current_user["id"]
    import asyncio as _aio
    from kb.fact_extraction import get_fact_extraction_job
    job = get_fact_extraction_job()
    await _aio.to_thread(job.enable_session_memory, user_id, body.session_id, body.scope)
    logger.info("Session memory enabled: user=%s session=%s scope=%s", user_id, body.session_id, body.scope)
    return {"ok": True, "session_id": body.session_id, "scope": body.scope}


# ===========================================================================
# User facts (KB-12: fact extraction + management)
# ===========================================================================

@router.get(
    "/api/v1/user/facts",
    summary="List all stored user facts",
)
async def list_user_facts(
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    user_id = current_user["id"]
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, fact_type, fact_key, fact_value, confidence,
                   source, consent_level, created_at, updated_at, expires_at
            FROM kb_user_facts
            WHERE user_id = ?
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()

    return {"facts": [dict(r) for r in rows], "total": len(rows)}


@router.put(
    "/api/v1/user/facts/{fact_id}",
    summary="Edit a stored user fact value",
)
async def edit_user_fact(
    fact_id: str,
    body: FactEditBody,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    user_id = current_user["id"]
    now = _utcnow()
    with _conn() as conn:
        updates = ["fact_value = ?", "updated_at = ?", "source = 'explicit'", "confidence = 1.0"]
        params: list = [body.fact_value, now]
        if body.fact_key is not None:
            updates.append("fact_key = ?")
            params.append(body.fact_key)
        params.extend([fact_id, user_id])
        result = conn.execute(
            f"UPDATE kb_user_facts SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
            params,
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Fact not found")
        row = conn.execute(
            "SELECT id, fact_type, fact_key, fact_value, confidence, source, updated_at FROM kb_user_facts WHERE id = ?",
            (fact_id,),
        ).fetchone()
    return dict(row)


@router.delete(
    "/api/v1/user/facts/{fact_id}",
    summary="Delete a stored user fact",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_user_fact(
    fact_id: str,
    current_user: dict = Depends(require_2fa),
) -> None:
    user_id = current_user["id"]
    with _conn() as conn:
        result = conn.execute(
            "DELETE FROM kb_user_facts WHERE id = ? AND user_id = ?",
            (fact_id, user_id),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Fact not found")


@router.delete(
    "/api/v1/user/facts",
    summary="Delete all stored user facts (GDPR wipe)",
    status_code=status.HTTP_200_OK,
)
async def clear_all_user_facts(
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    user_id = current_user["id"]
    with _conn() as conn:
        result = conn.execute(
            "DELETE FROM kb_user_facts WHERE user_id = ?",
            (user_id,),
        )
    logger.info("User %s cleared all facts (%d deleted)", user_id, result.rowcount)
    return {"deleted": result.rowcount}


@router.get(
    "/api/v1/user/interests",
    summary="List user interest topics with computed decay scores",
)
async def list_user_interests(
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    user_id = current_user["id"]
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT id, topic, mention_timestamps, last_mentioned
            FROM kb_user_interests
            WHERE user_id = ?
            ORDER BY last_mentioned DESC
            LIMIT 50
            """,
            (user_id,),
        ).fetchall()

    from datetime import datetime as _dt
    import math

    def _decay_score(timestamps_json: str) -> float:
        """Exponential decay; half-life = 14 days."""
        try:
            timestamps = json.loads(timestamps_json or "[]")
        except Exception:
            return 0.0
        now = _dt.now(timezone.utc)
        half_life = 14.0
        score = 0.0
        for ts in timestamps:
            try:
                age_days = (now - _dt.fromisoformat(ts)).total_seconds() / 86400
                score += math.pow(0.5, age_days / half_life)
            except Exception:
                pass
        return round(score, 4)

    interests = []
    for r in rows:
        d = dict(r)
        d["score"] = _decay_score(d["mention_timestamps"])
        del d["mention_timestamps"]   # don't leak raw timestamps to client
        interests.append(d)

    interests.sort(key=lambda x: x["score"], reverse=True)
    return {"interests": interests, "total": len(interests)}


# ===========================================================================
# KB-25 — Conversation Message Sync
# ===========================================================================

_MSG_RETENTION_DAYS = 14  # default for conversation messages (§21.3)


class MessageItem(BaseModel):
    """One message as sent from the client in a sync request."""
    id: str
    seq: int = Field(..., ge=0)
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str
    model: Optional[str] = None
    created_at: Optional[str] = None


class PostMessagesBody(BaseModel):
    messages: List[MessageItem] = Field(..., min_length=1, max_length=200)


class ForkBody(BaseModel):
    fork_parent_id: str
    fork_seq: int = Field(..., ge=0)
    title: Optional[str] = Field(None, max_length=120)


def _get_user_retention_days(conn: sqlite3.Connection, user_id: str) -> int:
    """Return memory_retention_days for the user; fall back to default."""
    row = conn.execute(
        "SELECT memory_retention_days FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row and row["memory_retention_days"]:
        return int(row["memory_retention_days"])
    return _MSG_RETENTION_DAYS


def _ownership_check(conn: sqlite3.Connection, conv_id: str, user_id: str) -> bool:
    """Return True if conv_id belongs to user_id."""
    row = conn.execute(
        "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
        (conv_id, user_id),
    ).fetchone()
    return row is not None


@router.post(
    "/api/v1/conversations/{conv_id}/messages",
    summary="Upsert messages for a conversation (KB-25 sync write path)",
    status_code=200,
)
async def post_messages(
    conv_id: str,
    body: PostMessagesBody,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    """Sync client messages to the server.

    Idempotent: ON CONFLICT (conv_id, seq) DO NOTHING.
    Ownership verified before any write (§22.3).
    """
    user_id = current_user["id"]
    with _conn() as conn:
        if not _ownership_check(conn, conv_id, user_id):
            raise HTTPException(status_code=404, detail="Conversation not found")

        retention = _get_user_retention_days(conn, user_id)
        synced = 0
        now = _utcnow()
        for msg in body.messages:
            created = msg.created_at or now
            try:
                expires = (
                    datetime.fromisoformat(created.replace("Z", "+00:00"))
                    + timedelta(days=retention)
                ).isoformat()
            except Exception:
                expires = (
                    datetime.now(timezone.utc) + timedelta(days=retention)
                ).isoformat()
            ctx = compute_content_ctx(msg.content)
            conn.execute(
                """
                INSERT OR IGNORE INTO conversation_messages
                    (id, conv_id, seq, role, content, content_ctx, model, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg.id, conv_id, msg.seq, msg.role,
                    msg.content, ctx, msg.model, created, expires,
                ),
            )
            synced += conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
    return {"synced": synced}


@router.get(
    "/api/v1/conversations/{conv_id}/messages",
    summary="Read messages for a conversation (KB-25 cross-device read path)",
)
async def get_messages(
    conv_id: str,
    after_seq: int = 0,
    limit: int = 100,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    """Return messages newer than after_seq, respecting expires_at (§22.3)."""
    user_id = current_user["id"]
    limit = min(limit, 200)
    with _conn() as conn:
        if not _ownership_check(conn, conv_id, user_id):
            raise HTTPException(status_code=404, detail="Conversation not found")

        # Check if this is a fork — if so, merge parent messages up to fork_seq
        conv_row = conn.execute(
            "SELECT fork_parent_id, fork_seq FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()
        fork_parent_id = conv_row["fork_parent_id"] if conv_row else None
        fork_seq_limit = conv_row["fork_seq"] if conv_row else None

        base_rows: list = []
        if fork_parent_id and fork_seq_limit is not None:
            # Verify parent ownership
            if _ownership_check(conn, fork_parent_id, user_id):
                base_rows = conn.execute(
                    """
                    SELECT id, seq, role, content, model, created_at
                    FROM conversation_messages
                    WHERE conv_id = ?
                      AND seq <= ?
                      AND (expires_at IS NULL OR expires_at > datetime('now'))
                    ORDER BY seq
                    """,
                    (fork_parent_id, fork_seq_limit),
                ).fetchall()

        own_rows = conn.execute(
            """
            SELECT id, seq, role, content, model, created_at
            FROM conversation_messages
            WHERE conv_id = ?
              AND seq >= ?
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY seq
            LIMIT ?
            """,
            (conv_id, after_seq, limit),
        ).fetchall()

    all_rows = base_rows + own_rows if fork_parent_id else own_rows
    messages = [dict(r) for r in all_rows]
    return {"messages": messages, "total": len(messages)}


@router.delete(
    "/api/v1/conversations/{conv_id}/messages",
    summary="GDPR wipe — delete all messages for a conversation (KB-25)",
    status_code=200,
)
async def delete_messages(
    conv_id: str,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    """Delete all message content for this conversation.

    The conversation record itself is preserved; only the messages table rows
    are removed.  Satisfies right-to-erasure for message content independently
    of user facts (which have their own wipe endpoint).
    """
    user_id = current_user["id"]
    with _conn() as conn:
        if not _ownership_check(conn, conv_id, user_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        result = conn.execute(
            "DELETE FROM conversation_messages WHERE conv_id = ?", (conv_id,)
        )
        deleted = result.rowcount
        conn.commit()
    logger.info("User %s wiped %d messages from conv %s", user_id, deleted, conv_id)
    return {"deleted": deleted}


@router.post(
    "/api/v1/conversations/{conv_id}/fork",
    summary="Fork a conversation at a specific message (KB-25-G)",
    status_code=201,
)
async def fork_conversation(
    conv_id: str,
    body: ForkBody,
    current_user: dict = Depends(require_2fa),
) -> Dict[str, Any]:
    """Create a new conversation branched from body.fork_parent_id at body.fork_seq.

    The caller must have ownership of both the parent and the new conv_id.
    A conv_links entry is created automatically (relation_type='spawned_from').
    """
    user_id = current_user["id"]
    new_id = conv_id  # client provides the new conv_id in the URL

    with _conn() as conn:
        # Verify parent ownership
        if not _ownership_check(conn, body.fork_parent_id, user_id):
            raise HTTPException(status_code=404, detail="Parent conversation not found")

        # Get parent title for default title
        parent_row = conn.execute(
            "SELECT title FROM conversations WHERE id = ?", (body.fork_parent_id,)
        ).fetchone()
        parent_title = parent_row["title"] if parent_row else ""
        fork_title = body.title or (f"Fork of: {parent_title}"[:120] if parent_title else "Forked conversation")

        now = _utcnow()
        # Create the new conversation (the client may have already synced metadata via /sync;
        # use INSERT OR IGNORE so we don't fail if it already exists)
        conn.execute(
            """
            INSERT OR IGNORE INTO conversations
                (id, user_id, title, fork_parent_id, fork_seq, started_at, last_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (new_id, user_id, fork_title, body.fork_parent_id, body.fork_seq, now, now),
        )
        # Ensure fork columns are set (in case the row already existed from /sync)
        conn.execute(
            """
            UPDATE conversations
            SET fork_parent_id = ?, fork_seq = ?, title = COALESCE(title, ?)
            WHERE id = ? AND user_id = ?
            """,
            (body.fork_parent_id, body.fork_seq, fork_title, new_id, user_id),
        )

        # Create conv_links edge: new_id → parent, relation_type=spawned_from
        link_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT OR IGNORE INTO conv_links
                (id, source_id, target_id, relation_type, created_by, created_at)
            VALUES (?, ?, ?, 'spawned_from', ?, ?)
            """,
            (link_id, new_id, body.fork_parent_id, user_id, now),
        )
        conn.commit()

    chat_url = f"/chat?conv={new_id}"
    logger.info(
        "User %s forked conv %s at seq=%d → new conv %s",
        user_id, body.fork_parent_id, body.fork_seq, new_id,
    )
    return {"new_conversation_id": new_id, "chat_url": chat_url}
