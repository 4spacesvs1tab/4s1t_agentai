"""
Nostr NIP-17 Module for 4S1T Agent AI

Sends and receives encrypted DMs using NIP-17 (GiftWrap).
"""
from .nostr_client import (
    NIP17NostrClient,
    RelayConfig,
    NostrMessage,
    ReceivedMessage,
    MessageType,
    create_nip17_client
)
from .config import NIP17Config, NIP17ConfigManager, load_config_from_env
from .security import (
    SecurityValidator,
    SecurityConfig,
    SecurityLevel,
    create_security_validator
)
from .exceptions import (
    NostrClientError,
    RelayConnectionError,
    MessageSendError,
    MessageReceiveError,
    ConfigurationError,
    KeyError
)
from .chat_agent import (
    NIP17ChatAgent,
    ApprovalRequest,
    ApprovalStatus,
    create_chat_agent
)

__all__ = [
    'NIP17NostrClient',
    'RelayConfig',
    'NostrMessage',
    'ReceivedMessage',
    'MessageType',
    'create_nip17_client',
    'NIP17Config',
    'NIP17ConfigManager',
    'load_config_from_env',
    'SecurityValidator',
    'SecurityConfig',
    'SecurityLevel',
    'create_security_validator',
    'NostrClientError',
    'RelayConnectionError',
    'MessageSendError',
    'MessageReceiveError',
    'ConfigurationError',
    'KeyError',
    'NIP17ChatAgent',
    'ApprovalRequest',
    'ApprovalStatus',
    'create_chat_agent'
]

__version__ = '1.3.0'
