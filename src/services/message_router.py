"""
Message Router for Nostr NIP-17 Communication

Routes incoming Nostr messages to appropriate handlers based on message type.
Supports approval responses, chat messages, and commands.

Usage:
    from services.message_router import MessageRouter
    
    router = MessageRouter()
    router.register_chat_handler(my_chat_handler)
    router.register_approval_handler(my_approval_handler)
    
    # Messages are automatically routed in background
"""
import logging
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass

from communication.nostr_nip17 import ReceivedMessage, MessageType
from services.nostr_service import get_nostr_service, ApprovalResult

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """A chat message from user."""
    sender_npub: str
    content: str
    timestamp: float


@dataclass
class ApprovalResponse:
    """An approval response from user."""
    request_id: str
    approved: bool
    original_message: str


class MessageRouter:
    """
    Routes incoming Nostr messages to appropriate handlers.
    
    Handles three message types:
    1. Approval responses - routes to approval handlers
    2. Chat messages - routes to chat handlers
    3. Commands - routes to command handlers
    """
    
    def __init__(self):
        """Initialize message router."""
        self._chat_handlers: List[Callable[[ChatMessage], None]] = []
        self._approval_handlers: List[Callable[[ApprovalResponse], None]] = []
        self._command_handlers: Dict[str, Callable[[str], str]] = {}
        
        logger.info("Message router initialized")
    
    def register_chat_handler(self, handler: Callable[[ChatMessage], None]) -> None:
        """
        Register handler for chat messages.
        
        Args:
            handler: Function(ChatMessage)
        """
        self._chat_handlers.append(handler)
        logger.info(f"Chat handler registered. Total: {len(self._chat_handlers)}")
    
    def register_approval_handler(self, handler: Callable[[ApprovalResponse], None]) -> None:
        """
        Register handler for approval responses.
        
        Args:
            handler: Function(ApprovalResponse)
        """
        self._approval_handlers.append(handler)
        logger.info(f"Approval handler registered. Total: {len(self._approval_handlers)}")
    
    def register_command_handler(self, command: str, handler: Callable[[str], str]) -> None:
        """
        Register handler for commands.
        
        Args:
            command: Command name (e.g., "status", "help")
            handler: Function(args) -> response
        """
        self._command_handlers[command.lower()] = handler
        logger.info(f"Command handler registered: {command}")
    
    async def route_message(self, message: ReceivedMessage) -> None:
        """
        Route incoming message to appropriate handler.
        
        Args:
            message: ReceivedMessage from Nostr
        """
        try:
            if message.message_type == MessageType.APPROVAL_RESPONSE:
                await self._handle_approval_response(message)
            elif message.message_type == MessageType.CHAT:
                await self._handle_chat_message(message)
            elif message.message_type == MessageType.COMMAND:
                await self._handle_command(message)
            else:
                logger.info(f"Unknown message type: {message.message_type}")
                
        except Exception as e:
            logger.error(f"Error routing message: {e}")
    
    async def _handle_approval_response(self, message: ReceivedMessage) -> None:
        """
        Handle approval response message.
        
        Parses response format: "approved:12345" or "rejected:12345"
        
        Args:
            message: ReceivedMessage
        """
        content = message.content.lower().strip()
        request_id = None
        approved = None
        
        # Parse response format
        if content.startswith('approved:'):
            request_id = content.split(':', 1)[1].strip()
            approved = True
        elif content.startswith('rejected:'):
            request_id = content.split(':', 1)[1].strip()
            approved = False
        elif content in ('approved', 'yes'):
            # Auto-match to most recent pending approval
            request_id = self._get_most_recent_pending_request_id()
            approved = True
        elif content in ('rejected', 'no'):
            request_id = self._get_most_recent_pending_request_id()
            approved = False
        
        if request_id:
            response = ApprovalResponse(
                request_id=request_id,
                approved=approved,
                original_message=message.content
            )
            
            for handler in self._approval_handlers:
                try:
                    handler(response)
                except Exception as e:
                    logger.error(f"Approval handler error: {e}")
            
            logger.info(f"Approval response routed: {request_id} = {approved}")
    
    async def _handle_chat_message(self, message: ReceivedMessage) -> None:
        """
        Handle chat message.
        
        Args:
            message: ReceivedMessage
        """
        chat_msg = ChatMessage(
            sender_npub=message.sender_npub,
            content=message.content,
            timestamp=message.timestamp
        )
        
        for handler in self._chat_handlers:
            try:
                handler(chat_msg)
            except Exception as e:
                logger.error(f"Chat handler error: {e}")
        
        logger.info(f"Chat message routed from {message.sender_npub[:20]}...")
    
    async def _handle_command(self, message: ReceivedMessage) -> None:
        """
        Handle command message.
        
        Command format: "!command args" or "/cmd command args"
        
        Args:
            message: ReceivedMessage
        """
        content = message.content.strip()
        
        # Remove command prefix
        if content.startswith('/cmd '):
            content = content[5:]
        elif content.startswith('/command '):
            content = content[9:]
        elif content.startswith('!'):
            content = content[1:]
        
        parts = content.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        if cmd in self._command_handlers:
            try:
                result = self._command_handlers[cmd](args)
                logger.info(f"Command executed: {cmd} -> {result}")
                
                # Send response back via Nostr
                service = get_nostr_service()
                if service:
                    await service.send_message(f"✓ {cmd}: {result}")
                
            except Exception as e:
                logger.error(f"Command handler error: {e}")
        else:
            logger.warning(f"Unknown command: {cmd}")
    
    def _get_most_recent_pending_request_id(self) -> Optional[str]:
        """
        Get the most recent pending approval request ID.
        
        Returns:
            Request ID or None
        """
        service = get_nostr_service()
        if not service:
            return None
        
        pending = service.get_pending_approvals()
        if not pending:
            return None
        
        # Get most recent by timestamp
        latest = max(pending.values(), key=lambda x: x.timestamp)
        return latest.request_id
    
    def format_chat_response(self, response: str, sender_npub: str) -> str:
        """
        Format chat response message.
        
        Args:
            response: Response content
            sender_npub: Sender's npub
            
        Returns:
            Formatted message
        """
        return f"🤖 {response}"
    
    def format_approval_response(self, request_id: str, approved: bool) -> str:
        """
        Format approval response message.
        
        Args:
            request_id: Request ID
            approved: Whether approved
            
        Returns:
            Formatted message
        """
        status = "✅ APPROVED" if approved else "❌ REJECTED"
        return f"{status}: {request_id}"


# Global router instance
_router: Optional[MessageRouter] = None


def get_message_router() -> MessageRouter:
    """Get the global MessageRouter instance."""
    global _router
    
    if _router is None:
        _router = MessageRouter()
    
    return _router


async def handle_nostr_message(message: ReceivedMessage) -> None:
    """
    Convenience function to handle incoming Nostr message.
    
    Args:
        message: ReceivedMessage
    """
    router = get_message_router()
    await router.route_message(message)
