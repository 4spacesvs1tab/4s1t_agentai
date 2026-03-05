"""
Main entry point for the 4S1T Agent AI system.
"""
import uvicorn
from fastapi import FastAPI, HTTPException, Depends
from typing import Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
import os
from contextlib import asynccontextmanager

from database.connection import DatabaseConnection
from core.audit import get_audit_log
from api.auth_routes import router as auth_router
from api.web_routes import router as web_router
from api.mobile_routes import router as mobile_router
from api.agent_routes import router as agent_router
from api.api_key_routes import router as api_key_router
from api.mfa_routes import router as mfa_router
from api.preference_routes import router as preference_router
from api.security_dependencies import require_2fa
from config.settings import Settings
from components.system.initializer import system_initializer, system_lifespan
from services.auth_service import get_auth_service
from services.nostr_service import NostrCommunicationService, start_nostr_service, stop_nostr_service
from vector_database.service import get_vector_database_service
from utils.logger import setup_logger

# Initialize logger
logger = setup_logger(__name__)

# Initialize settings
settings = Settings()


class ContentSizeLimitMiddleware:
    """ASGI middleware that enforces per-route request body size limits.

    Auth routes (/auth/*) are limited to 64 KB.
    All other routes are limited to 1 MB.
    Rejects oversized requests with HTTP 413 before the body reaches handlers.
    """

    AUTH_LIMIT = 64 * 1_024          # 64 KB
    DEFAULT_LIMIT = 1 * 1_024 * 1_024  # 1 MB

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        limit = self.AUTH_LIMIT if path.startswith("/auth/") else self.DEFAULT_LIMIT

        # Fast rejection based on Content-Length header (avoids buffering when possible)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        cl_header = headers.get(b"content-length")
        if cl_header:
            try:
                if int(cl_header) > limit:
                    await self._send_413(send)
                    return
            except (ValueError, TypeError):
                pass

        # Buffer the incoming body while enforcing the byte limit
        body_parts: list[bytes] = []
        total = 0
        more_body = True

        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > limit:
                    await self._send_413(send)
                    return
                body_parts.append(chunk)
                more_body = message.get("more_body", False)
            else:
                # http.disconnect or unexpected — pass through unchanged
                more_body = False

        full_body = b"".join(body_parts)

        # Replay the buffered body to downstream handlers
        body_consumed = False

        async def replay_receive():
            nonlocal body_consumed
            if not body_consumed:
                body_consumed = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            # Forward to the real receive so Starlette's disconnect monitor
            # waits for an actual client disconnect instead of firing immediately.
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _send_413(send) -> None:
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"detail":"Request body too large"}',
            "more_body": False,
        })


class SecurityHeadersMiddleware:
    """Add security headers to every HTTP response.

    Pure-ASGI implementation (no BaseHTTPMiddleware) so that long-running
    streaming responses (SSE) are never cancelled by middleware task-group
    scope exit — a known BaseHTTPMiddleware limitation.
    """

    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self' wss:;"
    )

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Frame-Options"] = "DENY"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["X-XSS-Protection"] = "1; mode=block"
                headers["Content-Security-Policy"] = self._CSP
                headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


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
        nostr_config_path = os.path.join(os.path.dirname(__file__), "..", "config", "nostr_nip17.yaml")
        nostr_service_started = await start_nostr_service(config_path=nostr_config_path)
        
        if nostr_service_started:
            logger.info("Nostr NIP-17 Communication Service started successfully")
            from services.nostr_service import get_nostr_service
            service = get_nostr_service()
            if service:
                logger.info(f"Active relay: {service.chat_agent.client.active_relay if service.chat_agent else 'N/A'}")

                # Per-sender conversation history keyed by sender_npub
                _nip17_histories: Dict[str, Any] = {}

                async def handle_nostr_chat(sender_npub: str, message: str) -> None:
                    """6E.2 — Orchestrator-backed Nostr chat handler."""
                    logger.info(f"Nostr chat from {sender_npub[:8]}... ({len(message)} chars)")
                    try:
                        history = _nip17_histories.setdefault(sender_npub, [])

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

                        # Build context string from last 20 turns
                        context = ""
                        if history:
                            turns = [
                                f"{m.get('role', 'user')}: {m.get('content', '')}"
                                for m in history[-20:]
                            ]
                            context = "\n".join(turns)

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
                        )
                        result = await orchestrator.run(task=message, context=context)

                        # Update per-sender history (bounded to 40 entries = 20 turns)
                        history.append({"role": "user", "content": message})
                        history.append({"role": "assistant", "content": result.output})
                        _nip17_histories[sender_npub] = history[-40:]

                        if result.output:
                            await service.send_message(result.output)
                            logger.info(f"Sent AI reply ({len(result.output)} chars) via Nostr")
                        else:
                            logger.warning("AI reply was empty (LLM failure?) — skipping Nostr send")
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

        # Stop NIP-17 service
        logger.info("Stopping Nostr NIP-17 Communication Service...")
        await stop_nostr_service()
        logger.info("Nostr NIP-17 Communication Service stopped")


# Create FastAPI app with lifespan
app = FastAPI(
    title="4S1T Agent AI",
    description="An AI Agent system for IT Business Analysts and Data Analysts",
    version="0.1.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Add content size limit (outermost — enforced before any other middleware or handler)
app.add_middleware(ContentSizeLimitMiddleware)

# Mount static files directory
static_dir = os.path.join(os.path.dirname(__file__), "web", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include routers
app.include_router(auth_router)
app.include_router(web_router)
app.include_router(mobile_router)
app.include_router(agent_router)
app.include_router(api_key_router)
app.include_router(mfa_router)
app.include_router(preference_router)


@app.get("/")
async def root():
    """Root endpoint returning system information."""
    return {
        "message": "4S1T Agent AI System",
        "version": "0.1.0",
        "status": "operational"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "components": {
            "api": "operational"
        }
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
