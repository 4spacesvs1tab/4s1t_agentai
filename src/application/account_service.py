"""
AccountService — application service for KB account CRUD.

Orchestrates account persistence via AccountRepository.  Route handlers call
this service; they never touch SQLite or AccountRepository directly.

DDD Rule 3: No FastAPI / HTTPException / Request imports here.

Scope (E3a — this session):
  list_accounts, add_account, remove_account, update_account

Deferred to E3-continuation:
  backfill_account, upsert_alias, delete_alias, blacklist_account,
  consolidate_preview, consolidate_confirm, set_account_scope
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from kb.ports.account_repository import AccountRepository
from kb.social_graph import KBAccount
from utils.logger import setup_logger

logger = setup_logger(__name__)


class AccountService:
    """Application service for KB account management (basic CRUD).

    Receives AccountRepository via constructor so that unit tests can supply
    an in-memory fake without touching SQLite.
    """

    def __init__(self, account_repo: AccountRepository) -> None:
        self._repo = account_repo

    # ------------------------------------------------------------------
    # list_accounts
    # ------------------------------------------------------------------

    async def list_accounts(
        self,
        user_id: str,
        layer: Optional[int] = None,
        domain: Optional[str] = None,
    ) -> list[dict]:
        """Return active accounts with aliases, optionally filtered by layer/domain.

        Response format is identical to account_routes.list_kb_accounts:
        all kb_accounts columns + parsed ``aliases`` dict + ``domains_list``.
        """
        return self._repo.find_filtered(user_id, layer=layer, domain=domain)

    # ------------------------------------------------------------------
    # add_account
    # ------------------------------------------------------------------

    async def add_account(
        self,
        user_id: str,
        display_name: str,
        layer: int,
        domains: str,
        aliases: Optional[dict[str, str]] = None,
    ) -> str:
        """Create a new KB account and return its UUID.

        Mirrors account_routes.create_kb_account — inserts into kb_accounts
        and kb_account_aliases with confidence=1.0, verified=1.

        Note: the existing AccountRepository.save() sets confidence=1.0/verified=0
        for aliases inserted from social_graph context (E2 origin).  The route
        uses confidence=1.0/verified=1 for user-supplied aliases.  A dedicated
        ``save_user_account()`` method is the correct long-term fix (deferred to
        E3-continuation); for now, save() is used and the verified flag
        difference is documented.
        """
        account_id = str(uuid.uuid4())
        account = KBAccount(
            id=account_id,
            user_id=user_id,
            display_name=display_name,
            layer=layer,
            domains=domains,
            active=True,
            added_by="user",
            aliases=aliases or {},
        )
        self._repo.save(account)
        logger.info(
            "Created KB account %s for user=%s: %s", account_id, user_id, display_name
        )
        return account_id

    # ------------------------------------------------------------------
    # remove_account
    # ------------------------------------------------------------------

    async def remove_account(self, account_id: str, user_id: str) -> bool:
        """Soft-delete an account (active=0).

        Returns True if found and deactivated, False if not found.
        Mirrors account_routes.deactivate_kb_account.
        """
        return self._repo.deactivate(account_id, user_id)

    # ------------------------------------------------------------------
    # update_account
    # ------------------------------------------------------------------

    async def update_account(
        self,
        account_id: str,
        user_id: str,
        display_name: Optional[str] = None,
        domains: Optional[str] = None,
        layer: Optional[int] = None,
    ) -> bool:
        """Update one or more kb_accounts fields.

        Returns True if found and updated, False if not found.
        Returns True with no DB change if no fields were provided.
        Mirrors account_routes.update_kb_account.
        """
        fields: dict[str, Any] = {}
        if display_name is not None:
            fields["display_name"] = display_name
        if domains is not None:
            fields["domains"] = domains
        if layer is not None:
            fields["layer"] = layer
        if not fields:
            return True
        return self._repo.update_fields(account_id, user_id, fields)
