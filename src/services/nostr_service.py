"""
Nostr NIP-17 Communication Service for 4S1T Agent AI

Standalone service for bidirectional Nostr DM communication using NIP-17 (GiftWrap).
Independent from MCP framework - runs as a service like auth_service.

Two primary use cases:
1. Approval Flow - Agent sends approval requests, user responds via Keychat
2. Direct Chat - Bidirectional chat over Nostr encrypted DMs
"""
import asyncio
import uuid
import time
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum

from communication.nostr_nip17 import (
    NIP17ChatAgent,
    ApprovalRequest,
    ApprovalStatus,
    NIP17ConfigManager,
    create_chat_agent
)

from utils.logger import setup_logger
logger = setup_logger(__name__)


class ApprovalResult(Enum):
    """Result of an approval request."""
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    EXPIRED = "expired"


@dataclass
class ApprovalContext:
    """Context for an approval request."""
    request_id: str
    action: str
    details: str
    timestamp: float
    result: Optional[ApprovalResult] = None
    response_time: Optional[float] = None


class NostrCommunicationService:
    """
    Standalone NIP-17 communication service.
    
    Handles two-way communication between agent and user via Nostr NIP-17 encrypted DMs.
    Runs as a background service, independent from MCP tools.
    
    Usage:
        # Start service
        service = NostrCommunicationService()
        await service.start()
        
        # Request approval
        request_id = await service.send_approval_request(
            action="delete_database",
            details="User requested deletion of production database"
        )
        
        # Wait for response
        result = await service.wait_for_approval(request_id, timeout=300)
        
        # Stop service
        await service.stop()
    """
    
    def __init__(
        self,
        config_path: Optional[str] = None,
        approval_timeout: float = 300.0  # 5 minutes default
    ):
        """
        Initialize Nostr Communication Service.
        
        Args:
            config_path: Path to nostr_nip17.yaml config file
            approval_timeout: Seconds to wait for approval responses
        """
        self.config_path = config_path
        self.approval_timeout = approval_timeout
        self.chat_agent: Optional[NIP17ChatAgent] = None
        self._running = False
        self._approval_contexts: Dict[str, ApprovalContext] = {}
        self._approval_callbacks: Dict[str, Callable[[ApprovalContext], None]] = {}
        self._message_handlers: List[Callable[[str, str], None]] = []
        
        # Stats
        self._stats = {
            'messages_sent': 0,
            'messages_received': 0,
            'approvals_requested': 0,
            'approvals_completed': 0
        }
    
    async def start(self) -> bool:
        """
        Start the NIP-17 communication service.
        
        Creates and starts the underlying NIP17ChatAgent with message listeners.
        
        Returns:
            True if service started successfully, False otherwise
        """
        if self._running:
            logger.warning("Nostr service already running")
            return True
        
        try:
            # Create chat agent
            if self.config_path:
                # Load config from the provided path, then pass it to the agent
                manager = NIP17ConfigManager(self.config_path)
                config = manager.load_config()
                self.chat_agent = NIP17ChatAgent(config=config)
            else:
                self.chat_agent = NIP17ChatAgent()
            
            # Start the agent
            started = await self.chat_agent.start()
            
            if not started:
                logger.error("Failed to start NIP17ChatAgent")
                return False
            
            # Register message handlers
            self.chat_agent.register_chat_handler(self._handle_chat_message)
            
            self._running = True
            
            logger.info("Nostr Communication Service started")
            logger.info(f"Agent npub: {self.chat_agent.client.npub}")
            logger.info(f"Active relay: {self.chat_agent.client.active_relay}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Nostr service: {e}")
            return False
    
    async def stop(self) -> None:
        """Stop the NIP-17 communication service."""
        if not self._running:
            logger.warning("Nostr service not running")
            return
        
        self._running = False
        
        if self.chat_agent:
            await self.chat_agent.stop()
            self.chat_agent = None
        
        logger.info("Nostr Communication Service stopped")
    
    async def send_approval_request(
        self,
        action: str,
        details: str,
        callback: Optional[Callable[[ApprovalContext], None]] = None
    ) -> str:
        """
        Send approval request to user via Nostr NIP-17 DM.
        
        Args:
            action: Short description of action (e.g., "delete_database")
            details: Detailed description of what's being requested
            callback: Optional callback when user responds
            
        Returns:
            Request ID for tracking the approval
        """
        if not self._running or not self.chat_agent:
            raise RuntimeError("Nostr service not running")
        
        request_id = str(uuid.uuid4())[:8]
        
        context = ApprovalContext(
            request_id=request_id,
            action=action,
            details=details,
            timestamp=time.time(),
            result=None
        )
        
        self._approval_contexts[request_id] = context
        
        if callback:
            self._approval_callbacks[request_id] = callback
        
        # Send approval request via chat agent
        message = f"""🔒 APPROVAL REQUEST #{request_id}

Action: {action}
Details: {details}

Reply with:
  "approved:{request_id}" or "rejected:{request_id}"
  or simply "approved" / "rejected" for this request"""
        
        event_id = await self.chat_agent.send_message(message)
        
        self._stats['approvals_requested'] += 1
        
        logger.info(f"Approval request sent: {request_id} - {action}")
        
        return request_id
    
    async def wait_for_approval(
        self,
        request_id: str,
        timeout: Optional[float] = None
    ) -> Optional[ApprovalResult]:
        """
        Wait for approval response (blocking).
        
        Args:
            request_id: The request ID to wait for
            timeout: Seconds to wait (None uses default approval_timeout)
            
        Returns:
            ApprovalResult if received, None if timeout
        """
        if request_id not in self._approval_contexts:
            logger.warning(f"Unknown approval request: {request_id}")
            return None
        
        if timeout is None:
            timeout = self.approval_timeout
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            context = self._approval_contexts.get(request_id)
            
            if context and context.result:
                # Approval completed
                return context.result
            
            await asyncio.sleep(0.5)
        
        # Timeout
        context = self._approval_contexts.get(request_id)
        if context:
            context.result = ApprovalResult.TIMEOUT
            context.response_time = time.time()
            logger.warning(f"Approval timeout: {request_id}")
        
        return ApprovalResult.TIMEOUT
    
    async def send_message(self, message: str, recipient_npub: Optional[str] = None) -> Optional[str]:
        """
        Send a direct message to user via Nostr NIP-17 DM.
        
        Args:
            message: Message content to send
            recipient_npub: Optional recipient npub (uses default if None)
            
        Returns:
            Event ID if successful, None otherwise
        """
        if not self._running or not self.chat_agent:
            raise RuntimeError("Nostr service not running")
        
        try:
            event_id = await self.chat_agent.send_message(message)
            self._stats['messages_sent'] += 1
            logger.info(f"Message sent: {event_id}")
            return event_id
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return None
    
    async def broadcast_message(self, message: str) -> Dict[str, str]:
        """
        Broadcast message to all configured recipients.
        
        Args:
            message: Message content to send
            
        Returns:
            Dict mapping recipient to event ID (or error)
        """
        if not self._running or not self.chat_agent:
            raise RuntimeError("Nostr service not running")
        
        results = {}
        
        # Send to default recipient
        event_id = await self.send_message(message)
        results['default'] = event_id
        
        return results
    
    def register_message_handler(self, handler: Callable[[str, str], None]) -> None:
        """
        Register handler for incoming chat messages.
        
        Args:
            handler: Function(sender_npub: str, message: str)
        """
        self._message_handlers.append(handler)
        logger.info(f"Message handler registered. Total: {len(self._message_handlers)}")
    
    async def _handle_chat_message(self, sender_npub: str, message: str) -> None:
        """
        Handle incoming chat message.

        Routes messages to registered handlers.

        Args:
            sender_npub: Sender's public key
            message: Message content
        """
        self._stats['messages_received'] += 1

        logger.info(f"Received chat message from {sender_npub[:20]}...")

        for handler in self._message_handlers:
            try:
                result = handler(sender_npub, message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Message handler error: {e}")
    
    def get_approval_context(self, request_id: str) -> Optional[ApprovalContext]:
        """
        Get approval context for a request.
        
        Args:
            request_id: The request ID
            
        Returns:
            ApprovalContext if exists, None otherwise
        """
        return self._approval_contexts.get(request_id)
    
    def get_pending_approvals(self) -> Dict[str, ApprovalContext]:
        """
        Get all pending approval requests.
        
        Returns:
            Dict of request_id to ApprovalContext for pending requests
        """
        return {
            k: v for k, v in self._approval_contexts.items()
            if v.result is None
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        return {
            **self._stats,
            'running': self._running,
            'connected': self.chat_agent.client.is_connected if self.chat_agent else False,
            'npub': self.chat_agent.client.npub if self.chat_agent else None,
            'active_relay': self.chat_agent.client.active_relay if self.chat_agent else None,
            'pending_approvals': len(self.get_pending_approvals())
        }


# Global service instance
_service: Optional[NostrCommunicationService] = None


def get_nostr_service() -> Optional[NostrCommunicationService]:
    """Get the global Nostr service instance."""
    return _service


async def start_nostr_service(config_path: Optional[str] = None) -> bool:
    """
    Start the global Nostr service.
    
    Args:
        config_path: Path to nostr_nip17.yaml config file
        
    Returns:
        True if successful, False otherwise
    """
    global _service
    
    if _service is None:
        _service = NostrCommunicationService(config_path=config_path)
    
    return await _service.start()


async def stop_nostr_service() -> None:
    """Stop the global Nostr service."""
    global _service
    
    if _service:
        await _service.stop()
        _service = None


async def send_approval_request(
    action: str,
    details: str,
    callback: Optional[Callable[[ApprovalContext], None]] = None
) -> Optional[str]:
    """
    Convenience function to send approval request.
    
    Args:
        action: Short description of action
        details: Detailed description
        callback: Optional callback when user responds
        
    Returns:
        Request ID if service running, None otherwise
    """
    if _service:
        return await _service.send_approval_request(action, details, callback)
    return None


async def wait_for_approval(
    request_id: str,
    timeout: Optional[float] = None
) -> Optional[ApprovalResult]:
    """
    Convenience function to wait for approval.
    
    Args:
        request_id: The request ID to wait for
        timeout: Seconds to wait (None uses default)
        
    Returns:
        ApprovalResult if received, None if timeout
    """
    if _service:
        return await _service.wait_for_approval(request_id, timeout)
    return None
