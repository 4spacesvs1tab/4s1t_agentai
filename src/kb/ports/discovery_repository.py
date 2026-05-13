"""
DiscoveryRepository — domain port for KB discovery-queue persistence.

Rule: this file must never import sqlite3, httpx, os.environ, or any I/O
library.  Only standard-library ABCs and TYPE_CHECKING imports are allowed.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kb.discovery import DiscoveryCandidate


class DiscoveryRepository(ABC):
    """Abstract persistence interface for DiscoveryCandidate queue.

    Implementations live in src/infrastructure/sqlite/.
    Wire a concrete implementation at the composition root (initializer).

    SQL origin (all from discovery.py):
      find_pending              ← get_pending()             kb_discovery_queue SELECT
      find_all                  ← get_all()                 kb_discovery_queue SELECT
      find_by_id                ← approve_candidate()       kb_discovery_queue SELECT by PK
      find_active_account_by_handle ← upsert/approve       kb_account_aliases JOIN kb_accounts
      find_active_account_by_name   ← upsert_candidate()   kb_accounts SELECT by display_name
      find_candidate_by_handle  ← upsert_candidate()       kb_discovery_queue handle LIKE
      find_candidate_by_name    ← upsert_candidate()       kb_discovery_queue name lower()
      insert_candidate          ← upsert_candidate()       kb_discovery_queue INSERT
      update_candidate          ← upsert_candidate()       kb_discovery_queue UPDATE
      set_status                ← reject/set_status/approve kb_discovery_queue UPDATE status
      merge_aliases_into_account← approve_candidate()      kb_account_aliases INSERT OR IGNORE
      create_account_with_aliases ← approve_candidate()    kb_accounts + kb_account_aliases INSERT
      insert_discovered_via_relation ← approve_candidate() kb_relations INSERT OR IGNORE
    """

    @abstractmethod
    def find_pending(
        self, user_id: str, min_mentions: int = 3
    ) -> list[DiscoveryCandidate]:
        """Return pending candidates with mention_count >= *min_mentions*."""
        ...

    @abstractmethod
    def find_all(
        self, user_id: str, status: str | None = None
    ) -> list[DiscoveryCandidate]:
        """Return all candidates, optionally filtered by *status*."""
        ...

    @abstractmethod
    def find_by_id(
        self, candidate_id: int, user_id: str
    ) -> DiscoveryCandidate | None:
        """Return a single candidate by primary key, or None."""
        ...

    @abstractmethod
    def find_active_account_by_handle(
        self, user_id: str, platform: str, handle: str
    ) -> str | None:
        """Return account_id if an active account owns this platform handle, else None.

        Joins kb_account_aliases with kb_accounts.
        """
        ...

    @abstractmethod
    def find_active_account_by_name(
        self, user_id: str, name: str
    ) -> str | None:
        """Return account_id if an active account has this display_name (case-insensitive), else None."""
        ...

    @abstractmethod
    def find_candidate_by_handle(
        self, user_id: str, handle_fragment: str
    ) -> DiscoveryCandidate | None:
        """Return the most-recent non-approved/non-blacklisted queue row whose
        candidate_handles JSON contains *handle_fragment*, or None.

        *handle_fragment* is the literal handle string used in LIKE '%"<handle>"%' matching.
        """
        ...

    @abstractmethod
    def find_candidate_by_name(
        self, user_id: str, name: str
    ) -> DiscoveryCandidate | None:
        """Return the most-recent queue row matching *name* (case-insensitive), or None."""
        ...

    @abstractmethod
    def insert_candidate(
        self,
        user_id: str,
        name: str,
        handles: dict,
        discovered_via: str | None,
        evidence: list[str],
        rationale: str,
    ) -> None:
        """Insert a new kb_discovery_queue row with status='pending', mention_count=1."""
        ...

    @abstractmethod
    def update_candidate(
        self,
        candidate_id: int,
        mention_count: int,
        evidence: list[str],
        handles: dict,
        rationale: str | None,
    ) -> None:
        """Update mention_count, evidence, and candidate_handles for an existing row.

        If *rationale* is not None it is also updated; otherwise the existing
        value is preserved.
        """
        ...

    @abstractmethod
    def set_status(
        self,
        candidate_id: int,
        user_id: str,
        status: str,
        reviewed_at: str,
    ) -> None:
        """Set kb_discovery_queue.status and reviewed_at for *candidate_id*."""
        ...

    @abstractmethod
    def merge_aliases_into_account(
        self, account_id: str, aliases: dict[str, str]
    ) -> None:
        """INSERT OR IGNORE aliases into kb_account_aliases for an existing account."""
        ...

    @abstractmethod
    def create_account_with_aliases(
        self,
        account_id: str,
        user_id: str,
        display_name: str,
        domains: str,
        aliases: dict[str, str],
    ) -> None:
        """Insert into kb_accounts (layer=2, added_by='agent') and kb_account_aliases."""
        ...

    @abstractmethod
    def insert_discovered_via_relation(
        self, from_id: str, to_id: str
    ) -> None:
        """INSERT OR IGNORE a 'discovered_via' edge into kb_relations."""
        ...
