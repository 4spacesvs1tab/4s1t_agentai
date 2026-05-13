"""
NIP-17 DeliveryPort adapter.

Implements DeliveryPort.send() by forwarding text to the running
NostrCommunicationService via its chat_agent.  Splits long messages
into _MAX_DM_CHARS-character chunks so each DM stays within the
practical Nostr relay limit.
"""
from __future__ import annotations

from communication.ports.delivery_port import DeliveryPort
from utils.logger import setup_logger

logger = setup_logger(__name__)

_MAX_DM_CHARS = 4000


class NostrNIP17DeliveryAdapter(DeliveryPort):
    """Delivers text via NIP-17 DMs through the running NostrCommunicationService."""

    async def send(self, text: str) -> bool:
        try:
            from services.nostr_service import get_nostr_service
            service = get_nostr_service()
        except Exception:
            logger.debug("NostrService import failed — skipping DM delivery")
            return False

        if service is None or not service._running or service.chat_agent is None:
            logger.debug("NIP-17 service unavailable — skipping DM delivery")
            return False

        parts = [text[i:i + _MAX_DM_CHARS] for i in range(0, len(text), _MAX_DM_CHARS)]
        for part in parts:
            try:
                event_id = await service.chat_agent.send_message(part)
                if event_id is None:
                    logger.warning("NIP-17 send_message returned None — delivery failed (relay down?)")
                    return False
            except Exception as exc:
                logger.warning("NIP-17 send_message failed: %s", exc)
                return False
        return True


_adapter: NostrNIP17DeliveryAdapter | None = None


def get_delivery_port() -> NostrNIP17DeliveryAdapter:
    """Singleton factory — returns a shared NostrNIP17DeliveryAdapter."""
    global _adapter
    if _adapter is None:
        _adapter = NostrNIP17DeliveryAdapter()
    return _adapter
