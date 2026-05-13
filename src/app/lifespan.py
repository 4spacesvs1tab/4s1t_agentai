"""
Application lifespan (startup / shutdown) for the 4S1T Agent AI application.

Extracted from src/main.py (B4 refactor).
"""
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, Depends

from database.connection import DatabaseConnection
from core.audit import get_audit_log
from core.db_path import get_db_path
from api.security_dependencies import require_2fa
from components.system.initializer import system_initializer, system_lifespan
from services.nostr_service import start_nostr_service, stop_nostr_service
from utils.logger import setup_logger

logger = setup_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events using system initializer."""
    async with system_lifespan(system_initializer):
        # Verify DB file permissions at startup
        try:
            DatabaseConnection.startup_permission_check()
        except RuntimeError as e:
            logger.error(f"Startup permission check failed: {e}")
            raise

        # Start audit log writer
        audit_log = get_audit_log()
        await audit_log.start()
        logger.info("Audit log started")

        # Initialise shared agent infrastructure (skills, API client, executor)
        from components.system.agent_infrastructure import create_agent_infrastructure
        app.state.agent_infra = await create_agent_infrastructure()
        logger.info("Agent infrastructure ready")

        # Start KB ingestion scheduler (Phase KB-2/KB-4)
        # IMPORTANT: store a strong reference to the task — the event loop only
        # keeps weak refs, so an unreferenced task gets GC'd and silently cancelled.
        from kb.scheduler import KBScheduler
        from infrastructure.agents.agent_brief_adapter import AgentBriefAdapter
        _brief_port = AgentBriefAdapter(agent_infra=app.state.agent_infra)
        app.state.kb_scheduler = KBScheduler(brief_port=_brief_port)
        app.state.kb_scheduler_task = asyncio.create_task(
            app.state.kb_scheduler.run_forever(),
            name="kb-scheduler",
        )
        logger.info("KB ingestion scheduler started")

        # After system initialization is complete, integrate MCP routes
        logger.info("System initialization complete, integrating MCP routes...")
        try:
            # Import here to avoid circular imports
            from mcp.server import global_mcp_server
            from mcp.mcp_types import MCPRequest, RequestMethod

            logger.info(f"MCP server available after initialization: {global_mcp_server is not None}")

            if global_mcp_server:
                # Integrate MCP routes directly with the main app.
                # All three endpoints require a fully 2FA-verified session.
                @app.get("/mcp/tools")
                async def list_mcp_tools(
                    _user: Dict[str, Any] = Depends(require_2fa)
                ):
                    logger.info("MCP tools endpoint called")
                    request = MCPRequest(method=RequestMethod.TOOL_LIST)
                    response = await global_mcp_server.handle_request(request)
                    if response.error:
                        logger.error(f"MCP tools error: {response.error}")
                        raise HTTPException(status_code=500, detail=response.error.get("message", str(response.error)))
                    # Ensure the response format matches what the web UI expects
                    return {"tools": response.result.get("tools", [])} if isinstance(response.result, dict) else {"tools": []}

                @app.post("/mcp/tools/{tool_name}")
                async def call_mcp_tool(
                    tool_name: str,
                    arguments: dict,
                    _user: Dict[str, Any] = Depends(require_2fa),
                ):
                    logger.info(f"MCP tool call endpoint called for tool: {tool_name}")
                    tool_arguments = arguments

                    request = MCPRequest(
                        method=RequestMethod.TOOL_CALL,
                        params={"name": tool_name, "arguments": tool_arguments}
                    )
                    response = await global_mcp_server.handle_request(request)
                    if response.error:
                        logger.error(f"MCP tool call error: {response.error}")
                        raise HTTPException(status_code=500, detail=response.error.get("message", str(response.error)))
                    return response.result

                @app.get("/mcp/resources")
                async def list_mcp_resources(
                    _user: Dict[str, Any] = Depends(require_2fa)
                ):
                    logger.info("MCP resources endpoint called")
                    request = MCPRequest(method=RequestMethod.RESOURCE_LIST)
                    response = await global_mcp_server.handle_request(request)
                    if response.error:
                        logger.error(f"MCP resources error: {response.error}")
                        raise HTTPException(status_code=500, detail=response.error.get("message", str(response.error)))
                    return response.result

                logger.info("MCP routes integrated successfully")
            else:
                logger.warning("MCP server not available, skipping MCP route integration")
        except Exception as e:
            logger.error(f"Failed to integrate MCP routes: {e}", exc_info=True)

        # Start NIP-17 communication service
        logger.info("Starting Nostr NIP-17 Communication Service...")
        nostr_config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "nostr_nip17.yaml")
        nostr_service_started = await start_nostr_service(config_path=nostr_config_path)

        if nostr_service_started:
            logger.info("Nostr NIP-17 Communication Service started successfully")
            from services.nostr_service import get_nostr_service
            service = get_nostr_service()
            if service:
                logger.info(f"Active relay: {service.chat_agent.client.active_relay if service.chat_agent else 'N/A'}")

                # Per-sender conversation history keyed by sender_npub
                _nip17_histories: Dict[str, Any] = {}

                # KB-27: per-sender session state {conv_id, last_at (unix ts)}
                _nip17_sessions: Dict[str, Any] = {}
                _NIP17_SESSION_GAP_SECS = 4 * 3600  # 4-hour boundary

                # Tracks how many NEW messages have been added to each conv_id this session
                # (separate from seeded DB history, which belongs to prior conv_ids)
                _nip17_conv_seq: Dict[str, int] = {}

                def _resolve_nostr_user_id() -> str:
                    """Return the first user's ID from the DB (solo deployment)."""
                    try:
                        from database.connection import get_database_connection as _gdb
                        rows = _gdb().execute_query("SELECT id FROM users LIMIT 1", ())
                        if rows:
                            return rows[0]["id"]
                    except Exception:
                        pass
                    return ""

                async def _handle_memory_command(cmd_parts: list, sender_npub: str) -> str:
                    """Handle /memory on|off command."""
                    import asyncio as _aio
                    from kb.fact_extraction import get_fact_extraction_job
                    if len(cmd_parts) < 2:
                        return "Usage: /memory on | /memory off"
                    action = cmd_parts[1].lower()
                    user_id = _resolve_nostr_user_id()
                    if not user_id:
                        return "Could not resolve user — memory command unavailable."
                    session_id = f"nostr_{sender_npub[:16]}"
                    job = get_fact_extraction_job()
                    if action == "on":
                        await _aio.to_thread(job.enable_session_memory, user_id, session_id)
                        return "Memory enabled for this session. Facts will be extracted from your messages and stored privately."
                    elif action == "off":
                        await _aio.to_thread(job.disable_session_memory, user_id, session_id)
                        return "Memory disabled for this session."
                    return f"Unknown memory action '{action}'. Use: /memory on | /memory off"

                async def _handle_facts_command() -> str:
                    """Handle /facts command — list stored facts."""
                    import sqlite3 as _sql
                    _db_path = str(get_db_path())
                    user_id = _resolve_nostr_user_id()
                    if not user_id:
                        return "Could not resolve user."
                    try:
                        conn = _sql.connect(_db_path)
                        conn.row_factory = _sql.Row
                        rows = conn.execute(
                            """
                            SELECT id, fact_type, fact_key, fact_value, confidence
                            FROM kb_user_facts
                            WHERE user_id = ?
                              AND (expires_at IS NULL OR expires_at > datetime('now'))
                            ORDER BY updated_at DESC
                            LIMIT 20
                            """,
                            (user_id,),
                        ).fetchall()
                        conn.close()
                    except Exception as exc:
                        logger.warning("Facts query failed: %s", exc)
                        return "Failed to retrieve facts."
                    if not rows:
                        return "No stored facts. Enable memory with /memory on to start learning."
                    lines = [f"Stored facts ({len(rows)}):"]
                    for r in rows:
                        lines.append(f"• [{r['id'][:8]}] {r['fact_key']}: {r['fact_value']} (conf={r['confidence']:.2f})")
                    lines.append("\nUse /forget <id-prefix> to delete a fact.")
                    return "\n".join(lines)

                async def _handle_forget_command(cmd_parts: list) -> str:
                    """Handle /forget <fact_id> command."""
                    import sqlite3 as _sql
                    if len(cmd_parts) < 2:
                        return "Usage: /forget <fact-id-prefix>"
                    fact_prefix = cmd_parts[1].strip()
                    _db_path = str(get_db_path())
                    user_id = _resolve_nostr_user_id()
                    if not user_id:
                        return "Could not resolve user."
                    try:
                        conn = _sql.connect(_db_path)
                        result = conn.execute(
                            "DELETE FROM kb_user_facts WHERE user_id = ? AND id LIKE ?",
                            (user_id, f"{fact_prefix}%"),
                        )
                        conn.commit()
                        deleted = result.rowcount
                        conn.close()
                    except Exception as exc:
                        logger.warning("Forget command failed: %s", exc)
                        return "Failed to delete fact."
                    if deleted:
                        return f"Deleted {deleted} fact(s) matching '{fact_prefix}'."
                    return f"No facts found with ID starting with '{fact_prefix}'."

                # ── KB-22: Additional command handlers ────────────────────────

                async def _handle_search_command(cmd_parts: list) -> str:
                    """Handle /search <query> — calls knowledge_base_search skill."""
                    if len(cmd_parts) < 2:
                        return "Usage: /search <query>"
                    query = " ".join(cmd_parts[1:])
                    user_id = _resolve_nostr_user_id()
                    try:
                        import sys as _sys
                        import os as _os
                        _skills_dir = _os.path.join(_os.path.dirname(__file__), "..", "skills")
                        if _skills_dir not in _sys.path:
                            _sys.path.insert(0, _os.path.dirname(_os.path.dirname(__file__)))
                        from skills.knowledge_base_search.handler import execute as _kbs
                        result = _kbs({"query": query, "n_results": 5, "user_id": user_id})
                        items = result.get("results", [])
                        if not items:
                            return f"No KB results for '{query}'."
                        lines = [f"KB results for '{query}':"]
                        for i, it in enumerate(items[:5], 1):
                            acc = it.get("account_id", "?")
                            snippet = (it.get("text") or "")[:200].replace("\n", " ")
                            lines.append(f"{i}. [{acc}] {snippet}")
                        return "\n".join(lines)
                    except Exception as exc:
                        logger.warning("Search command failed: %s", exc)
                        return "Search failed. Try the web UI for advanced queries."

                async def _handle_brief_command(cmd_parts: list) -> str:
                    """Handle /brief [domain] — triggers brief generation (all due domains)."""
                    try:
                        sched = getattr(app.state, "kb_scheduler", None)
                        if sched is None:
                            return "KB scheduler not running. Try the web dashboard."
                        import asyncio as _aio
                        user_id = _resolve_nostr_user_id()
                        if not user_id:
                            return "Could not resolve user."
                        # Generates briefs for all domains with new content since last brief.
                        # Individual domain filtering is handled inside the scheduler.
                        _aio.create_task(
                            sched._generate_briefs_for_user(user_id),
                            name="brief-nostr-on-demand",
                        )
                        hint = f" (domain: {cmd_parts[1]})" if len(cmd_parts) > 1 else ""
                        return f"Brief generation queued{hint}. You'll receive the brief(s) shortly via Nostr DM."
                    except Exception as exc:
                        logger.warning("Brief command failed: %s", exc)
                        return "Brief generation failed. Try the web dashboard."

                async def _handle_remind_command(cmd_parts: list, sender_npub: str) -> str:
                    """Handle /remind <message> in <time> — schedules a reminder."""
                    if len(cmd_parts) < 2:
                        return (
                            "Usage: /remind <message> in <time>\n"
                            "Examples: /remind call Charlie in 2 hours\n"
                            "          /remind review PR in 30 minutes"
                        )
                    rest = " ".join(cmd_parts[1:])
                    user_id = _resolve_nostr_user_id()
                    if not user_id:
                        return "Could not resolve user."
                    try:
                        from skills.schedule_reminder.handler import execute as _sr
                        result = _sr({
                            "message": rest,
                            "trigger_expression": rest,
                            "user_id": user_id,
                        })
                        if result.get("trigger_at_local"):
                            return f"Reminder set for {result['trigger_at_local']}: {result.get('message', rest)}"
                        return f"Reminder scheduled: {rest}"
                    except Exception as exc:
                        logger.warning("Remind command failed: %s", exc)
                        return f"Could not schedule reminder. Try: /remind <message> in <time>"

                async def _handle_task_command(cmd_parts: list) -> str:
                    """Handle /task <title> [due <date>] — creates a task."""
                    if len(cmd_parts) < 2:
                        return "Usage: /task <title> [due YYYY-MM-DD]"
                    rest = " ".join(cmd_parts[1:])
                    user_id = _resolve_nostr_user_id()
                    if not user_id:
                        return "Could not resolve user."
                    # Extract optional due date
                    due_date = None
                    title = rest
                    import re as _re
                    m = _re.search(r'\bdue\s+(\d{4}-\d{2}-\d{2})\b', rest, _re.IGNORECASE)
                    if m:
                        due_date = m.group(1)
                        title = rest[:m.start()].strip() or rest
                    try:
                        from skills.manage_task.handler import execute as _mt
                        result = _mt({
                            "action": "create",
                            "title": title,
                            "due_date": due_date,
                            "user_id": user_id,
                        })
                        tid = result.get("task_id", "")[:8]
                        msg = f"Task created [{tid}]: {title}"
                        if due_date:
                            msg += f" (due {due_date})"
                        return msg
                    except Exception as exc:
                        logger.warning("Task command failed: %s", exc)
                        return "Could not create task."

                async def _handle_tasks_command(cmd_parts: list) -> str:
                    """Handle /tasks [open|all] — lists tasks."""
                    status_filter = "open"
                    if len(cmd_parts) > 1 and cmd_parts[1].lower() == "all":
                        status_filter = None
                    user_id = _resolve_nostr_user_id()
                    if not user_id:
                        return "Could not resolve user."
                    try:
                        from skills.manage_task.handler import execute as _mt
                        result = _mt({
                            "action": "list",
                            "status": status_filter,
                            "user_id": user_id,
                        })
                        tasks = result.get("tasks", [])
                        if not tasks:
                            label = "open " if status_filter == "open" else ""
                            return f"No {label}tasks."
                        lines = [f"Tasks ({len(tasks)}):"]
                        for t in tasks[:10]:
                            due = f" — due {t['due_date']}" if t.get("due_date") else ""
                            lines.append(f"• [{t['id'][:6]}] {t['title']} [{t['status']}]{due}")
                        if len(tasks) > 10:
                            lines.append(f"… and {len(tasks) - 10} more. Use the web UI for full list.")
                        return "\n".join(lines)
                    except Exception as exc:
                        logger.warning("Tasks command failed: %s", exc)
                        return "Could not list tasks."

                # ── KB-27: NIP-17 session + conversation persistence helpers ──

                def _get_or_create_nostr_session(sender_npub: str) -> tuple:
                    """Return (conv_id, is_new) for sender, creating a new session if needed."""
                    import time as _time
                    now = _time.time()
                    session = _nip17_sessions.get(sender_npub)
                    if session and (now - session["last_at"]) < _NIP17_SESSION_GAP_SECS:
                        session["last_at"] = now
                        return session["conv_id"], False
                    # New session (first contact or gap exceeded)
                    conv_id = f"nostr_{sender_npub[:8]}_{int(now)}"
                    _nip17_sessions[sender_npub] = {"conv_id": conv_id, "last_at": now}
                    return conv_id, True

                def _load_history_from_db(user_id: str, n_turns: int = 20) -> list:
                    """Load last n_turns from conversation_messages across all channels (web+nip17)."""
                    import sqlite3 as _sql
                    _db = str(get_db_path())
                    try:
                        conn = _sql.connect(_db)
                        conn.row_factory = _sql.Row
                        rows = conn.execute(
                            """
                            SELECT cm.role, cm.content_ctx
                            FROM conversation_messages cm
                            JOIN conversations c ON c.id = cm.conv_id
                            WHERE c.user_id = ?
                              AND (cm.expires_at IS NULL OR cm.expires_at > datetime('now'))
                            ORDER BY cm.created_at DESC
                            LIMIT ?
                            """,
                            (user_id, n_turns * 2),
                        ).fetchall()
                        conn.close()
                        return [{"role": r["role"], "content": r["content_ctx"] or ""} for r in reversed(rows)]
                    except Exception as exc:
                        logger.debug("_load_history_from_db failed: %s", exc)
                        return []

                def _upsert_nostr_conversation(user_id: str, conv_id: str,
                                               sender_npub: str, title: str) -> None:
                    """Upsert a conversations row for a NIP-17 session."""
                    import sqlite3 as _sql
                    _db = str(get_db_path())
                    now_iso = __import__('datetime').datetime.utcnow().isoformat()
                    try:
                        conn = _sql.connect(_db)
                        conn.execute("""
                            INSERT INTO conversations
                                (id, user_id, title, source, nostr_npub,
                                 memory_scope, message_count, started_at, last_active)
                            VALUES (?, ?, ?, 'nip17', ?,
                                    'off', 0, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                last_active = excluded.last_active,
                                message_count = message_count + 2,
                                title = COALESCE(NULLIF(conversations.title, ''), excluded.title)
                        """, (conv_id, user_id, title[:80], sender_npub, now_iso, now_iso))
                        conn.commit()
                        conn.close()
                    except Exception as exc:
                        logger.warning("Nostr conversation upsert failed: %s", exc)

                async def _sync_nip17_messages(conv_id: str, user_id: str,
                                               user_msg: str, ai_msg: str,
                                               seq_start: int) -> None:
                    """Fire-and-forget: persist NIP-17 exchange to conversation_messages."""
                    import sqlite3 as _sql
                    import uuid as _uuid
                    _db = str(get_db_path())
                    now_iso = __import__('datetime').datetime.utcnow().isoformat()
                    # Default retention: 14 days
                    expires_iso = (
                        __import__('datetime').datetime.utcnow()
                        + __import__('datetime').timedelta(days=14)
                    ).isoformat()

                    def _strip_ctx(text: str) -> str:
                        import re as _re
                        _PL = _re.compile(r'@startuml.*?@enduml', _re.DOTALL | _re.IGNORECASE)
                        _BM = _re.compile(r'<definitions[^>]*>.*?</definitions>', _re.DOTALL | _re.IGNORECASE)
                        _CL = _re.compile(r'```[a-z]*\n(.*?)```', _re.DOTALL)
                        ctx = _PL.sub('[PlantUML diagram]', text)
                        ctx = _BM.sub('[BPMN diagram]', ctx)
                        def _shorten(m):
                            lines = m.group(1).count('\n')
                            return f'```[code block, {lines} lines]```' if lines > 30 else m.group(0)
                        ctx = _CL.sub(_shorten, ctx)
                        if len(ctx) > 8000:
                            ctx = ctx[:8000] + '…[truncated]'
                        return ctx

                    try:
                        conn = _sql.connect(_db)
                        conn.execute("""
                            INSERT OR IGNORE INTO conversation_messages
                                (id, conv_id, seq, role, content, content_ctx, created_at, expires_at)
                            VALUES (?, ?, ?, 'user', ?, ?, ?, ?)
                        """, (_uuid.uuid4().hex, conv_id, seq_start,
                              user_msg, _strip_ctx(user_msg), now_iso, expires_iso))
                        conn.execute("""
                            INSERT OR IGNORE INTO conversation_messages
                                (id, conv_id, seq, role, content, content_ctx, created_at, expires_at)
                            VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?)
                        """, (_uuid.uuid4().hex, conv_id, seq_start + 1,
                              ai_msg, _strip_ctx(ai_msg), now_iso, expires_iso))
                        conn.commit()
                        conn.close()
                    except Exception as exc:
                        logger.warning("NIP-17 message sync failed: %s", exc)

                async def handle_nostr_chat(sender_npub: str, message: str) -> None:
                    """6E.2 — Orchestrator-backed Nostr chat handler with KB-12 command routing."""
                    logger.info(f"Nostr chat from {sender_npub[:8]}... ({len(message)} chars)")
                    try:
                        # ── KB-12: Command routing ─────────────────────────────────────
                        stripped = message.strip()
                        if stripped.startswith("/"):
                            cmd_parts = stripped.split()
                            cmd = cmd_parts[0].lower()

                            if cmd == "/memory":
                                reply = await _handle_memory_command(cmd_parts, sender_npub)
                            elif cmd == "/facts":
                                reply = await _handle_facts_command()
                            elif cmd == "/forget":
                                reply = await _handle_forget_command(cmd_parts)
                            elif cmd == "/search":
                                reply = await _handle_search_command(cmd_parts)
                            elif cmd == "/brief":
                                reply = await _handle_brief_command(cmd_parts)
                            elif cmd == "/remind":
                                reply = await _handle_remind_command(cmd_parts, sender_npub)
                            elif cmd == "/task":
                                reply = await _handle_task_command(cmd_parts)
                            elif cmd == "/tasks":
                                reply = await _handle_tasks_command(cmd_parts)
                            elif cmd == "/new":
                                # KB-27: force a new NIP-17 session
                                _nip17_sessions.pop(sender_npub, None)
                                _nip17_histories.pop(sender_npub, None)
                                reply = "New conversation started. Your previous context has been cleared."
                            elif cmd == "/help":
                                reply = (
                                    "Available commands:\n"
                                    "/search <query>          — search the knowledge base\n"
                                    "/brief <domain>          — generate a brief\n"
                                    "/remind <msg> in <time>  — set a reminder (e.g. in 2 hours)\n"
                                    "/task <title> [due DATE] — create a task\n"
                                    "/tasks [open|all]        — list your tasks\n"
                                    "/memory on|off           — enable/disable fact memory for this session\n"
                                    "/facts                   — list stored facts about you\n"
                                    "/forget <id>             — delete a stored fact\n"
                                    "/new                     — start a fresh conversation (clear context)\n"
                                    "/status                  — KB ingestion status\n"
                                    "/help                    — this message\n"
                                    "\nOr just send a message to chat with the AI."
                                )
                            elif cmd == "/status":
                                reply = "KB scheduler is running. Use the web dashboard for detailed status."
                            else:
                                reply = f"Unknown command '{cmd}'. Send /help for available commands."

                            if reply:
                                await service.send_message(reply)
                            return
                        # ── End command routing ────────────────────────────────────────

                        # ── KB-27: session management ──────────────────────────────────
                        conv_id, _is_new_session = _get_or_create_nostr_session(sender_npub)
                        # ── End KB-27 session ──────────────────────────────────────────

                        history = _nip17_histories.setdefault(sender_npub, [])
                        # On new session, seed history from DB (web + nip17 last 14 days)
                        if _is_new_session and not history:
                            _uid_for_hist = _resolve_nostr_user_id()
                            if _uid_for_hist:
                                _db_hist = _load_history_from_db(_uid_for_hist, n_turns=20)
                                if _db_hist:
                                    _nip17_histories[sender_npub] = _db_hist
                                    history = _nip17_histories[sender_npub]
                                    logger.info("Seeded NIP-17 history from DB: %d turns", len(_db_hist) // 2)

                        # Resolve NIP-17 model preference (any user's nip17 row)
                        model_id = None
                        provider_name = None
                        try:
                            from database.connection import get_database_connection
                            db = get_database_connection()
                            rows = db.execute_query(
                                "SELECT provider_name, model_id FROM user_model_preferences "
                                "WHERE route = 'nip17' ORDER BY updated_at DESC LIMIT 1",
                                (),
                            )
                            if rows:
                                model_id = rows[0]["model_id"]
                                provider_name = rows[0]["provider_name"]
                        except Exception as exc:
                            logger.warning(f"Could not load NIP-17 model preference: {exc}")

                        # ── KB-13: Build user profile snippet ─────────────────────
                        _user_profile_snippet = ""
                        try:
                            from kb.assistant_context import AssistantContext
                            _nostr_uid = _resolve_nostr_user_id()
                            if _nostr_uid:
                                _asst_ctx = AssistantContext.build(
                                    user_id=_nostr_uid,
                                    session_id=f"nostr_{sender_npub[:16]}",
                                    message=message,
                                )
                                _user_profile_snippet = _asst_ctx.system_prompt_snippet
                        except Exception as _asst_exc:
                            logger.debug("AssistantContext build failed: %s", _asst_exc)
                        # ── End KB-13 ──────────────────────────────────────────────

                        # Build context string from last 20 turns
                        context = ""
                        if history:
                            turns = [
                                f"{m.get('role', 'user')}: {m.get('content', '')}"
                                for m in history[-20:]
                            ]
                            context = "\n".join(turns)
                        if _user_profile_snippet:
                            context = _user_profile_snippet + "\n\n" + context if context else _user_profile_snippet

                        from agents.factory import create_orchestrator
                        from database.connection import get_database_connection as _get_db
                        _nip17_pii = False
                        try:
                            _db = _get_db()
                            _pii_rows = _db.execute_query(
                                "SELECT pii_scrubbing_enabled FROM users LIMIT 1", ()
                            )
                            if _pii_rows:
                                _nip17_pii = bool(_pii_rows[0]["pii_scrubbing_enabled"])
                        except Exception as _exc:
                            logger.warning(f"Could not load NIP-17 PII scrubbing preference: {_exc}")
                        orchestrator = create_orchestrator(
                            infra=app.state.agent_infra,
                            model_id=model_id or None,
                            provider_name=provider_name or None,
                            user_pii_scrubbing=_nip17_pii,
                            extra_skill_grants=frozenset({"web_search"}),
                        )
                        result = await orchestrator.run(task=message, context=context)

                        # Update per-sender history (bounded to 40 entries = 20 turns)
                        # seq_start counts only NEW messages in this conv_id (not seeded DB history)
                        seq_start = _nip17_conv_seq.get(conv_id, 0)
                        history.append({"role": "user", "content": message})
                        history.append({"role": "assistant", "content": result.output})
                        _nip17_histories[sender_npub] = history[-40:]
                        _nip17_conv_seq[conv_id] = seq_start + 2

                        if result.output:
                            await service.send_message(result.output)
                            logger.info(f"Sent AI reply ({len(result.output)} chars) via Nostr")
                        else:
                            logger.warning("AI reply was empty (LLM failure?) — skipping Nostr send")

                        # ── KB-27: persist conversation to DB (fire-and-forget) ─────────
                        import asyncio as _aio27
                        _nostr_uid_27 = _resolve_nostr_user_id()
                        if _nostr_uid_27:
                            _upsert_nostr_conversation(
                                _nostr_uid_27, conv_id, sender_npub,
                                title=message[:80],
                            )
                            _aio27.create_task(
                                _sync_nip17_messages(
                                    conv_id, _nostr_uid_27,
                                    message, result.output or "",
                                    seq_start,
                                ),
                                name=f"nip17-sync-{conv_id[-8:]}",
                            )
                        # ── End KB-27 ─────────────────────────────────────────────────────

                        # ── KB-12: Trigger fact extraction every N turns ───────────────
                        import asyncio as _aio
                        total_turns = len(_nip17_histories[sender_npub])
                        if total_turns % 10 == 0:  # every 5 turns = 10 history entries
                            user_id = _resolve_nostr_user_id()
                            session_id = f"nostr_{sender_npub[:16]}"
                            if user_id:
                                from kb.fact_extraction import get_fact_extraction_job
                                job = get_fact_extraction_job()
                                _aio.create_task(
                                    _aio.to_thread(
                                        job.process_session_batch,
                                        user_id,
                                        session_id,
                                        _nip17_histories[sender_npub],
                                    ),
                                    name=f"fact-extract-{sender_npub[:8]}",
                                )
                        # ── End fact extraction ───────────────────────────────────────

                    except Exception as e:
                        logger.error(f"AI chat handler failed: {e}")

                service.register_message_handler(handle_nostr_chat)
                logger.info("AI chat handler registered for Nostr messages")
        else:
            logger.warning("Failed to start Nostr NIP-17 Communication Service - continuing without it")

        logger.info("Application started successfully")
        yield
        logger.info("Application shutting down...")

        # Stop audit log (flushes remaining queue before exit)
        await audit_log.stop()
        logger.info("Audit log stopped")

        # Stop KB scheduler
        if hasattr(app.state, "kb_scheduler"):
            app.state.kb_scheduler.stop()
            if hasattr(app.state, "kb_scheduler_task"):
                app.state.kb_scheduler_task.cancel()
                try:
                    await app.state.kb_scheduler_task
                except (asyncio.CancelledError, Exception):
                    pass
            logger.info("KB scheduler stopped")

        # Stop NIP-17 service
        logger.info("Stopping Nostr NIP-17 Communication Service...")
        await stop_nostr_service()
        logger.info("Nostr NIP-17 Communication Service stopped")
