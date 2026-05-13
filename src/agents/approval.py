"""
ApprovalGate — async HITL approval gate via Nostr DM (FR-15, §3.4.1).

Flow:
  1. Send an encrypted Nostr DM to the configured user pubkey with:
       APPROVE REQUEST <approval_id>: <skill_name> params=<json>
  2. Poll receive_messages() every POLL_INTERVAL_SECONDS for a response
     whose content starts with "approved <approval_id>" or
     "denied <approval_id>" (case-insensitive).
  3. Return True (approved) or False (denied / timeout).

If the Nostr client is unavailable (import fails, not configured), the gate
falls back to auto-approval and logs a warning — so the agent keeps running
in development environments without a Nostr setup.

Usage::

    from communication.nostr_nip17.nostr_client import NIP17NostrClient
    gate = ApprovalGate(nostr_client, timeout_seconds=300)
    approved = await gate.request_approval("python_execute", {"code": "..."}, "data_agent", "wf-123")
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from utils.logger import setup_logger

logger = setup_logger(__name__)

_POLL_INTERVAL = 5.0       # seconds between message checks
_POLL_LOOKBACK = 600       # how far back (seconds) to search for response messages


class ApprovalGate:
    """
    Async HITL approval gate backed by Nostr DM.

    Args:
        nostr_client: A connected NIP17NostrClient instance. May be None,
                      in which case all approvals are auto-granted (dev mode).
        timeout_seconds: How long to wait for a user response before denying.
        audit_log: Optional AuditLog for writing SKILL_APPROVAL_* events.
    """

    def __init__(
        self,
        nostr_client: Any | None = None,   # NIP17NostrClient — optional
        timeout_seconds: float = 300.0,
        audit_log: Any | None = None,      # AuditLog — optional
    ) -> None:
        self._client = nostr_client
        self._timeout = timeout_seconds
        self._audit_log = audit_log

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        skill_name: str,
        parameters: dict,
        agent_type: str,
        workflow_id: str | None = None,
    ) -> bool:
        """
        Request HITL approval before executing a sensitive skill.

        Returns:
            True  — user sent "approved <approval_id>"
            False — user sent "denied <approval_id>" or timeout elapsed
        """
        if self._client is None:
            logger.warning(
                f"[approval] No Nostr client configured — "
                f"auto-approving skill '{skill_name}' (dev mode)"
            )
            return True

        approval_id = str(uuid.uuid4())[:8]   # short, human-readable ID

        # Sanitise parameters (strip any key that looks like a secret)
        safe_params = {
            k: v for k, v in parameters.items()
            if not any(s in k.upper() for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS"))
        }

        dm_text = (
            f"APPROVE REQUEST {approval_id}\n"
            f"Agent : {agent_type}\n"
            f"Skill : {skill_name}\n"
            f"Params: {json.dumps(safe_params, default=str)}\n\n"
            f"Reply with:\n"
            f"  approved {approval_id}\n"
            f"  denied {approval_id}"
        )

        # Log approval request
        await self._audit(
            "SKILL_APPROVAL_REQUESTED",
            actor=agent_type,
            target=skill_name,
            metadata={"approval_id": approval_id, "workflow_id": workflow_id},
        )

        # Send DM
        try:
            msg_id = await self._client.send_encrypted_dm(dm_text)
            if msg_id:
                logger.info(
                    f"[approval] Sent approval request {approval_id} for "
                    f"'{skill_name}' (msg_id={msg_id})"
                )
            else:
                logger.warning(
                    f"[approval] send_encrypted_dm returned None for approval_id={approval_id}"
                )
        except Exception as exc:
            logger.error(f"[approval] Failed to send Nostr DM: {exc}")
            return False

        # Poll for response
        approved = await self._poll_for_response(approval_id, agent_type, skill_name)

        event = "SKILL_APPROVAL_GRANTED" if approved else "SKILL_APPROVAL_DENIED"
        await self._audit(
            event,
            actor=agent_type,
            target=skill_name,
            metadata={"approval_id": approval_id, "workflow_id": workflow_id},
        )
        return approved

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _poll_for_response(
        self,
        approval_id: str,
        agent_type: str,
        skill_name: str,
    ) -> bool:
        """
        Poll Nostr messages until an approval response for approval_id is found,
        or timeout elapses.
        """
        deadline = asyncio.get_event_loop().time() + self._timeout
        approved_prefix = f"approved {approval_id}".lower()
        denied_prefix = f"denied {approval_id}".lower()

        logger.info(
            f"[approval] Waiting up to {self._timeout}s for response to {approval_id} "
            f"(skill='{skill_name}', agent='{agent_type}')"
        )

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)

            try:
                messages = await self._client.receive_messages(since_seconds=_POLL_LOOKBACK)
            except Exception as exc:
                logger.warning(f"[approval] receive_messages error: {exc}")
                continue

            for msg in messages:
                content_lower = (msg.content or "").lower().strip()
                if content_lower.startswith(approved_prefix):
                    logger.info(
                        f"[approval] APPROVED: approval_id={approval_id} skill='{skill_name}'"
                    )
                    return True
                if content_lower.startswith(denied_prefix):
                    logger.info(
                        f"[approval] DENIED: approval_id={approval_id} skill='{skill_name}'"
                    )
                    return False

        logger.warning(
            f"[approval] TIMEOUT: approval_id={approval_id} skill='{skill_name}' "
            f"after {self._timeout}s — treating as denied"
        )
        return False

    async def _audit(self, event_type: str, **kwargs: Any) -> None:
        if self._audit_log is None:
            return
        try:
            await self._audit_log.log(event_type=event_type, **kwargs)
        except Exception as exc:
            logger.error(f"[approval] AuditLog write failed: {exc}")
