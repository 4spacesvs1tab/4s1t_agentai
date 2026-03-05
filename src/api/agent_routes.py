"""
Agent API routes for 4S1T Agent AI system.
Provides REST API endpoints for other agents to interact with the system.
"""
from typing import Dict, Any, Optional, List
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status, Header
from pydantic import BaseModel as PydanticBaseModel

from api.security_dependencies import require_2fa
from services.api_key_service import get_api_key_service
from database.connection import get_database_connection
from agents.factory import create_orchestrator
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Create router
router = APIRouter(prefix="/api/v1", tags=["agent-api"])

# ---------------------------------------------------------------------------
# 6F.2 — In-memory conversation store for REST API calls.
# Keyed by conversation_id (client-generated UUID).
# Bounded to last 40 entries (20 turns) per conversation.
# Lost on server restart — acceptable for Phase 1.
# ---------------------------------------------------------------------------
_api_conversations: Dict[str, List[Dict[str, Any]]] = {}

# Pydantic models for API requests/responses
class AgentRequest(PydanticBaseModel):
    """Request model for agent API calls."""
    action: str
    parameters: Dict[str, Any] = {}
    context: Optional[Dict[str, Any]] = None
    conversation_id: Optional[str] = None  # 6F.2 — client-generated UUID for multi-turn context


class AgentResponse(PydanticBaseModel):
    """Response model for agent API calls."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = {}


class ToolExecutionRequest(PydanticBaseModel):
    """Request model for tool execution."""
    tool_name: str
    arguments: Dict[str, Any]


class ToolExecutionResponse(PydanticBaseModel):
    """Response model for tool execution."""
    tool_name: str
    result: Any
    success: bool
    error: Optional[str] = None


# Dependency for API token authentication
async def verify_api_token(authorization: str = Header(None)) -> Dict[str, Any]:
    """Validate Bearer token against DB. Returns key_info dict with user_id, scopes,
    provider_override, model_override, etc. on success."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing"
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format"
        )

    plain_key = authorization[len("Bearer "):]
    if not plain_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )

    key_info = get_api_key_service().validate_api_key(plain_key)
    if key_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key"
        )

    return key_info


async def verify_session_or_api_token(
    request: Request,
    authorization: str = Header(None),
) -> Dict[str, Any]:
    """Dual-auth: accept a Bearer API key OR a browser session JWT (cookie/header).

    Priority:
    1. Bearer API key — if Authorization header present and validates against api_keys DB.
    2. Session JWT  — from Authorization: Bearer <jwt> header or access_token cookie.
    """
    # 1. Try to validate as API key when a Bearer header is present
    if authorization and authorization.startswith("Bearer "):
        plain_key = authorization[len("Bearer "):]
        key_info = get_api_key_service().validate_api_key(plain_key)
        if key_info is not None:
            return key_info

    # 2. Fall back to session JWT (cookie or Authorization header)
    from api.security_dependencies import require_auth
    return await require_auth(request)


@router.get("")
@router.get("/")
async def api_root():
    """
    API root — returns an OpenAI-compatible model list.

    Some clients (e.g. Goose) use the base URL directly as the models
    endpoint instead of appending /models.  This endpoint satisfies both
    patterns without requiring authentication.
    """
    from config.provider_config import load_all_providers

    all_model_ids: list = []
    seen: set = set()
    try:
        providers = load_all_providers()
        for _name, prov in providers.items():
            for _pref, model_id in prov.models.items():
                if model_id and model_id not in seen:
                    seen.add(model_id)
                    all_model_ids.append(
                        {"id": model_id, "object": "model", "created": 0, "owned_by": "system"}
                    )
    except Exception:
        pass  # return empty list on error rather than 500

    return {"object": "list", "data": all_model_ids}


@router.get("/status")
async def get_system_status():
    """Get system status information."""
    import os as _os
    import sqlite3 as _sqlite3

    try:
        from config.provider_config import get_active_provider as _get_provider
        _provider = _get_provider()
        api_provider_status = "operational" if _provider.api_key else "degraded"
    except Exception:
        api_provider_status = "operational" if _os.getenv("NANO_GPT_API_KEY") else "degraded"

    try:
        from config.settings import settings as _settings
        db_path = _settings.DATABASE_URL.replace("sqlite:///", "")
        conn = _sqlite3.connect(db_path, timeout=2.0)
        conn.execute("SELECT 1")
        conn.close()
        auth_status = "operational"
    except Exception:
        auth_status = "unhealthy"

    return {
        "status": "operational",
        "version": "0.1.0",
        "components": {
            "api": "operational",
            "nanogpt": api_provider_status,
            "auth": auth_status,
            "templates": "operational",
        },
    }


@router.post("/execute", response_model=AgentResponse)
async def execute_agent_request(
    body: AgentRequest,
    http_request: Request,
    key_info: Dict[str, Any] = Depends(verify_api_token),
):
    """
    6F.1 — Execute an agent task via the real OrchestratorAgent.

    Model resolution order:
      1. API key's provider_override / model_override  (per-key setting)
      2. User's saved api_default preference in DB
      3. System active provider → general agent_preference[0]

    6F.2 — Pass conversation_id to maintain multi-turn context across calls.

    The ``action`` field becomes the task description sent to the orchestrator.
    Any ``parameters`` are appended as JSON so the agent can read them.
    """
    user_id = key_info["user_id"]
    logger.info(f"REST API execute: action={body.action!r}  user={user_id}")

    # --- 1. resolve model + provider ----------------------------------------
    # Priority: key override → user api_default pref → system default (None)
    model_id: Optional[str] = key_info.get("model_override")
    provider_name: Optional[str] = key_info.get("provider_override")

    if not model_id or not provider_name:
        try:
            db = get_database_connection()
            rows = db.execute_query(
                "SELECT provider_name, model_id FROM user_model_preferences "
                "WHERE user_id = ? AND route = 'api_default'",
                (user_id,),
            )
            if rows:
                if not provider_name and rows[0]["provider_name"]:
                    provider_name = rows[0]["provider_name"]
                if not model_id and rows[0]["model_id"]:
                    model_id = rows[0]["model_id"]
        except Exception as exc:
            logger.warning(f"Could not load api_default preference for user {user_id}: {exc}")

    # --- 2. build task description -------------------------------------------
    import json as _json  # stdlib, safe to import inline
    task = body.action
    if body.parameters:
        task = f"{body.action}\nParameters: {_json.dumps(body.parameters)}"

    # --- 3. load conversation history (6F.2) ---------------------------------
    conv_id = body.conversation_id
    history: List[Dict[str, Any]] = []
    if conv_id:
        history = list(_api_conversations.get(conv_id, []))

    context = ""
    if history:
        turns = [
            f"{m.get('role', 'user')}: {m.get('content', '')}"
            for m in history[-20:]
        ]
        context = "\n".join(turns)

    # --- 4. get shared infrastructure ----------------------------------------
    try:
        infra = http_request.app.state.agent_infra
    except AttributeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent infrastructure not initialised",
        )

    # --- 5. create orchestrator and run --------------------------------------
    user_pii_scrubbing = False
    try:
        db = get_database_connection()
        _pii_rows = db.execute_query(
            "SELECT pii_scrubbing_enabled FROM users WHERE id = ?", (user_id,)
        )
        if _pii_rows:
            user_pii_scrubbing = bool(_pii_rows[0]["pii_scrubbing_enabled"])
    except Exception as exc:
        logger.warning(f"Could not load PII scrubbing preference for user {user_id}: {exc}")

    try:
        orchestrator = create_orchestrator(
            infra=infra,
            model_id=model_id or None,
            provider_name=provider_name or None,
            user_pii_scrubbing=user_pii_scrubbing,
        )
        result = await orchestrator.run(task=task, context=context)
    except Exception as exc:
        logger.error(f"Orchestrator error (user={user_id}): {exc}", exc_info=True)
        return AgentResponse(
            success=False,
            error=str(exc),
            metadata={"action": body.action},
        )

    # --- 6. update conversation history (6F.2) --------------------------------
    if conv_id:
        history.append({"role": "user", "content": task})
        history.append({"role": "assistant", "content": result.output})
        _api_conversations[conv_id] = history[-40:]  # bounded to 20 turns

    return AgentResponse(
        success=True,
        data={"output": result.output, "workflow_id": result.workflow_id},
        metadata={"action": body.action, "conversation_id": conv_id},
    )


@router.get("/tools")
async def list_available_tools(_key: Dict[str, Any] = Depends(verify_api_token)):
    """List available MCP tools."""
    # In a real implementation, this would query the MCP server
    return {
        "tools": [
            {
                "name": "calculator",
                "description": "Performs basic arithmetic operations",
                "parameters": {
                    "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
                    "a": {"type": "number"},
                    "b": {"type": "number"}
                }
            },
            {
                "name": "echo",
                "description": "Echoes back the input text",
                "parameters": {
                    "text": {"type": "string"}
                }
            }
        ]
    }


@router.post("/tools/execute", response_model=ToolExecutionResponse)
async def execute_tool(
    request: ToolExecutionRequest,
    _key: Dict[str, Any] = Depends(verify_api_token)
):
    """
    Execute an MCP tool.
    
    Args:
        request: Tool execution request
        token: API token for authentication
        
    Returns:
        ToolExecutionResponse with the result
    """
    try:
        logger.info(f"Executing tool: {request.tool_name}")
        
        # This would integrate with the MCP system
        result = await simulate_tool_execution(request.tool_name, request.arguments)
        
        return ToolExecutionResponse(
            tool_name=request.tool_name,
            result=result,
            success=True
        )
        
    except Exception as e:
        logger.error(f"Error executing tool {request.tool_name}: {e}")
        return ToolExecutionResponse(
            tool_name=request.tool_name,
            result=None,
            success=False,
            error=str(e)
        )


async def simulate_tool_execution(tool_name: str, arguments: Dict[str, Any]) -> Any:
    """
    Simulate tool execution (would integrate with MCP in real implementation).
    
    Args:
        tool_name: Name of the tool to execute
        arguments: Arguments for the tool
        
    Returns:
        Tool execution result
    """
    if tool_name == "calculator":
        operation = arguments.get("operation")
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        
        if operation == "add":
            return a + b
        elif operation == "subtract":
            return a - b
        elif operation == "multiply":
            return a * b
        elif operation == "divide":
            return a / b if b != 0 else "Cannot divide by zero"
        else:
            raise ValueError(f"Unknown operation: {operation}")
    
    elif tool_name == "echo":
        return {"echo": arguments.get("text", ""), "received_at": "2025-12-08T21:00:00Z"}
    
    else:
        raise ValueError(f"Unknown tool: {tool_name}")


@router.get("/profile")
async def get_agent_profile(_key: Dict[str, Any] = Depends(verify_api_token)):
    """Get agent profile information."""
    # In a real implementation, this would return authenticated user info
    return {
        "agent_id": "agent_001",
        "name": "External Agent",
        "permissions": ["read_tools", "execute_tools"],
        "created_at": "2025-12-08T21:00:00Z"
    }


# Models API for web UI
class ModelInfoResponse(PydanticBaseModel):
    """Response model for model information."""
    id: str
    name: str
    provider: str            # display name shown in UI
    provider_id: Optional[str] = None  # YAML key / slug used by backend routing
    capabilities: List[str]
    context_window: int
    is_pro_model: bool
    description: str
    category: Optional[str]
    max_tokens: int
    subscription_tier: str


class ModelsResponse(PydanticBaseModel):
    """Response model for models list."""
    models: List[ModelInfoResponse]
    total: int
    filters: Dict[str, Any]


@router.get("/providers")
async def list_providers(_key: Dict[str, Any] = Depends(verify_session_or_api_token)):
    """
    List all configured LLM providers from providers.yaml.

    Returns display metadata for each provider (no API keys exposed).
    """
    from config.provider_config import load_all_providers

    providers = load_all_providers()
    result = [
        {
            "name": name,
            "display_name": p.display_name,
            "base_url": p.base_url,
            "catalog_strategy": p.catalog_strategy,
            "model_filter": p.model_filter,
            "preferences": list(p.agent_preferences.keys()),
        }
        for name, p in providers.items()
    ]
    return {"providers": result, "total": len(result)}


@router.get("/providers/{provider_name}/models")
async def get_provider_models(
    provider_name: str,
    q: Optional[str] = None,
    _key: Dict[str, Any] = Depends(verify_session_or_api_token),
):
    """
    Return the live model list for *provider_name*.

    The list is fetched from the provider's ``GET /models`` endpoint (or from
    the static ``agent_preferences`` fallback) and cached for 1 hour.

    Optional ``?q=`` parameter performs a case-insensitive substring match on
    model IDs.
    """
    from config.provider_config import load_all_providers
    from services.model_catalog_service import get_model_catalog_service

    providers = load_all_providers()
    if provider_name not in providers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider '{provider_name}' not found",
        )

    provider = providers[provider_name]
    service = get_model_catalog_service()

    try:
        models = await service.get_models(provider)
    except Exception as exc:
        logger.error(f"Error fetching models for provider '{provider_name}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch models: {exc}",
        )

    if q and q.strip():
        ql = q.strip().lower()
        models = [m for m in models if ql in m.get("id", "").lower()]

    return {
        "provider": provider_name,
        "display_name": provider.display_name,
        "models": models,
        "total": len(models),
        "filter": q or None,
    }


@router.get("/models")
async def list_available_models(
    filter_text: Optional[str] = None,
    subscription_tier: Optional[str] = None,
    type_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    provider: Optional[str] = None,
    _key: Dict[str, Any] = Depends(verify_session_or_api_token)
):
    """
    List available models from all configured LLM providers (providers.yaml).

    Aggregates the live model catalog from every provider so the chat UI
    model-selector can offer models across all providers simultaneously.
    """
    try:
        from config.provider_config import load_all_providers
        from services.model_catalog_service import get_model_catalog_service

        providers = load_all_providers()
        catalog = get_model_catalog_service()

        raw: list[dict] = []
        seen: set[tuple] = set()

        for prov in providers.values():
            # Optional provider filter (passed as query param)
            if provider and prov.name != provider:
                continue

            catalog_models = await catalog.get_models(prov)

            for m in catalog_models:
                model_id = m.get("id", "")
                if not model_id:
                    continue
                key = (prov.name, model_id)
                if key in seen:
                    continue
                seen.add(key)

                preference = m.get("preference", "general") or "general"
                raw.append({
                    "model_id": model_id,
                    "name": model_id,
                    "provider": prov.display_name,
                    "provider_id": prov.name,       # slug used by backend routing
                    "capabilities": ["text_generation"],
                    "context_window": 32768,
                    "is_pro_model": False,
                    "description": preference,
                    "category": preference,
                    "max_tokens": 4096,
                    "subscription_tier": "FREE",
                })

        # Simple text filter
        if filter_text and filter_text.strip().lower() not in ("all", ""):
            ft = filter_text.strip().lower()
            raw = [
                m for m in raw
                if ft in m["model_id"].lower()
                or ft in m["category"].lower()
                or ft in m["provider"].lower()
            ]

        model_responses = [
            ModelInfoResponse(
                id=m["model_id"],
                name=m["name"],
                provider=m["provider"],
                provider_id=m["provider_id"],
                capabilities=m["capabilities"],
                context_window=m["context_window"],
                is_pro_model=m["is_pro_model"],
                description=m["description"],
                category=m["category"],
                max_tokens=m["max_tokens"],
                subscription_tier=m["subscription_tier"],
            )
            for m in raw
        ]

        # OpenAI-compatible data field (for external clients like Goose)
        openai_data = [
            {"id": m.id, "object": "model", "created": 0, "owned_by": "system"}
            for m in model_responses
        ]

        return {
            # OpenAI-compatible fields
            "object": "list",
            "data": openai_data,
            # 4S1T internal fields (used by model-selector.js)
            "models": [m.model_dump() for m in model_responses],
            "total": len(model_responses),
            "filters": {
                "filter_text": filter_text,
                "subscription_tier": subscription_tier,
                "type_filter": type_filter,
                "category_filter": category_filter,
                "provider": provider,
            },
        }

    except Exception as e:
        logger.error(f"Error listing models: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list models: {str(e)}",
        )


# ---------------------------------------------------------------------------
# OpenAI-compatible chat completions (for external agents like Goose)
# ---------------------------------------------------------------------------

class ChatCompletionMessage(PydanticBaseModel):
    role: str
    content: str


class ChatCompletionsRequest(PydanticBaseModel):
    model: str
    messages: List[ChatCompletionMessage]
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


@router.post("")
@router.post("/")
@router.post("/chat/completions")
async def openai_chat_completions(
    body: ChatCompletionsRequest,
    request: Request,
    key_info: Dict[str, Any] = Depends(verify_session_or_api_token),
):
    """
    OpenAI-compatible chat completions endpoint.

    Accepts the standard OpenAI messages format and returns a compatible
    response, making 4S1T usable as a drop-in provider in tools like Goose.

    Supports both streaming (SSE) and non-streaming responses.
    Auth: Bearer API key or session JWT.
    """
    import uuid as _uuid
    import time as _time
    import json as _json
    from fastapi.responses import StreamingResponse

    user_id = key_info.get("user_id") or key_info.get("id", "unknown")

    # Resolve model + provider (key override → request body → system default)
    model_id: Optional[str] = key_info.get("model_override") or body.model or None
    provider_name: Optional[str] = key_info.get("provider_override") or None

    logger.info(
        f"chat/completions: user={user_id}  model={model_id}  "
        f"stream={body.stream}  msgs={len(body.messages)}"
    )

    # Build task (last user message) and context (prior messages)
    messages = body.messages
    if not messages:
        raise HTTPException(status_code=422, detail="No messages provided")

    task = ""
    context_msgs: List[ChatCompletionMessage] = []
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user" and not task:
            task = messages[i].content
            context_msgs = list(messages[:i])
            break
    if not task:
        task = messages[-1].content

    context = "\n".join(f"{m.role}: {m.content}" for m in context_msgs[-20:])

    # Get shared infrastructure
    try:
        infra = request.app.state.agent_infra
    except AttributeError:
        raise HTTPException(status_code=503, detail="Agent infrastructure not initialised")

    completion_id = f"chatcmpl-{_uuid.uuid4().hex[:12]}"
    actual_model = model_id or body.model

    user_pii_scrubbing = False
    try:
        db = get_database_connection()
        _pii_rows = db.execute_query(
            "SELECT pii_scrubbing_enabled FROM users WHERE id = ?", (user_id,)
        )
        if _pii_rows:
            user_pii_scrubbing = bool(_pii_rows[0]["pii_scrubbing_enabled"])
    except Exception as exc:
        logger.warning(f"Could not load PII scrubbing preference for user {user_id}: {exc}")

    if body.stream:
        # Streaming: send one keepalive immediately (so client knows we're alive),
        # then await the orchestrator and stream the result word-by-word.
        async def _stream():
            created = int(_time.time())

            # Immediate keepalive so the client does not time out
            yield ": keepalive\n\n"

            try:
                orchestrator = create_orchestrator(
                    infra=infra,
                    model_id=model_id,
                    provider_name=provider_name,
                    user_pii_scrubbing=user_pii_scrubbing,
                )
                result = await orchestrator.run(task=task, context=context)
                response_text = result.output
            except Exception as exc:
                logger.error(
                    f"chat/completions stream error (user={user_id}): {exc}",
                    exc_info=True,
                )
                err = {"error": {"message": str(exc), "type": "agent_error"}}
                yield f"data: {_json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"
                return

            logger.info(
                f"chat/completions stream: response_len={len(response_text)}"
                f"  snippet={response_text[:80]!r}"
            )

            # First chunk: role + empty content (matches OpenAI SSE format exactly)
            first = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": actual_model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
            }
            yield f"data: {_json.dumps(first)}\n\n"

            # Content chunks (word by word)
            words = response_text.split(" ")
            for i, word in enumerate(words):
                chunk_content = word + (" " if i < len(words) - 1 else "")
                chunk = {
                    "id": completion_id, "object": "chat.completion.chunk",
                    "created": created, "model": actual_model,
                    "choices": [{"index": 0, "delta": {"content": chunk_content}, "finish_reason": None}],
                }
                yield f"data: {_json.dumps(chunk)}\n\n"

            # Final chunk
            final = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": actual_model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {_json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # Non-streaming: run orchestrator then return complete JSON
    try:
        orchestrator = create_orchestrator(
            infra=infra,
            model_id=model_id,
            provider_name=provider_name,
            user_pii_scrubbing=user_pii_scrubbing,
        )
        result = await orchestrator.run(task=task, context=context)
    except Exception as exc:
        logger.error(f"chat/completions orchestrator error (user={user_id}): {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}")

    response_text = result.output
    created = int(_time.time())
    logger.info(
        f"chat/completions non-stream: response_len={len(response_text)}"
        f"  snippet={response_text[:80]!r}"
    )

    prompt_tokens = sum(len(m.content.split()) for m in messages)
    completion_tokens = len(response_text.split())
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": actual_model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": response_text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# 6D.1 — WebUI chat endpoint
# ---------------------------------------------------------------------------

class ChatRequest(PydanticBaseModel):
    message: str
    model_id: Optional[str] = None
    provider_name: Optional[str] = None
    conversation_id: Optional[str] = None
    history: Optional[List[Dict[str, Any]]] = None


class ChatResponse(PydanticBaseModel):
    response: str
    conversation_id: Optional[str] = None
    workflow_id: Optional[str] = None


# ---------------------------------------------------------------------------
# 6E.3 — Trusted Nostr Contact management
# ---------------------------------------------------------------------------

class NostrContactCreate(PydanticBaseModel):
    npub: str
    name: Optional[str] = None
    alias: Optional[str] = None
    notes: Optional[str] = None


@router.get("/nostr/contacts")
async def list_nostr_contacts(_user: dict = Depends(require_2fa)):
    """List all Nostr contacts with their trust/block status."""
    db = get_database_connection()
    rows = db.execute_query(
        "SELECT id, npub, name, alias, is_trusted, is_blocked, notes, created_at "
        "FROM nostr_contacts ORDER BY created_at DESC",
        (),
    )
    return {"contacts": [dict(r) for r in rows]}


@router.post("/nostr/contacts", status_code=status.HTTP_201_CREATED)
async def add_nostr_contact(
    body: NostrContactCreate,
    _user: dict = Depends(require_2fa),
):
    """Add or re-trust a Nostr contact by npub."""
    if not body.npub.startswith("npub1") or len(body.npub) < 60:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid npub format",
        )
    db = get_database_connection()
    try:
        db.execute_command(
            """
            INSERT INTO nostr_contacts (npub, name, alias, notes, is_trusted, is_blocked)
            VALUES (?, ?, ?, ?, 1, 0)
            ON CONFLICT(npub) DO UPDATE SET
                name       = excluded.name,
                alias      = excluded.alias,
                notes      = excluded.notes,
                is_trusted = 1,
                is_blocked = 0,
                updated_at = CURRENT_TIMESTAMP
            """,
            (body.npub, body.name, body.alias, body.notes),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {exc}",
        )
    rows = db.execute_query(
        "SELECT id, npub, name, alias, is_trusted, is_blocked, notes, created_at "
        "FROM nostr_contacts WHERE npub = ?",
        (body.npub,),
    )
    return dict(rows[0]) if rows else {"status": "created"}


@router.delete("/nostr/contacts/{npub}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_nostr_contact(npub: str, _user: dict = Depends(require_2fa)):
    """Delete a Nostr contact (revokes trust)."""
    db = get_database_connection()
    db.execute_command("DELETE FROM nostr_contacts WHERE npub = ?", (npub,))


@router.post("/nostr/contacts/{npub}/block", status_code=status.HTTP_200_OK)
async def block_nostr_contact(npub: str, _user: dict = Depends(require_2fa)):
    """Block a Nostr contact — sets is_blocked=1, is_trusted=0."""
    db = get_database_connection()
    db.execute_command(
        """
        INSERT INTO nostr_contacts (npub, is_trusted, is_blocked)
        VALUES (?, 0, 1)
        ON CONFLICT(npub) DO UPDATE SET
            is_blocked = 1,
            is_trusted = 0,
            updated_at = CURRENT_TIMESTAMP
        """,
        (npub,),
    )
    return {"npub": npub, "status": "blocked"}


@router.post("/chat", response_model=ChatResponse)
async def webui_chat(
    body: ChatRequest,
    request: Request,
    current_user: dict = Depends(require_2fa),
):
    """
    WebUI chat endpoint.

    1. Resolves the user's saved WebUI model preference (falls back to the
       ``model_id`` supplied in the request body, then to system default).
    2. Creates a per-request OrchestratorAgent from shared infrastructure.
    3. Runs the orchestrator and returns the text response.
    """
    user_id = current_user["id"]

    # --- resolve model + provider -------------------------------------------
    model_id = body.model_id
    provider_name = body.provider_name

    try:
        db = get_database_connection()
        rows = db.execute_query(
            "SELECT provider_name, model_id FROM user_model_preferences "
            "WHERE user_id = ? AND route = 'webui'",
            (user_id,),
        )
        if rows:
            pref = rows[0]
            if not model_id and pref["model_id"]:
                model_id = pref["model_id"]
            if not provider_name and pref["provider_name"]:
                provider_name = pref["provider_name"]
    except Exception as exc:
        logger.warning(f"Could not load model preference for user {user_id}: {exc}")

    # --- build context string from history ----------------------------------
    context = ""
    if body.history:
        turns = []
        for msg in body.history[-20:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            turns.append(f"{role}: {content}")
        context = "\n".join(turns)

    # --- create orchestrator and run ----------------------------------------
    try:
        infra = request.app.state.agent_infra
    except AttributeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent infrastructure not initialised",
        )

    user_pii_scrubbing = False
    try:
        db = get_database_connection()
        _pii_rows = db.execute_query(
            "SELECT pii_scrubbing_enabled FROM users WHERE id = ?", (user_id,)
        )
        if _pii_rows:
            user_pii_scrubbing = bool(_pii_rows[0]["pii_scrubbing_enabled"])
    except Exception as exc:
        logger.warning(f"Could not load PII scrubbing preference for user {user_id}: {exc}")

    try:
        orchestrator = create_orchestrator(
            infra=infra,
            model_id=model_id or None,
            provider_name=provider_name or None,
            user_pii_scrubbing=user_pii_scrubbing,
        )
        result = await orchestrator.run(task=body.message, context=context)
    except Exception as exc:
        logger.error(f"Orchestrator error for user {user_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent error: {exc}",
        )

    return ChatResponse(
        response=result.output,
        conversation_id=body.conversation_id,
        workflow_id=result.workflow_id,
    )
