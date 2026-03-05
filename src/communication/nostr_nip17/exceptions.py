"""
Exceptions for Nostr NIP-17 client
"""


class NostrClientError(Exception):
    """Base exception for Nostr client errors."""
    pass


class RelayConnectionError(NostrClientError):
    """Raised when connection to a relay fails."""
    pass


class MessageSendError(NostrClientError):
    """Raised when sending a message fails."""
    pass


class ConfigurationError(NostrClientError):
    """Raised when configuration is invalid."""
    pass


class KeyError(NostrClientError):
    """Raised when there's an issue with keys."""
    pass


class MessageReceiveError(NostrClientError):
    """Raised when receiving a message fails."""
    pass
