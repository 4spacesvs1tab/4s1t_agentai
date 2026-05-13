"""
AccountRepository — domain port for KB account persistence.

Rule: this file must never import sqlite3, httpx, os.environ, or any I/O
library.  Only standard-library ABCs and TYPE_CHECKING imports are allowed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from kb.social_graph import KBAccount


class AccountRepository(ABC):
    """Abstract persistence interface for KBAccount aggregates.

    Implementations live in src/infrastructure/sqlite/.
    Wire a concrete implementation at the composition root (initializer).

    SQL origin:
      find_active_by_user   ← social_graph.load()  (kb_accounts + kb_account_aliases)
      find_relations         ← social_graph.load()  (kb_relations)
      save                  ← social_graph.add_account()
      upsert_relation       ← social_graph.add_relation()
    """

    @abstractmethod
    def find_active_by_user(self, user_id: str) -> list[KBAccount]:
        """Return all active accounts with their aliases for *user_id*.

        Implementations must join kb_accounts and kb_account_aliases and
        populate KBAccount.aliases before returning.
        """
        ...

    @abstractmethod
    def find_relations_by_account_ids(
        self, account_ids: list[str]
    ) -> list[tuple[str, str, str, float]]:
        """Return graph edges involving any of *account_ids*.

        Each tuple is (from_account_id, to_account_id, relation_type, weight).
        Returns an empty list when kb_relations does not exist yet.
        """
        ...

    @abstractmethod
    def save(self, account: KBAccount) -> None:
        """Persist a new account and its aliases.

        Inserts into kb_accounts and kb_account_aliases.
        Does not update existing rows — callers must ensure no duplicate IDs.
        """
        ...

    @abstractmethod
    def upsert_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: str,
        weight: float,
    ) -> None:
        """Insert a new kb_relations edge or strengthen an existing one.

        If the (from_id, to_id, relation_type) triplet already exists,
        increments evidence_count and bumps weight by 0.1 (capped at 1.0).
        """
        ...

    # ------------------------------------------------------------------
    # Application-service methods (wired in E3)
    # ------------------------------------------------------------------

    @abstractmethod
    def find_filtered(
        self,
        user_id: str,
        layer: Optional[int] = None,
        domain: Optional[str] = None,
    ) -> list[dict]:
        """Return active accounts with aliases as raw dicts.

        Includes all kb_accounts columns plus a parsed ``aliases`` dict
        (platform → platform_id) and a ``domains_list`` list.  Optionally
        filtered by *layer* and/or *domain* substring.

        SQL origin: account_routes.list_kb_accounts — SELECT a.* + aliases.
        """
        ...

    @abstractmethod
    def deactivate(self, account_id: str, user_id: str) -> bool:
        """Soft-delete an account by setting active=0.

        Returns True if a row was updated (account existed and belonged to user).

        SQL origin: account_routes.deactivate_kb_account.
        """
        ...

    @abstractmethod
    def update_fields(
        self,
        account_id: str,
        user_id: str,
        fields: dict[str, Any],
    ) -> bool:
        """Update arbitrary kb_accounts columns by name.

        *fields* is a mapping of column → new value; only non-None values
        from the request body are included.  Returns True if a row was updated.

        SQL origin: account_routes.update_kb_account.
        """
        ...
