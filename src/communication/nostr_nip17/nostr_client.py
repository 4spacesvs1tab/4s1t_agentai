"""
Nostr NIP-17 Client for 4S1T Agent AI

Sends and receives encrypted DMs using NIP-17 (GiftWrap) with multi-relay support.
Restricted to NIP-17 only - no other Nostr features.

Key features:
- NIP-17 encrypted messaging (GiftWrap)
- Multi-relay support (up to 5 relays)
- Automatic failover between relays
- Message receiving via polling
- Support for various relay URL formats
"""
import asyncio
import logging
import time
import traceback
from datetime import timedelta
from typing import List, Dict, Optional, Union, Callable
from dataclasses import dataclass, field
from enum import Enum

try:
    from nostr_sdk import (
        Client, Keys, PublicKey, RelayUrl, NostrSigner,
        Filter, Kind, Timestamp, EventBuilder, Tag, nip44_encrypt, Nip44Version
    )
    NOSTR_SDK_AVAILABLE = True
except ImportError:
    NOSTR_SDK_AVAILABLE = False
    logging.warning("nostr-sdk not installed. Nostr client will not function.")

from .exceptions import RelayConnectionError, NostrClientError
from .security import SecurityValidator, SecurityConfig, create_security_validator

from utils.logger import setup_logger
logger = setup_logger(__name__)

# Optional persistence service
try:
    from services.nostr_persistence_service import (
        get_nostr_persistence_service,
        MessageTypeDB,
        DeliveryStatus
    )
    PERSISTENCE_AVAILABLE = True
except ImportError:
    PERSISTENCE_AVAILABLE = False
    logger.warning("Nostr persistence service not available - messages will not be stored")


class MessageType(Enum):
    """Types of incoming messages."""
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESPONSE = "approval_response"
    COMMAND = "command"
    CHAT = "chat"
    UNKNOWN = "unknown"


@dataclass
class RelayConfig:
    """Configuration for a single Nostr relay."""
    url: str
    enabled: bool = True
    priority: int = 1  # Lower = higher priority
    timeout: int = 15
    reconnect: bool = True
    
    def __post_init__(self):
        # Normalize URL format
        self.url = self._normalize_url(self.url)
    
    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize various relay URL formats to wss:// or ws://"""
        url = url.strip()
        
        # If it already has ws:// or wss://, keep it
        if url.startswith(('ws://', 'wss://')):
            return url
        
        # If it starts with http:// or https://, convert to ws://
        if url.startswith('https://'):
            return 'wss://' + url[8:]
        if url.startswith('http://'):
            return 'ws://' + url[7:]
        
        # If no protocol, check if it looks like an onion address or local domain
        if '.onion' in url or '.local' in url or url.count('.') >= 2:
            return 'wss://' + url
        
        # IP address or hostname
        if ':' in url and url.split(':')[1].isdigit():
            return 'ws://' + url
        
        # Default to wss://
        return 'wss://' + url


@dataclass
class NostrMessage:
    """Represents a Nostr NIP-17 message."""
    id: Optional[str] = None
    content: str = ""
    sender_npub: Optional[str] = None
    recipient_npub: Optional[str] = None
    timestamp: Optional[int] = None
    relay: Optional[str] = None
    message_type: MessageType = MessageType.UNKNOWN
    
    def classify(self) -> MessageType:
        """Classify message based on content prefix."""
        content_lower = self.content.lower().strip()
        
        if content_lower.startswith(('approve:', 'approval:', 'request approval')):
            return MessageType.APPROVAL_REQUEST
        elif content_lower.startswith(('approved', 'rejected', 'denied')):
            return MessageType.APPROVAL_RESPONSE
        elif content_lower.startswith(('/cmd ', '/command ', '!')):
            return MessageType.COMMAND
        else:
            return MessageType.CHAT


@dataclass
class ReceivedMessage:
    """A received message with metadata."""
    event_id: str
    content: str
    sender_npub: str
    timestamp: int
    message_type: MessageType


class NIP17NostrClient:
    """
    Nostr client restricted to NIP-17 encrypted messaging only.
    Supports up to 5 relays with automatic failover.
    """
    
    MAX_RELAYS = 5
    
    def __init__(
        self,
        relay_configs: List[RelayConfig],
        private_key: str,
        recipient_npub: str
    ):
        if not NOSTR_SDK_AVAILABLE:
            raise NostrClientError("nostr-sdk not installed")
        
        if len(relay_configs) > self.MAX_RELAYS:
            logger.warning(f"Too many relays, limiting to {self.MAX_RELAYS}")
            relay_configs = relay_configs[:self.MAX_RELAYS]
        
        self.relay_configs = sorted(
            [r for r in relay_configs if r.enabled],
            key=lambda r: r.priority
        )
        self.private_key = private_key
        self.recipient_npub = recipient_npub
        
        # Internal state
        self.keys: Optional[Keys] = None
        self.signer: Optional[NostrSigner] = None
        self.recipient_pubkey: Optional[PublicKey] = None
        self.clients: Dict[str, Client] = {}
        self.relay_status: Dict[str, Dict] = {}
        self.active_relay: Optional[str] = None
        self._message_handlers: List[Callable[[ReceivedMessage], None]] = []
        self._received_events: set = set()  # Track received event IDs
        
        # Security validator
        security_config = SecurityConfig(
            enforce_local_relay_only=True,
            audit_logging=True,
            max_message_size=10000,
            rate_limit_messages_per_minute=10
        )
        self.security_validator = create_security_validator(security_config)
        
        self._initialize()
    
    def _initialize(self) -> None:
        """Initialize keys and signer."""
        try:
            self.keys = Keys.parse(self.private_key)
            self.recipient_pubkey = PublicKey.parse(self.recipient_npub)
            self.signer = NostrSigner.keys(self.keys)
            
            for config in self.relay_configs:
                self.relay_status[config.url] = {
                    'connected': False,
                    'last_error': None,
                    'failure_count': 0,
                    'last_success': None
                }
            
            logger.info(f"NIP-17 client initialized with {len(self.relay_configs)} relays")
            
        except Exception as e:
            raise NostrClientError(f"Failed to initialize Nostr client: {e}")
    
    async def connect_to_relay(self, relay_url: str) -> bool:
        """Connect to a specific relay."""
        try:
            if relay_url in self.clients:
                try:
                    await asyncio.wait_for(
                        self.clients[relay_url].disconnect(),
                        timeout=5.0
                    )
                except:
                    pass
                del self.clients[relay_url]
            
            client = Client(self.signer)
            self.clients[relay_url] = client
            
            parsed_url = RelayUrl.parse(relay_url)
            
            from nostr_sdk import RelayOptions
            relay_options = RelayOptions().ping(True).reconnect(True)
            
            await asyncio.wait_for(
                client.add_relay_with_opts(parsed_url, relay_options),
                timeout=15.0
            )
            
            await asyncio.wait_for(client.connect(), timeout=15.0)
            await asyncio.sleep(1)
            
            relays = await asyncio.wait_for(client.relays(), timeout=5.0)
            if parsed_url in relays:
                relay = relays[parsed_url]
                if relay.is_connected():
                    self.relay_status[relay_url]['connected'] = True
                    self.relay_status[relay_url]['failure_count'] = 0
                    self.relay_status[relay_url]['last_success'] = time.time()
                    logger.info(f"Connected to relay: {relay_url}")
                    return True
            
            self.relay_status[relay_url]['connected'] = False
            return False
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout connecting to relay: {relay_url}")
            self.relay_status[relay_url]['failure_count'] += 1
            return False
        except Exception as e:
            logger.error(f"Error connecting to relay {relay_url}: {e}")
            self.relay_status[relay_url]['failure_count'] += 1
            self.relay_status[relay_url]['last_error'] = str(e)
            return False
    
    async def connect_to_primary(self) -> Optional[str]:
        """Connect to the first available relay."""
        for config in self.relay_configs:
            if await self.connect_to_relay(config.url):
                self.active_relay = config.url
                logger.info(f"Connected to primary relay: {config.url}")
                return config.url
        
        logger.error("Failed to connect to any relay")
        return None
    
    async def send_encrypted_dm(self, message: str) -> Optional[str]:
        """Send encrypted DM using NIP-17 (GiftWrap)."""
        if not self.recipient_pubkey:
            raise NostrClientError("No recipient public key configured")
        
        if not self.active_relay or not self.relay_status.get(self.active_relay, {}).get('connected'):
            if not await self.connect_to_primary():
                raise RelayConnectionError("Failed to connect to any relay")
        
        event_id = None
        relay_used = self.active_relay

        try:
            client = self.clients[self.active_relay]
            event_id = await asyncio.wait_for(
                self._send_gift_wrap(client, message),
                timeout=20.0
            )
            logger.info(f"Sent NIP-17 DM: {event_id}")

            # Store in persistence if available
            if PERSISTENCE_AVAILABLE and event_id:
                try:
                    persistence = get_nostr_persistence_service()
                    persistence.store_message(
                        event_id=str(event_id),
                        message_type=MessageTypeDB.SENT,
                        sender_npub=self.npub,
                        recipient_npub=self.recipient_npub,
                        content=message,
                        relay_url=relay_used
                    )
                    persistence.update_relay_status(relay_used, is_connected=True)
                except Exception as e:
                    logger.warning(f"Failed to persist message: {e}")

            return str(event_id)

        except asyncio.TimeoutError:
            logger.error(f"Timeout sending DM via {self.active_relay}")
            self.relay_status[self.active_relay]['connected'] = False
            if PERSISTENCE_AVAILABLE:
                try:
                    persistence = get_nostr_persistence_service()
                    persistence.update_relay_status(relay_used, is_connected=False, error_message="Timeout")
                except:
                    pass
        except Exception as e:
            logger.error(f"Error sending DM: {e}")
            self.relay_status[self.active_relay]['connected'] = False
            if PERSISTENCE_AVAILABLE:
                try:
                    persistence = get_nostr_persistence_service()
                    persistence.update_relay_status(relay_used, is_connected=False, error_message=str(e))
                except:
                    pass

        # Failover
        for config in self.relay_configs:
            if config.url == self.active_relay:
                continue

            if await self.connect_to_relay(config.url):
                self.active_relay = config.url
                relay_used = config.url
                try:
                    client = self.clients[self.active_relay]
                    event_id = await asyncio.wait_for(
                        self._send_gift_wrap(client, message),
                        timeout=20.0
                    )
                    logger.info(f"Sent via failover: {event_id}")

                    if PERSISTENCE_AVAILABLE and event_id:
                        try:
                            persistence = get_nostr_persistence_service()
                            persistence.store_message(
                                event_id=str(event_id),
                                message_type=MessageTypeDB.SENT,
                                sender_npub=self.npub,
                                recipient_npub=self.recipient_npub,
                                content=message,
                                relay_url=relay_used
                            )
                            persistence.update_relay_status(relay_used, is_connected=True)
                        except Exception as e:
                            logger.warning(f"Failed to persist message: {e}")

                    return str(event_id)
                except Exception as e:
                    self.relay_status[config.url]['connected'] = False
                    if PERSISTENCE_AVAILABLE:
                        try:
                            persistence = get_nostr_persistence_service()
                            persistence.update_relay_status(relay_used, is_connected=False, error_message=str(e))
                        except:
                            pass

        raise RelayConnectionError("Failed to send via any relay")

    async def _send_gift_wrap(self, client: "Client", message: str) -> str:
        """
        Build and publish a NIP-17 gift wrap (kind 1059) with Timestamp.now().

        nostr-sdk's Client.send_private_msg() randomises created_at 0–48 h into
        the past (NIP-59 compliance). That causes Keychat's backfill 'since' filter
        to miss events when the app is opened hours after delivery. By constructing
        the gift wrap manually with Timestamp.now() we ensure the event is always
        visible in any backfill window.

        Pipeline:
          rumor  (kind 14, unsigned)   ← private_msg_rumor + custom_created_at(now)
          seal   (kind 13, signed)     ← EventBuilder.seal() with agent keys
          wrap   (kind 1059, signed)   ← nip44_encrypt(ephemeral_key, recipient, seal)
                                          + custom_created_at(Timestamp.now())
                                          + sign_with_keys(ephemeral_keys)
        """
        # 1. Rumor — unsigned kind-14 with current timestamp
        rumor = (
            EventBuilder.private_msg_rumor(self.recipient_pubkey, message)
            .custom_created_at(Timestamp.now())
            .build(self.keys.public_key())
        )

        # 2. Seal — signed kind-13, encrypted to recipient with agent key
        seal_builder = await EventBuilder.seal(self.signer, self.recipient_pubkey, rumor)
        seal_event = await seal_builder.sign(self.signer)

        # 3. Ephemeral key for outer gift wrap
        ephemeral_keys = Keys.generate()

        # 4. Encrypt seal JSON with ephemeral key (NIP-44 v2)
        encrypted_content = nip44_encrypt(
            ephemeral_keys.secret_key(),
            self.recipient_pubkey,
            seal_event.as_json(),
            Nip44Version.V2,
        )

        # 5. Gift wrap — kind 1059, Timestamp.now(), signed with ephemeral key
        recipient_hex = self.recipient_pubkey.to_hex()
        gift_wrap = (
            EventBuilder(Kind(1059), encrypted_content)
            .custom_created_at(Timestamp.now())
            .tags([Tag.parse(["p", recipient_hex])])
            .sign_with_keys(ephemeral_keys)
        )

        # 6. Publish
        output = await client.send_event(gift_wrap)
        return str(output)
    
    def add_message_handler(self, handler: Callable[[ReceivedMessage], None]) -> None:
        """Add a handler for incoming messages."""
        self._message_handlers.append(handler)
        logger.info(f"Message handler added. Total handlers: {len(self._message_handlers)}")
    
    async def receive_messages(self, since_seconds: int = 300) -> List[ReceivedMessage]:
        """
        Poll for gift wrap messages using get_events_of() with proper Filter.

        This method queries the relay for historical messages, not just real-time notifications.
        It can retrieve messages sent before the handler was registered.

        Args:
            since_seconds: Only retrieve messages created since this many seconds ago
                          (use 0 to get all historical messages)

        Returns:
            List of ReceivedMessage objects
        """
        messages = []

        if not self.active_relay or not self.relay_status.get(self.active_relay, {}).get('connected'):
            if not await self.connect_to_primary():
                logger.error("Cannot receive messages - no relay connected")
                return messages

        try:
            client = self.clients[self.active_relay]

            # Create filter for NIP-17 gift wrap messages (kind 1059)
            # We query for messages where kind is 1059 (gift wrap)
            now = time.time()
            since_timestamp = int(now - since_seconds)

            filter_obj = Filter().kind(Kind(1059)).pubkey(self.keys.public_key()).since(Timestamp.from_secs(since_timestamp))

            # Get events matching the filter from the relay
            # This is the proper way to poll for historical messages
            events = await client.fetch_events(filter_obj, timedelta(seconds=15))
            event_list = events.to_vec()

            logger.info(f"Received {len(event_list)} events from relay")

            # Process each event
            for event in event_list:
                event_id = event.id().to_hex()

                # Check if we've already processed this event
                if event_id in self._received_events:
                    logger.debug(f"Skipping already processed event: {event_id[:16]}...")
                    continue

                self._received_events.add(event_id)

                # Decrypt gift wrap to extract real content and sender
                try:
                    unwrapped = await self.clients[self.active_relay].unwrap_gift_wrap(event)
                    content = unwrapped.rumor().content()
                    sender = unwrapped.sender().to_bech32()
                    created_at = int(event.created_at().as_secs())

                    # Create message
                    msg = NostrMessage(
                        id=event_id,
                        content=content,
                        sender_npub=sender,
                        recipient_npub=self.npub,
                        timestamp=created_at,
                        relay=self.active_relay
                    )
                    msg_type = msg.classify()

                    received = ReceivedMessage(
                        event_id=event_id,
                        content=content,
                        sender_npub=sender,
                        timestamp=created_at,
                        message_type=msg_type
                    )

                    messages.append(received)
                    logger.info(f"Received gift wrap from {sender[:20]}... (kind 1059)")

                except Exception as e:
                    logger.error(f"Error processing event {event_id[:16]}...: {e}")

            logger.info(f"Total messages processed: {len(messages)}")

            # Call registered handlers for all messages
            for msg in messages:
                for handler in self._message_handlers:
                    try:
                        handler(msg)
                    except Exception as e:
                        logger.error(f"Message handler error: {e}")

        except asyncio.TimeoutError:
            logger.error(f"Timeout receiving messages from {self.active_relay}")
        except Exception as e:
            import traceback
            logger.error(f"Error receiving messages: {e}")
            logger.error(f"Traceback:\n{traceback.format_exc()}")

        return messages
    
    async def disconnect_all(self) -> None:
        """Disconnect from all relays."""
        for relay_url, client in list(self.clients.items()):
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
                logger.info(f"Disconnected from {relay_url}")
            except:
                pass
            finally:
                self.relay_status[relay_url]['connected'] = False
        
        self.clients.clear()
        self.active_relay = None
    
    def get_relay_status(self) -> Dict[str, Dict]:
        """Get status of all relays."""
        return self.relay_status.copy()
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected to any relay."""
        return any(
            status.get('connected', False)
            for status in self.relay_status.values()
        )
    
    @property
    def npub(self) -> Optional[str]:
        """Get client's npub."""
        if self.keys:
            return self.keys.public_key().to_bech32()
        return None


class NotificationHandler:
    """Handler for Nostr notifications."""
    
    def __init__(self, client: NIP17NostrClient):
        self.client = client
        self.messages: List[ReceivedMessage] = []
    
    async def handle(self, relay_url: str, subscription_id: str, event: any) -> None:
        """Handle incoming event."""
        try:
            # Check if we've already processed this event
            event_id = event.id().to_hex()
            if event_id in self.client._received_events:
                return
            
            self.client._received_events.add(event_id)
            
            # Check if this is a gift wrap (kind 1059)
            if event.kind().as_u16() == 1059:
                client_obj = list(self.client.clients.values())[0]
                unwrapped = await client_obj.unwrap_gift_wrap(event)
                content = unwrapped.rumor().content()
                sender = unwrapped.sender().to_bech32()
                created_at = int(event.created_at().as_secs())
                
                # Create message
                msg = NostrMessage(
                    id=event_id,
                    content=content,
                    sender_npub=sender,
                    recipient_npub=self.client.npub,
                    timestamp=created_at,
                    relay=relay_url
                )
                msg_type = msg.classify()
                
                received = ReceivedMessage(
                    event_id=event_id,
                    content=content,
                    sender_npub=sender,
                    timestamp=created_at,
                    message_type=msg_type
                )
                
                self.messages.append(received)
                logger.info(f"Received gift wrap from {sender[:20]}...")
                
        except Exception as e:
            logger.error(f"Error handling notification: {e}")
    
    def get_messages(self) -> List[ReceivedMessage]:
        """Return collected messages."""
        return self.messages


def create_nip17_client(
    relays: List[str],
    nsec: str,
    recipient_npub: str
) -> NIP17NostrClient:
    """Factory function for easy instantiation."""
    relay_configs = [
        RelayConfig(url=url, priority=i+1)
        for i, url in enumerate(relays[:NIP17NostrClient.MAX_RELAYS])
    ]
    
    return NIP17NostrClient(
        relay_configs=relay_configs,
        private_key=nsec,
        recipient_npub=recipient_npub
    )
