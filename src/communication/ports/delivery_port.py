"""
DeliveryPort — domain port for outbound NIP-17 DM delivery.

Separates the Knowledge Base context from the Communication context's
transport implementation (NostrService / NIP17Client).

Rule: this file must never import httpx, sqlite3, os.environ, or any I/O
library.  Only standard-library ABCs are allowed here.

Invariant: DeliveryPort is the only permitted entry point for message
delivery from other bounded contexts.  Direct calls to NostrService or
NIP17Client from outside the communication context are a DDD violation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class DeliveryPort(ABC):
    """Abstract interface for outbound NIP-17 DM delivery.

    Implementations live in src/communication/ (e.g. NostrNIP17DeliveryAdapter).
    Wire the concrete adapter at the composition root (main.py / initializer.py).

    The port deliberately exposes a single text-delivery method.
    Splitting, chunking, and Tor routing are implementation details
    of the adapter — invisible to callers.
    """

    @abstractmethod
    async def send(self, text: str) -> bool:
        """Send *text* as a NIP-17 encrypted DM to the configured recipient.

        Long messages may be split by the implementation to respect relay
        content-size limits.

        Returns:
            True  — all message parts delivered to the relay.
            False — delivery failed or transport unavailable (retry next tick).

        Raises:
            Nothing — implementations must catch and log transport errors.
            A False return is the failure signal; callers must not raise on it.
        """
        ...
