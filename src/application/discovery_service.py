"""
DiscoveryService — application service for L2 discovery candidates.

Orchestrates the discovery lifecycle via DiscoveryManager (domain) and
AccountRepository (persistence).  Route handlers call this service; they
never touch DiscoveryManager or AccountRepository directly.

DDD Rule 3: No FastAPI / HTTPException / Request imports here.
"""
from __future__ import annotations

from typing import Optional

from kb.discovery import DiscoveryManager
from kb.ports.account_repository import AccountRepository
from kb.ports.discovery_repository import DiscoveryRepository
from utils.logger import setup_logger

logger = setup_logger(__name__)


class DiscoveryService:
    """Application service for managing L2 discovery candidates.

    Constructor receives a DiscoveryRepository and AccountRepository so that
    unit tests can supply in-memory fakes without touching SQLite.

    Internally a DiscoveryManager is constructed with the provided repo so
    that the existing domain logic (approve_candidate dedup, relation wiring,
    etc.) is reused verbatim.
    """

    def __init__(
        self,
        discovery_repo: DiscoveryRepository,
        account_repo: AccountRepository,
    ) -> None:
        self._dm = DiscoveryManager(discovery_repo=discovery_repo)
        self._account_repo = account_repo

    # ------------------------------------------------------------------
    # list_candidates
    # ------------------------------------------------------------------

    async def list_candidates(
        self,
        user_id: str,
        status_filter: Optional[str] = None,
        min_mentions: int = 1,
    ) -> dict:
        """Return discovery candidates for *user_id*, enriched with via-account info.

        Mirrors the logic in discovery_routes.list_discovery_candidates verbatim.
        Via-info enrichment uses AccountRepository.find_active_by_user() filtered
        in Python; inactive via-accounts will have an empty name/domains entry
        (edge case documented for E3-continuation).
        """
        candidates = [
            c for c in self._dm.get_all(user_id, status=status_filter)
            if c.mention_count >= min_mentions
        ]

        via_ids = list({c.discovered_via for c in candidates if c.discovered_via})
        via_info: dict[str, dict] = {}
        if via_ids:
            try:
                via_id_set = set(via_ids)
                accounts = self._account_repo.find_active_by_user(user_id)
                via_info = {
                    a.id: {"name": a.display_name, "domains": a.domains or ""}
                    for a in accounts
                    if a.id in via_id_set
                }
            except Exception:
                pass  # best-effort — same behaviour as the original raw-SQL try/except

        return {
            "candidates": [
                {
                    "id": c.id,
                    "candidate_name": c.candidate_name,
                    "candidate_handles": c.candidate_handles,
                    "discovered_via": c.discovered_via,
                    "discovered_via_name": via_info.get(c.discovered_via, {}).get(
                        "name", c.discovered_via or ""
                    ),
                    "discovered_via_domains": via_info.get(c.discovered_via, {}).get(
                        "domains", ""
                    ),
                    "evidence": c.evidence,
                    "mention_count": c.mention_count,
                    "discovery_source": c.discovery_source,
                    "rationale": c.rationale,
                    "status": c.status,
                    "created_at": c.created_at,
                    "reviewed_at": c.reviewed_at,
                }
                for c in candidates
            ]
        }

    # ------------------------------------------------------------------
    # approve_candidate
    # ------------------------------------------------------------------

    async def approve_candidate(
        self,
        candidate_id: int,
        user_id: str,
        domains: str,
        aliases: Optional[dict[str, str]] = None,
    ) -> Optional[str]:
        """Approve a discovery candidate, creating an L2 account.

        Returns the new account_id UUID, or None if the candidate was not found
        or approval failed.  Mirrors discovery_routes.approve_discovery_candidate.
        """
        return self._dm.approve_candidate(
            candidate_id=candidate_id,
            user_id=user_id,
            domains=domains,
            aliases=aliases,
        )

    # ------------------------------------------------------------------
    # reject_candidate
    # ------------------------------------------------------------------

    async def reject_candidate(self, candidate_id: int, user_id: str) -> bool:
        """Mark a discovery candidate as rejected.

        Returns True on success, False if the candidate was not found.
        Mirrors discovery_routes.reject_discovery_candidate.
        """
        return self._dm.reject_candidate(candidate_id=candidate_id, user_id=user_id)

    # ------------------------------------------------------------------
    # set_status
    # ------------------------------------------------------------------

    async def set_status(
        self,
        candidate_id: int,
        user_id: str,
        new_status: str,
    ) -> bool:
        """Force-set candidate status (pending / rejected / blacklisted).

        Returns True on success.  Mirrors discovery_routes.set_discovery_candidate_status.
        """
        return self._dm.set_status(
            candidate_id=candidate_id,
            user_id=user_id,
            new_status=new_status,
        )
