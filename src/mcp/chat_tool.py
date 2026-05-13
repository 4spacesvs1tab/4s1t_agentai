"""
Chat tool implementation for the 4S1T Agent AI system.
Provides conversational AI capabilities through the MCP framework.

Uses the active LLM provider configured via ACTIVE_PROVIDER env var
or the `active:` key in src/config/providers.yaml.
"""
import logging
import time
from typing import Dict, Any, Optional, List

from utils.logger import setup_logger

logger = setup_logger(__name__)

# In-memory conversation storage (no persistence, privacy-focused)
conversation_sessions: Dict[str, List[Dict[str, str]]] = {}
MAX_SESSION_MESSAGES = 100  # Max messages per session to prevent memory issues


def _build_messages(
    message: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Build an OpenAI-format messages list from optional history + current message."""
    messages: List[Dict[str, str]] = []
    if history:
        for msg in history[-20:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})
    return messages


def manage_conversation_session(
    conversation_id: str, message: str, response: str
) -> None:
    """Store a user/assistant exchange in the in-memory session."""
    if not conversation_id:
        return
    if conversation_id not in conversation_sessions:
        conversation_sessions[conversation_id] = []
    session = conversation_sessions[conversation_id]
    session.append({"role": "user", "content": message})
    session.append({"role": "assistant", "content": response})
    if len(session) > MAX_SESSION_MESSAGES:
        conversation_sessions[conversation_id] = session[-MAX_SESSION_MESSAGES:]


def get_session_history(conversation_id: str) -> List[Dict[str, str]]:
    """Return stored history for a session, or empty list."""
    if not conversation_id or conversation_id not in conversation_sessions:
        return []
    return conversation_sessions[conversation_id]


async def chat_tool_executor(arguments: Any) -> Dict[str, Any]:
    """
    Chat tool executor using the active LLM provider.

    Provider is selected by ACTIVE_PROVIDER env var → providers.yaml `active:` key
    → nano_gpt fallback.  The "general" model preference is used for chat.

    Args:
        arguments: Dict (or plain str) with optional keys:
            message        – user message (required)
            temperature    – float, default 0.7
            max_tokens     – int, default 2000
            history        – list of {role, content} dicts (client-side context)
            conversation_id – str for server-side session storage

    Returns:
        Dict with keys: response, model_used, provider, latency_ms,
                        conversation_id, context_messages
    """
    try:
        if isinstance(arguments, str):
            message = arguments
            temperature = 0.7
            max_tokens = 2000
            history = None
            conversation_id = None
            requested_model = None
        else:
            message = arguments.get("message", "")
            temperature = arguments.get("temperature", 0.7)
            max_tokens = arguments.get("max_tokens", 2000)
            history = arguments.get("history", None)
            conversation_id = arguments.get("conversation_id", None)
            requested_model = arguments.get("model", None)  # explicit model from UI

        if not message:
            return {"error": "Message is required"}

        # Resolve conversation context
        if history:
            ctx_history = history
            logger.debug(f"Using client-side history: {len(history)} messages")
        elif conversation_id:
            ctx_history = get_session_history(conversation_id)
            logger.debug(
                f"Using server session {conversation_id}: {len(ctx_history)} messages"
            )
        else:
            ctx_history = None

        messages = _build_messages(message, ctx_history)

        # Resolve active provider + model
        from core.api_client import get_api_client
        from core.model_selector import get_model_selector

        client = await get_api_client()
        model_selector = get_model_selector()
        # Use the model explicitly requested by the UI, or fall back to "general"
        model_id = requested_model if requested_model else model_selector.select("general")
        provider_name = model_selector.provider_name

        logger.debug(
            f"chat_tool: provider={provider_name!r} model={model_id!r} "
            f"messages={len(messages)}"
        )

        t0 = time.monotonic()
        api_response = await client.chat_completion(
            messages=messages,
            model=model_id,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        content = api_response.choices[0].message.content or ""

        if conversation_id:
            manage_conversation_session(conversation_id, message, content)

        return {
            "response": content,
            "model_used": model_id,
            "provider": provider_name,
            "latency_ms": latency_ms,
            "conversation_id": conversation_id,
            "context_messages": len(ctx_history) if ctx_history else 0,
        }

    except Exception as exc:
        logger.error(f"Error in chat tool executor: {exc}")
        return {"error": f"Failed to generate response: {str(exc)}"}
