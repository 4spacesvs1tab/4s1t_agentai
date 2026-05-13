"""
KB L2 Discovery Manager — Phase KB-3.

Manages the `kb_discovery_queue` table: the staging area for agent-discovered
accounts awaiting user review before promotion to the social graph (layer=2).

Workflow:
  1. Entity extractor finds a new name in ingested content.
  2. `upsert_candidate()` checks if the name is already a known L1/L2 account.
     If not, it inserts or increments `mention_count` in kb_discovery_queue.
  3. When mention_count reaches PROMOTE_THRESHOLD, the candidate is surfaced
     to the user (via brief / NIP-17 alert or the kb_monitor_agent brief).
  4. User approves → `approve_candidate()` creates an L2 kb_accounts row.
  5. User rejects → `reject_candidate()` marks status='rejected'.

Design reference: KnowledgeBase_design.md §6.5
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core.db_path import get_db_path
from kb.entity_extractor import ExtractedEntity, normalize_handle_hints
from kb.ports.discovery_repository import DiscoveryRepository
from utils.logger import setup_logger

logger = setup_logger(__name__)


def _build_rationale(entity) -> str:
    """Build a human-readable rationale string from entity extraction context."""
    parts = []
    if entity.relation and entity.relation != "mentioned":
        parts.append(entity.relation)
    if entity.sentiment and entity.sentiment != "neutral":
        parts.append(entity.sentiment)
    prefix = " · ".join(parts) if parts else ""
    if entity.snippet:
        snippet = entity.snippet[:300].rstrip()
        return f"{prefix} · \"{snippet}\"" if prefix else f"\"{snippet}\""
    if prefix:
        return prefix
    return ""

# Mention count required before a candidate is surfaced to the user
PROMOTE_THRESHOLD = 3


@dataclass
class DiscoveryCandidate:
    """A row from kb_discovery_queue."""
    id: int
    user_id: str
    candidate_name: str
    candidate_handles: dict        # JSON: {platform: handle}
    discovered_via: str            # source account_id
    evidence: list[str]            # list of source URLs
    mention_count: int
    discovery_source: str          # 'ingestion' | 'web_research'
    rationale: str
    status: str                    # 'pending' | 'approved' | 'rejected'
    created_at: str
    reviewed_at: Optional[str]


class DiscoveryManager:
    """CRUD manager for kb_discovery_queue (L2 discovery candidates)."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        *,
        discovery_repo: DiscoveryRepository,
    ) -> None:
        self._db_path = db_path or str(get_db_path())
        self._repo = discovery_repo

    # ------------------------------------------------------------------
    # Upsert (called from preprocessor after entity extraction)
    # ------------------------------------------------------------------

    def upsert_candidate(
        self,
        entity: ExtractedEntity,
        existing_account_ids: set[str] | None = None,
    ) -> bool:
        """
        Insert or increment a discovery candidate derived from entity extraction.

        Returns True if the candidate was newly promoted to >= PROMOTE_THRESHOLD.

        Skips if:
          - entity.name is already in the active kb_accounts for this user.
          - entity.name matches an existing kb_discovery_queue row with
            status='approved' or status='rejected'.
        """
        if not entity.name:
            return False

        # Reject candidates with no platform handles — without at least one handle
        # (Twitter, Nostr, YouTube, etc.) there is nothing to follow or ingest.
        if not entity.handle_hints:
            logger.debug("Skipping no-handle candidate: %r", entity.name)
            return False

        # Normalise platform names before any lookups
        norm_hints = normalize_handle_hints(entity.handle_hints)

        try:
            # ------------------------------------------------------------------
            # 1. Handle-based check: skip if any handle already exists in
            #    kb_account_aliases for an active account of this user.
            # ------------------------------------------------------------------
            for plat, handle in norm_hints.items():
                if self._repo.find_active_account_by_handle(entity.user_id, plat, handle):
                    logger.debug(
                        "Skipping %r — handle %s:%s already in kb_accounts",
                        entity.name, plat, handle,
                    )
                    return False

            # ------------------------------------------------------------------
            # 2. Name-based check: skip if display_name already exists.
            # ------------------------------------------------------------------
            if self._repo.find_active_account_by_name(entity.user_id, entity.name):
                return False  # already tracked

            # ------------------------------------------------------------------
            # 3. Handle-based dedup against existing queue entries: if any handle
            #    matches an existing pending/open entry, merge into that entry
            #    instead of creating a new one.
            # ------------------------------------------------------------------
            row: DiscoveryCandidate | None = None
            for plat, handle in norm_hints.items():
                candidate_row = self._repo.find_candidate_by_handle(entity.user_id, handle)
                if candidate_row and candidate_row.status not in ("approved", "blacklisted"):
                    row = candidate_row
                    break

            # ------------------------------------------------------------------
            # 4. Name-based dedup as fallback.
            # ------------------------------------------------------------------
            if row is None:
                row = self._repo.find_candidate_by_name(entity.user_id, entity.name)

            if row:
                if row.status in ("approved", "rejected", "blacklisted"):
                    return False  # already decided

                old_count = row.mention_count
                new_count = old_count + 1

                # Merge evidence URLs
                old_evidence: list[str] = list(row.evidence)
                if entity.source_url and entity.source_url not in old_evidence:
                    old_evidence.append(entity.source_url)

                # Merge handle hints (use normalised hints)
                old_handles: dict = normalize_handle_hints(dict(row.candidate_handles))
                for plat, handle in norm_hints.items():
                    if plat not in old_handles:
                        old_handles[plat] = handle

                # Build updated rationale from latest mention if richer than existing
                new_rationale = _build_rationale(entity)
                update_rationale = bool(new_rationale and new_rationale != row.rationale)

                self._repo.update_candidate(
                    candidate_id=row.id,
                    mention_count=new_count,
                    evidence=old_evidence,
                    handles=old_handles,
                    rationale=new_rationale if update_rationale else None,
                )

                promoted = old_count < PROMOTE_THRESHOLD <= new_count
                if promoted:
                    logger.info(
                        "Discovery candidate %r reached threshold (%d mentions) for user=%s",
                        entity.name, new_count, entity.user_id,
                    )
                return promoted

            else:
                # New candidate
                evidence = [entity.source_url] if entity.source_url else []
                self._repo.insert_candidate(
                    user_id=entity.user_id,
                    name=entity.name,
                    handles=norm_hints,
                    discovered_via=entity.discovered_via_account_id or None,
                    evidence=evidence,
                    rationale=(
                        _build_rationale(entity)
                        or f"Mentioned in content from {entity.discovered_via_account_id or 'unknown source'}"
                    ),
                )
                logger.debug(
                    "New L2 discovery candidate: %r (user=%s)", entity.name, entity.user_id
                )
                return False

        except Exception as exc:
            logger.warning("Failed to upsert discovery candidate %r: %s", entity.name, exc)
            return False

    # ------------------------------------------------------------------
    # ContentIngested event handler (Sprint 8 placeholder)
    # ------------------------------------------------------------------

    def handle_content_ingested(self, event: "ContentIngested") -> None:
        """
        ContentIngested subscriber — Sprint 8 placeholder.

        Full entity/discovery decoupling via an EntitiesExtracted domain event
        is planned for Sprint 8. Currently, entity extraction triggers discovery
        upsert directly inside EntityExtractor.handle_content_ingested to avoid
        a duplicate LLM call. This method is registered as a subscriber to
        satisfy the three-subscriber architecture and will be populated in Sprint 8.

        Sprint 8 plan:
          1. EntityExtractor fires EntitiesExtracted(entities=[...], ...) after extraction.
          2. DiscoveryManager subscribes to EntitiesExtracted and calls upsert_candidate()
             for each entity — no LLM call required in this handler.
          3. Remove entity+discovery coupling from EntityExtractor.handle_content_ingested.
          4. Remove the None-fallback event_bus default from KBPreprocessor.__init__.
        """
        # No-op until Sprint 8 introduces EntitiesExtracted event.

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_pending(self, user_id: str, min_mentions: int = PROMOTE_THRESHOLD) -> list[DiscoveryCandidate]:
        """Return pending candidates with >= min_mentions for *user_id*."""
        return self._repo.find_pending(user_id, min_mentions)

    def get_all(self, user_id: str, status: Optional[str] = None) -> list[DiscoveryCandidate]:
        """Return all discovery candidates for *user_id*, optionally filtered by status."""
        return self._repo.find_all(user_id, status)

    # ------------------------------------------------------------------
    # Approve / Reject
    # ------------------------------------------------------------------

    def approve_candidate(
        self,
        candidate_id: int,
        user_id: str,
        domains: str,
        aliases: Optional[dict[str, str]] = None,
    ) -> Optional[str]:
        """
        Approve a discovery candidate: create an L2 kb_accounts row.

        Returns the new account_id (UUID) or None on failure.
        """
        try:
            candidate = self._repo.find_by_id(candidate_id, user_id)
            if not candidate:
                logger.warning("approve_candidate: no row id=%d user=%s", candidate_id, user_id)
                return None

            if candidate.status == "approved":
                logger.warning(
                    "approve_candidate: candidate %d already approved, skipping account creation", candidate_id
                )
                return None

            # Merge handle hints from queue as aliases (normalise platforms)
            merged_aliases = normalize_handle_hints(dict(candidate.candidate_handles))
            if aliases:
                merged_aliases.update(normalize_handle_hints(aliases))

            if not merged_aliases:
                logger.warning(
                    "approve_candidate: candidate %d (%r) has no platform aliases — "
                    "refusing to create orphan account",
                    candidate_id, candidate.candidate_name,
                )
                return None

            # ------------------------------------------------------------------
            # Guard: if any identity-unique handle already exists in an active
            # account, merge this candidate's aliases into that account instead
            # of creating a duplicate.  Identity-unique platforms: twitter,
            # nostr, youtube (globally unique IDs / handles).
            # ------------------------------------------------------------------
            _IDENTITY_PLATFORMS = {"twitter", "nostr", "youtube"}
            existing_account_id: str | None = None
            for plat, handle in merged_aliases.items():
                if plat not in _IDENTITY_PLATFORMS:
                    continue
                found = self._repo.find_active_account_by_handle(user_id, plat, handle)
                if found:
                    existing_account_id = found
                    logger.info(
                        "approve_candidate: handle %s:%s already in account %s — merging instead of creating",
                        plat, handle, existing_account_id,
                    )
                    break

            now = datetime.now(timezone.utc).isoformat()

            if existing_account_id:
                # Merge any new aliases into the existing account
                self._repo.merge_aliases_into_account(existing_account_id, merged_aliases)
                self._repo.set_status(candidate_id, user_id, "approved", now)
                logger.info(
                    "Merged candidate %r into existing account %s (handle conflict)",
                    candidate.candidate_name, existing_account_id,
                )
                return existing_account_id

            # Create account
            account_id = str(uuid.uuid4())
            self._repo.create_account_with_aliases(
                account_id=account_id,
                user_id=user_id,
                display_name=candidate.candidate_name,
                domains=domains,
                aliases=merged_aliases,
            )
            self._repo.set_status(candidate_id, user_id, "approved", now)

            # Wire L1 → L2 relation
            if candidate.discovered_via:
                try:
                    self._repo.insert_discovered_via_relation(candidate.discovered_via, account_id)
                except Exception as exc:
                    logger.warning("Could not add graph relation for %s: %s", account_id, exc)

            logger.info(
                "Approved L2 candidate %r → account_id=%s (user=%s)",
                candidate.candidate_name, account_id, user_id,
            )
            return account_id

        except Exception as exc:
            logger.error("approve_candidate failed id=%d: %s", candidate_id, exc)
            return None

    def reject_candidate(self, candidate_id: int, user_id: str) -> bool:
        """Mark a discovery candidate as rejected."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            self._repo.set_status(candidate_id, user_id, "rejected", now)
            return True
        except Exception as exc:
            logger.warning("reject_candidate failed id=%d: %s", candidate_id, exc)
            return False

    def set_status(self, candidate_id: int, user_id: str, new_status: str) -> bool:
        """Force-set a candidate's status without creating an account (any → any except approved)."""
        valid = {"pending", "rejected", "blacklisted"}
        if new_status not in valid:
            logger.warning("set_status: invalid status %r", new_status)
            return False
        try:
            now = datetime.now(timezone.utc).isoformat()
            self._repo.set_status(candidate_id, user_id, new_status, now)
            return True
        except Exception as exc:
            logger.warning("set_status failed id=%d: %s", candidate_id, exc)
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager: DiscoveryManager | None = None


def get_discovery_manager(db_path: Optional[str] = None) -> DiscoveryManager:
    """Return the shared DiscoveryManager singleton.

    In production, the singleton is pre-wired in initializer.py with an explicit
    SqliteDiscoveryRepository.  This fallback path handles out-of-process callers
    (e.g. scripts and tests that bypass the initializer).
    """
    global _manager
    if _manager is None:
        from infrastructure.sqlite.sqlite_discovery_repository import SqliteDiscoveryRepository
        db = db_path or str(get_db_path())
        _manager = DiscoveryManager(db_path=db, discovery_repo=SqliteDiscoveryRepository(db))
    return _manager
