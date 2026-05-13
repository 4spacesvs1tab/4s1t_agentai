"""
Approval Gateway for Nostr NIP-17 Communication

Provides simplified approval workflow for the 4S1T Agent.
Uses Nostr NIP-17 encrypted DMs for secure agent-user approval.

Usage:
    from services.approval_gateway import request_approval
    
    approved = await request_approval(
        action="delete_database",
        details="User requested deletion of production database"
    )
    
    if approved:
        # Execute action
        pass
    else:
        # Cancel action
        pass
"""
from typing import Optional, Callable, Any
from datetime import datetime

from services.nostr_service import (
    NostrCommunicationService,
    send_approval_request,
    wait_for_approval,
    ApprovalResult,
    get_nostr_service
)

from utils.logger import setup_logger
logger = setup_logger(__name__)


class ApprovalGateway:
    """
    Simplified approval workflow using Nostr NIP-17 DMs.
    
    Provides blocking approval requests with timeout support.
    """
    
    def __init__(self, service: Optional[NostrCommunicationService] = None):
        """
        Initialize Approval Gateway.
        
        Args:
            service: NostrCommunicationService instance (uses global if None)
        """
        self.service = service or get_nostr_service()
        self._timeout = 300.0  # 5 minutes default
    
    async def request_approval(
        self,
        action: str,
        details: str,
        timeout: Optional[float] = None
    ) -> bool:
        """
        Request approval from user via Nostr NIP-17 DM.
        
        This is a BLOCKING call that waits for user response.
        
        Args:
            action: Short description of action (e.g., "delete_database")
            details: Detailed description of what's being requested
            timeout: Seconds to wait for response (None uses default)
            
        Returns:
            True if approved, False if rejected, None if timeout
        """
        if not self.service:
            logger.error("Nostr service not available")
            return False
        
        if timeout is None:
            timeout = self._timeout
        
        try:
            # Send approval request
            request_id = await self.service.send_approval_request(action, details)
            
            logger.info(f"Approval request sent: {request_id}")
            
            # Wait for response (blocking)
            result = await wait_for_approval(request_id, timeout=timeout)
            
            if result == ApprovalResult.APPROVED:
                logger.info(f"Approval granted: {request_id}")
                return True
            elif result == ApprovalResult.REJECTED:
                logger.info(f"Approval rejected: {request_id}")
                return False
            elif result == ApprovalResult.TIMEOUT:
                logger.warning(f"Approval timeout: {request_id}")
                return False
            elif result == ApprovalResult.EXPIRED:
                logger.warning(f"Approval expired: {request_id}")
                return False
            else:
                logger.warning(f"Unknown approval result: {result}")
                return False
                
        except Exception as e:
            logger.error(f"Error requesting approval: {e}")
            return False
    
    async def check_approval_status(self, request_id: str) -> Optional[bool]:
        """
        Check status of a pending approval request.
        
        Args:
            request_id: The request ID to check
            
        Returns:
            True if approved, False if rejected, None if still pending
        """
        if not self.service:
            return None
        
        context = self.service.get_approval_context(request_id)
        
        if not context:
            logger.warning(f"Unknown approval request: {request_id}")
            return None
        
        if context.result == ApprovalResult.APPROVED:
            return True
        elif context.result == ApprovalResult.REJECTED:
            return False
        elif context.result == ApprovalResult.TIMEOUT:
            return False
        elif context.result == ApprovalResult.EXPIRED:
            return False
        else:
            return None  # Still pending
    
    def get_pending_approvals(self) -> int:
        """Get count of pending approval requests."""
        if not self.service:
            return 0
        
        return len(self.service.get_pending_approvals())
    
    def format_approval_message(self, action: str, details: str, request_id: str) -> str:
        """
        Format approval request message.
        
        Args:
            action: Action description
            details: Details about the action
            request_id: Request ID for response matching
            
        Returns:
            Formatted message string
        """
        return f"""🔒 APPROVAL REQUEST #{request_id}

Action: {action}
Details: {details}

Reply with:
  "approved:{request_id}" or "rejected:{request_id}"
  or simply "approved" / "rejected" for this request"""
    
    def format_approval_response(self, request_id: str, approved: bool) -> str:
        """
        Format approval response message.
        
        Args:
            request_id: The request ID
            approved: Whether approved
            
        Returns:
            Formatted response string
        """
        status = "APPROVED" if approved else "REJECTED"
        return f"{status}:{request_id}"


# Global gateway instance
_gateway: Optional[ApprovalGateway] = None


def get_approval_gateway() -> ApprovalGateway:
    """Get the global ApprovalGateway instance."""
    global _gateway
    
    if _gateway is None:
        _gateway = ApprovalGateway()
    
    return _gateway


async def request_approval(
    action: str,
    details: str,
    timeout: Optional[float] = None
) -> bool:
    """
    Convenience function to request approval.
    
    Args:
        action: Short description of action
        details: Detailed description
        timeout: Seconds to wait (None uses default 300s)
        
    Returns:
        True if approved, False otherwise
    """
    gateway = get_approval_gateway()
    return await gateway.request_approval(action, details, timeout)


async def check_approval_status(request_id: str) -> Optional[bool]:
    """Check status of pending approval."""
    gateway = get_approval_gateway()
    return await gateway.check_approval_status(request_id)


def get_pending_count() -> int:
    """Get count of pending approval requests."""
    gateway = get_approval_gateway()
    return gateway.get_pending_approvals()


async def request_multichoice_approval(
    title: str,
    body: str,
    options: list,
    timeout: float = 120.0,
) -> Optional[int]:
    """
    Send a multi-choice NIP-17 prompt and wait for a digit response (1–4).

    Args:
        title:   Short heading for the NIP-17 message.
        body:    Detail text (e.g. detected PII summary, task preview).
        options: List of option description strings displayed to the user.
        timeout: Seconds to wait. Returns None on timeout.

    Returns:
        int 1–4 if user responds in time, None if Nostr unavailable or timeout.
    """
    from services.nostr_service import get_nostr_service
    service = get_nostr_service()
    if not service or not service.chat_agent:
        logger.warning("request_multichoice_approval: Nostr service unavailable")
        return None
    return await service.chat_agent.request_multichoice(
        title=title,
        body=body,
        options=options,
        timeout=timeout,
    )
