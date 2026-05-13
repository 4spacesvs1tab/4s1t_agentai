"""
NIP-17 Chat Agent Integration for 4S1T Agent AI

Provides bidirectional Nostr DM interface for:
- Approval requests/responses
- Agent commands
- Chat interface
"""
import asyncio
import time
from typing import Optional, Dict, Callable, Any, List
from dataclasses import dataclass
from enum import Enum, auto

from .nostr_client import NIP17NostrClient, ReceivedMessage, MessageType
from .config import NIP17Config, NIP17ConfigManager
from .exceptions import NostrClientError

from utils.logger import setup_logger
logger = setup_logger(__name__)


class ApprovalStatus(Enum):
    """Status of an approval request."""
    PENDING = auto()
    APPROVED = auto()
    REJECTED = auto()
    EXPIRED = auto()


@dataclass
class ApprovalRequest:
    """An approval request sent to user."""
    request_id: str
    action: str
    details: str
    timestamp: float
    status: ApprovalStatus
    user_response: Optional[str] = None
    response_timestamp: Optional[float] = None


class NIP17ChatAgent:
    """
    NIP-17 Chat Agent for secure bidirectional communication.
    
    Provides:
    - Approval request system
    - Command interface
    - Chat messaging
    """
    
    def __init__(self, config: Optional[NIP17Config] = None):
        """
        Initialize NIP-17 Chat Agent.
        
        Args:
            config: NIP-17 configuration. If None, loads from config file.
        """
        if config is None:
            manager = NIP17ConfigManager()
            config = manager.load_config()
        
        self.config = config
        self.client: Optional[NIP17NostrClient] = None
        self._running = False
        self._message_loop_task: Optional[asyncio.Task] = None
        
        # Approval tracking
        self._pending_approvals: Dict[str, ApprovalRequest] = {}
        self._approval_callbacks: Dict[str, Callable[[ApprovalRequest], None]] = {}

        # Multi-choice approval tracking: request_id → asyncio.Future[int]
        self._pending_multichoice: Dict[str, asyncio.Future] = {}

        # Command handlers
        self._command_handlers: Dict[str, Callable[[str], str]] = {}

        # Message handlers
        self._chat_handlers: List[Callable[[str, str], None]] = []

        # Stats
        self._stats = {
            'messages_sent': 0,
            'messages_received': 0,
            'approvals_requested': 0,
            'commands_received': 0
        }
    
    async def start(self) -> bool:
        """Start the chat agent."""
        if self._running:
            logger.warning("Chat agent already running")
            return True
        
        try:
            # Load private key from file
            private_key = self._load_private_key()
            if not private_key:
                logger.error("Failed to load private key")
                return False
            
            # Create client - config.relays is already List[RelayConfig]
            relay_configs = self.config.relays
            
            self.client = NIP17NostrClient(
                relay_configs=relay_configs,
                private_key=private_key,
                recipient_npub=self.config.recipient_npub
            )
            
            # Connect to primary relay
            if not await self.client.connect_to_primary():
                logger.error("Failed to connect to any relay")
                return False
            
            # Start message loop
            self._running = True
            self._message_loop_task = asyncio.create_task(
                self._message_loop(),
                name="nip17_message_loop"
            )
            
            logger.info(f"NIP-17 Chat Agent started")
            logger.info(f"Agent npub: {self.client.npub}")
            logger.info(f"Connected to: {self.client.active_relay}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start chat agent: {e}")
            return False
    
    async def stop(self) -> None:
        """Stop the chat agent."""
        self._running = False
        
        if self._message_loop_task:
            self._message_loop_task.cancel()
            try:
                await self._message_loop_task
            except asyncio.CancelledError:
                pass
        
        if self.client:
            await self.client.disconnect_all()
        
        logger.info("NIP-17 Chat Agent stopped")
    
    def _load_private_key(self) -> Optional[str]:
        """Load private key from file or environment."""
        try:
            key_path = self.config.private_key
            
            # Handle file: prefix
            if key_path.startswith('file:'):
                key_path = key_path[5:]
            
            # Try reading from file
            import os
            if os.path.exists(key_path):
                with open(key_path, 'r') as f:
                    content = f.read().strip()
                    # Extract nsec from content
                    for line in content.split('\n'):
                        line = line.strip()
                        if line.startswith('nsec1'):
                            return line
            
            # Try environment variable (NOSTR_NSEC or APPROVAL_PRIVATE_KEY)
            import os
            env_key = os.getenv('NOSTR_NSEC') or os.getenv('APPROVAL_PRIVATE_KEY')
            if env_key and env_key.startswith('nsec1'):
                return env_key
            
            # Try direct value
            if key_path.startswith('nsec1'):
                return key_path
            
            logger.error(f"Could not load private key from {key_path}")
            return None
            
        except Exception as e:
            logger.error(f"Error loading private key: {e}")
            return None
    
    async def _message_loop(self) -> None:
        """Main message polling loop."""
        logger.info("Message loop started")

        # Force-reconnect every _RECONNECT_EVERY_N polls (10s each → ~1h).
        # Long-idle WebSocket connections can stall silently even with the SDK's
        # internal reconnect, so we periodically tear down and re-establish the
        # relay connection to ensure fresh subscription state.
        _RECONNECT_EVERY_N = 360
        _poll_count = 0

        while self._running:
            try:
                # Periodic force-reconnect (every ~1 hour of idle polling)
                _poll_count += 1
                if _poll_count >= _RECONNECT_EVERY_N and self.client:
                    logger.info("NIP-17 periodic reconnect (poll=%d)", _poll_count)
                    try:
                        await self.client.connect_to_primary()
                    except Exception as rc_err:
                        logger.warning("Periodic reconnect failed: %s", rc_err)
                    _poll_count = 0

                # Poll for messages every 10 seconds.
                # Window must cover worst-case loop blockage (e.g. slow local LLM).
                messages = await self.client.receive_messages(since_seconds=172800)

                for msg in messages:
                    await self._process_message_async(msg)

                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in message loop: {e}")
                await asyncio.sleep(10)

        logger.info("Message loop stopped")
    
    def _handle_incoming_message(self, msg: ReceivedMessage) -> None:
        """Handle incoming message from client."""
        asyncio.create_task(self._process_message_async(msg))
    
    def _is_sender_trusted(self, npub: str) -> bool:
        """Return True if *npub* is in the trusted contacts list and not blocked."""
        try:
            from database.connection import get_database_connection
            db = get_database_connection()
            rows = db.execute_query(
                "SELECT is_trusted FROM nostr_contacts WHERE npub = ? AND is_blocked = 0",
                (npub,),
            )
            return bool(rows and rows[0]["is_trusted"])
        except Exception as exc:
            logger.warning(f"Could not check trusted sender {npub[:20]}: {exc}")
            return False

    async def _process_message_async(self, msg: ReceivedMessage) -> None:
        """Process a received message asynchronously."""
        # --- 6E.1: sender trust check ---
        if not self._is_sender_trusted(msg.sender_npub):
            logger.warning(
                f"Dropping message from untrusted sender: {msg.sender_npub[:20]}..."
            )
            try:
                from core.audit import get_audit_log
                audit = get_audit_log()
                await audit.log(
                    "NOSTR_UNKNOWN_SENDER",
                    actor=msg.sender_npub,
                    target="nip17_chat",
                    metadata={"content_preview": msg.content[:50]},
                )
            except Exception:
                pass
            return

        self._stats['messages_received'] += 1

        try:
            if msg.message_type == MessageType.APPROVAL_RESPONSE:
                await self._handle_approval_response(msg)
            elif msg.message_type == MessageType.COMMAND:
                await self._handle_command(msg)
            elif msg.message_type == MessageType.CHAT:
                await self._handle_chat(msg)
            else:
                logger.info(f"Received message: {msg.content[:50]}...")

        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    # Approval keywords for all supported languages.
    # Keys with ":" suffix are used as startswith() prefixes (include trailing colon).
    # Plain keys are used for exact-match (full-message) responses.
    _APPROVE_PREFIXES: tuple[str, ...] = ("approved:", "zatwierdź:")
    _REJECT_PREFIXES: tuple[str, ...] = ("rejected:", "odrzuć:")
    _APPROVE_EXACT: frozenset[str] = frozenset(["approved", "yes", "zatwierdź", "tak"])
    _REJECT_EXACT: frozenset[str] = frozenset(["rejected", "no", "odrzuć", "nie"])

    async def _handle_approval_response(self, msg: ReceivedMessage) -> None:
        """Handle approval response from user (all supported languages)."""
        content = msg.content.lower().strip()

        # Parse response (e.g., "approved:12345" / "zatwierdź:12345" or bare word)
        request_id = None
        approved = None

        for prefix in self._APPROVE_PREFIXES:
            if content.startswith(prefix):
                request_id = content[len(prefix):].strip()
                approved = True
                break

        if approved is None:
            for prefix in self._REJECT_PREFIXES:
                if content.startswith(prefix):
                    request_id = content[len(prefix):].strip()
                    approved = False
                    break

        if approved is None:
            if content in self._APPROVE_EXACT:
                if self._pending_approvals:
                    request_id = list(self._pending_approvals.keys())[-1]
                    approved = True
            elif content in self._REJECT_EXACT:
                if self._pending_approvals:
                    request_id = list(self._pending_approvals.keys())[-1]
                    approved = False
        
        if request_id and request_id in self._pending_approvals:
            req = self._pending_approvals[request_id]
            req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            req.user_response = msg.content
            req.response_timestamp = time.time()
            
            # Call callback if registered
            if request_id in self._approval_callbacks:
                try:
                    self._approval_callbacks[request_id](req)
                except Exception as e:
                    logger.error(f"Approval callback error: {e}")
            
            logger.info(f"Approval {request_id}: {'APPROVED' if approved else 'REJECTED'}")
            
            # Acknowledge
            await self.send_message(
                f"✓ Approval {request_id} recorded as {'APPROVED' if approved else 'REJECTED'}"
            )
    
    async def _handle_command(self, msg: ReceivedMessage) -> None:
        """Handle command from user."""
        self._stats['commands_received'] += 1
        
        content = msg.content.strip()
        
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
                await self.send_message(f"✓ {cmd}: {result}")
            except Exception as e:
                await self.send_message(f"✗ Error in {cmd}: {e}")
        else:
            await self.send_message(f"Unknown command: {cmd}. Type /help for available commands.")
    
    async def _handle_chat(self, msg: ReceivedMessage) -> None:
        """Handle chat message."""
        # Check for multi-choice responses before calling general handlers.
        # Accepted formats:
        #   "N"         — resolves the most-recently-registered pending request
        #   "N:id"      — resolves the specific request with that id
        # where N is a digit 1–4.
        content = msg.content.strip()
        if content and content[0].isdigit() and content[0] in "1234":
            digit = int(content[0])
            rest = content[1:].lstrip(":").strip()  # optional request_id suffix

            resolved = False
            if rest and rest in self._pending_multichoice:
                fut = self._pending_multichoice.pop(rest)
                if not fut.done():
                    fut.set_result(digit)
                resolved = True
            elif not rest and self._pending_multichoice:
                # No id supplied — resolve the most-recently-registered request
                last_id = list(self._pending_multichoice.keys())[-1]
                fut = self._pending_multichoice.pop(last_id)
                if not fut.done():
                    fut.set_result(digit)
                resolved = True

            if resolved:
                await self.send_message(f"✓ Choice {digit} recorded.")
                return

        for handler in self._chat_handlers:
            try:
                result = handler(msg.sender_npub, msg.content)
                if asyncio.iscoroutine(result):
                    await result

            except Exception as e:
                logger.error(f"Chat handler error: {e}")
    
    # Public API
    
    async def send_message(self, message: str) -> Optional[str]:
        """Send a chat message."""
        if not self.client or not self._running:
            logger.error("Chat agent not running")
            return None
        
        try:
            event_id = await self.client.send_encrypted_dm(message)
            self._stats['messages_sent'] += 1
            return event_id
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return None
    
    async def request_approval(
        self,
        action: str,
        details: str,
        callback: Optional[Callable[[ApprovalRequest], None]] = None,
        timeout: Optional[float] = None
    ) -> ApprovalRequest:
        """
        Request approval from user for an action.
        
        Args:
            action: Short action description (e.g., "delete_file")
            details: Detailed description
            callback: Called when user responds
            timeout: Seconds to wait for response (None = no timeout)
            
        Returns:
            ApprovalRequest object
        """
        import uuid
        request_id = str(uuid.uuid4())[:8]
        
        req = ApprovalRequest(
            request_id=request_id,
            action=action,
            details=details,
            timestamp=time.time(),
            status=ApprovalStatus.PENDING
        )
        
        self._pending_approvals[request_id] = req
        if callback:
            self._approval_callbacks[request_id] = callback
        
        # Format message
        message = f"""🔒 APPROVAL REQUEST #{request_id}

Action: {action}
Details: {details}

Reply with:
  "approved:{request_id}" or "rejected:{request_id}"
  or simply "approved" / "rejected" for this request"""
        
        await self.send_message(message)
        self._stats['approvals_requested'] += 1
        
        logger.info(f"Approval requested: {request_id} - {action}")
        
        return req
    
    async def wait_for_approval(
        self,
        request_id: str,
        timeout: float = 300.0
    ) -> Optional[bool]:
        """
        Wait for approval response (blocking).
        
        Args:
            request_id: The request ID to wait for
            timeout: Maximum seconds to wait
            
        Returns:
            True if approved, False if rejected, None if timeout
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if request_id not in self._pending_approvals:
                # Request was removed (probably handled)
                return None
            
            req = self._pending_approvals[request_id]
            
            if req.status == ApprovalStatus.APPROVED:
                return True
            elif req.status == ApprovalStatus.REJECTED:
                return False
            
            await asyncio.sleep(1)
        
        # Timeout
        req.status = ApprovalStatus.EXPIRED
        return None
    
    async def request_multichoice(
        self,
        title: str,
        body: str,
        options: List[str],
        timeout: float = 120.0,
    ) -> Optional[int]:
        """
        Send a multi-choice prompt via NIP-17 and wait for a digit response.

        The user replies with the digit alone ("1", "2", "3", "4") or in the
        format "N:request_id" to target a specific request.

        Args:
            title:   Short heading for the NIP-17 message.
            body:    Detail text (detected PII summary, task preview, etc.).
            options: List of option strings shown to the user.
            timeout: Seconds to wait before returning None.

        Returns:
            int 1–4 if user responds in time, None on timeout.
        """
        import uuid as _uuid
        request_id = _uuid.uuid4().hex[:8]

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_multichoice[request_id] = fut

        options_text = "\n".join(f"  {opt}" for opt in options)
        message = (
            f"⚠️  {title}  [#{request_id}]\n\n"
            f"{body}\n\n"
            f"{options_text}\n\n"
            f'Reply: "1:{request_id}", "2:{request_id}", ... or just "1"–"4"'
        )
        await self.send_message(message)

        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_multichoice.pop(request_id, None)
            if not fut.done():
                fut.cancel()
            logger.warning(f"Multi-choice request #{request_id} timed out after {timeout}s")
            return None

    def register_command(self, command: str, handler: Callable[[str], str]) -> None:
        """Register a command handler."""
        self._command_handlers[command.lower()] = handler
        logger.info(f"Registered command handler: {command}")
    
    def register_chat_handler(self, handler: Callable[[str, str], None]) -> None:
        """Register a chat message handler."""
        self._chat_handlers.append(handler)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get agent statistics."""
        return {
            **self._stats,
            'running': self._running,
            'connected': self.client.is_connected if self.client else False,
            'npub': self.client.npub if self.client else None,
            'active_relay': self.client.active_relay if self.client else None,
            'pending_approvals': len(self._pending_approvals)
        }
    
    def get_pending_approvals(self) -> Dict[str, ApprovalRequest]:
        """Get all pending approval requests."""
        return {
            k: v for k, v in self._pending_approvals.items()
            if v.status == ApprovalStatus.PENDING
        }


async def create_chat_agent(
    config_path: Optional[str] = None
) -> NIP17ChatAgent:
    """
    Factory function to create and start a chat agent.
    
    Args:
        config_path: Path to config file. If None, uses default.
        
    Returns:
        Running NIP17ChatAgent instance
    """
    if config_path:
        manager = NIP17ConfigManager(config_path)
    else:
        manager = NIP17ConfigManager()
    
    config = manager.load_config()
    agent = NIP17ChatAgent(config)
    
    if not await agent.start():
        raise NostrClientError("Failed to start chat agent")
    
    return agent
