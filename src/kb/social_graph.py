"""
KB Social Graph Manager — account entity and relationship management.

Responsibilities:
  - Load and cache kb_accounts + kb_account_aliases from SQLite
  - Maintain in-memory NetworkX DiGraph for fast graph traversal
  - Provide domain-scoped account list for KB ingestion filtering
  - Expose account lookup by platform ID (e.g., Twitter handle → account_id)
  - Phase KB-1: L1 accounts only; L2 discovery wired in Phase KB-3

Design reference: KnowledgeBase_design.md §6.1
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from core.db_path import get_db_path
from kb.domain.value_objects import Layer, Platform
from kb.ports.account_repository import AccountRepository
from utils.logger import setup_logger

logger = setup_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class KBAccount:
    """A tracked content source (person, show, publication)."""
    id: str
    user_id: str
    display_name: str
    layer: int | Layer     # 1=manual, 2=approved, 3=pending; use Layer enum preferred
    domains: str           # pipe-separated domain IDs
    active: bool
    added_by: str
    # Populated from kb_account_aliases
    aliases: dict[str, str] = field(default_factory=dict)  # platform → platform_id

    @property
    def domain_list(self) -> list[str]:
        return [d for d in self.domains.split("|") if d]


# ---------------------------------------------------------------------------
# SocialGraph
# ---------------------------------------------------------------------------

class SocialGraph:
    """
    In-memory social graph backed by SQLite via AccountRepository.

    Load once at startup via `load(user_id)`. The graph is refreshed on
    explicit `reload()` call — no automatic background refresh in KB-1.

    NetworkX DiGraph:
      nodes = account_id strings
      node attributes: display_name, layer, domains, active, user_id
      edges: (from_id, to_id, relation_type, weight)
    """

    def __init__(
        self,
        db_path: str | None = None,
        *,
        account_repo: AccountRepository,
    ) -> None:
        self._db_path = db_path or str(get_db_path())
        self._repo = account_repo
        # Dict keyed by user_id → dict of account_id → KBAccount
        self._accounts: dict[str, dict[str, KBAccount]] = {}
        # NetworkX graph per user_id (lazy import)
        self._graphs: dict[str, object] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, user_id: str = "default") -> None:
        """Load all active accounts and their aliases for *user_id* from DB."""
        try:
            import networkx as nx
        except ImportError:
            logger.warning("networkx not installed — graph features disabled; pip install networkx")
            nx = None

        accounts_list = self._repo.find_active_by_user(user_id)
        if not accounts_list and user_id not in self._accounts:
            # Table may be missing on fresh install — treat as empty
            logger.warning("kb_accounts table missing or empty for user=%s — run migration 010 first", user_id)

        accounts: dict[str, KBAccount] = {acc.id: acc for acc in accounts_list}
        self._accounts[user_id] = accounts

        # Build graph
        if nx is not None:
            g = nx.DiGraph()
            for acc in accounts.values():
                g.add_node(
                    acc.id,
                    display_name=acc.display_name,
                    layer=acc.layer,
                    domains=acc.domains,
                    user_id=acc.user_id,
                )
            # Edges (kb_relations)
            relations = self._repo.find_relations_by_account_ids(list(accounts.keys()))
            for from_id, to_id, relation_type, weight in relations:
                g.add_edge(from_id, to_id, relation_type=relation_type, weight=weight)
            self._graphs[user_id] = g

        logger.info(
            "SocialGraph loaded for user=%s: %d accounts",
            user_id, len(accounts),
        )

    def reload(self, user_id: str = "default") -> None:
        """Reload graph from DB (drops cached data first)."""
        self._accounts.pop(user_id, None)
        self._graphs.pop(user_id, None)
        self.load(user_id)

    # ------------------------------------------------------------------
    # Account queries
    # ------------------------------------------------------------------

    def get_account(self, account_id: str, user_id: str = "default") -> Optional[KBAccount]:
        """Return KBAccount by *account_id*, or None if not found."""
        if user_id not in self._accounts:
            self.load(user_id)
        return self._accounts.get(user_id, {}).get(account_id)

    def accounts_for_domain(
        self,
        domain: str,
        user_id: str = "default",
        layer: int | Layer | None = None,
    ) -> list[KBAccount]:
        """
        Return active accounts whose domains include *domain*.

        Args:
            domain: Domain ID string (e.g. "macroeconomics").
            user_id: Filter by user.
            layer: If provided, only return accounts of this layer. Accepts int or Layer enum.
        """
        if user_id not in self._accounts:
            self.load(user_id)
        result = []
        for acc in self._accounts.get(user_id, {}).values():
            if domain in acc.domain_list:
                if layer is None or acc.layer == layer:
                    result.append(acc)
        return result

    def all_accounts(self, user_id: str = "default") -> list[KBAccount]:
        """Return all active accounts for *user_id*."""
        if user_id not in self._accounts:
            self.load(user_id)
        return list(self._accounts.get(user_id, {}).values())

    def find_by_platform_id(
        self,
        platform: str | Platform,
        platform_id: str,
        user_id: str = "default",
    ) -> Optional[KBAccount]:
        """Look up an account by platform and platform ID (e.g., twitter, @handle)."""
        if user_id not in self._accounts:
            self.load(user_id)
        platform_key = platform.value if isinstance(platform, Platform) else platform
        for acc in self._accounts.get(user_id, {}).values():
            if acc.aliases.get(platform_key) == platform_id:
                return acc
        return None

    def account_exists(self, account_id: str, user_id: str = "default") -> bool:
        """Return True if *account_id* is a known account for *user_id*."""
        if user_id not in self._accounts:
            self.load(user_id)
        return account_id in self._accounts.get(user_id, {})

    # ------------------------------------------------------------------
    # Persistence (write)
    # ------------------------------------------------------------------

    def add_account(
        self,
        user_id: str,
        display_name: str,
        domains: str,
        layer: int | Layer = Layer.MANUAL,
        added_by: str = "user",
        aliases: dict[str, str] | None = None,
    ) -> str:
        """
        Add a new account to the DB and in-memory graph.

        Returns the new account_id (UUID).
        """
        account_id = str(uuid.uuid4())
        acc = KBAccount(
            id=account_id,
            user_id=user_id,
            display_name=display_name,
            layer=layer,
            domains=domains,
            active=True,
            added_by=added_by,
            aliases=aliases or {},
        )
        self._repo.save(acc)

        # Update in-memory cache
        if user_id not in self._accounts:
            self._accounts[user_id] = {}
        self._accounts[user_id][account_id] = acc
        logger.info("Added account %s (%s) for user=%s", display_name, account_id, user_id)
        return account_id

    # ------------------------------------------------------------------
    # Graph queries (Phase KB-3)
    # ------------------------------------------------------------------

    def get_graph(self, user_id: str = "default"):
        """Return the NetworkX DiGraph for *user_id*, loading if necessary."""
        if user_id not in self._graphs:
            self.load(user_id)
        return self._graphs.get(user_id)

    def get_l2_accounts(self, user_id: str = "default") -> list[KBAccount]:
        """Return all active L2 (agent-approved) accounts for *user_id*."""
        return [
            acc for acc in self.all_accounts(user_id)
            if acc.layer == Layer.APPROVED  # Layer.APPROVED == 2; old int values still match
        ]

    def add_relation(
        self,
        from_account_id: str,
        to_account_id: str,
        relation_type: str,
        weight: float = 1.0,
    ) -> None:
        """
        Add or strengthen a relation edge in kb_relations.

        If the relation already exists, increments evidence_count and updates
        last_seen / weight.
        """
        self._repo.upsert_relation(from_account_id, to_account_id, relation_type, weight)

        # Update in-memory graph if loaded
        for uid, g in self._graphs.items():
            try:
                g.add_edge(
                    from_account_id,
                    to_account_id,
                    relation_type=relation_type,
                    weight=weight,
                )
            except Exception:
                pass

    def get_neighbours(
        self,
        account_id: str,
        user_id: str = "default",
        relation_type: Optional[str] = None,
        direction: str = "both",
    ) -> list[KBAccount]:
        """
        Return accounts directly connected to *account_id* in the social graph.

        Args:
            account_id: Source node.
            relation_type: If specified, filter edges by this type.
            direction: 'out' (successors), 'in' (predecessors), 'both'.
        """
        g = self.get_graph(user_id)
        if g is None:
            return []
        try:
            neighbour_ids: set[str] = set()
            if direction in ("out", "both"):
                for nbr, data in g.adj.get(account_id, {}).items():
                    if relation_type is None or data.get("relation_type") == relation_type:
                        neighbour_ids.add(nbr)
            if direction in ("in", "both"):
                for src, adj_dict in g.pred.get(account_id, {}).items():
                    for _, data in adj_dict.items() if isinstance(adj_dict, dict) else []:
                        pass
                    # networkx DiGraph: pred[node] is {src: edge_data}
                    data = g.pred[account_id].get(src, {})
                    if relation_type is None or data.get("relation_type") == relation_type:
                        neighbour_ids.add(src)
        except Exception as exc:
            logger.debug("get_neighbours failed for %s: %s", account_id, exc)
            return []

        accounts = []
        for nid in neighbour_ids:
            acc = self.get_account(nid, user_id)
            if acc:
                accounts.append(acc)
        return accounts


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_graph: SocialGraph | None = None


def get_social_graph(db_path: str | None = None) -> SocialGraph:
    """Return the shared SocialGraph singleton.

    In production, the singleton is pre-wired in initializer.py with an explicit
    SqliteAccountRepository.  This fallback path handles out-of-process callers
    (e.g. scripts and tests that bypass the initializer).
    """
    global _graph
    if _graph is None:
        from infrastructure.sqlite.sqlite_account_repository import SqliteAccountRepository
        db = db_path or str(get_db_path())
        _graph = SocialGraph(db_path=db, account_repo=SqliteAccountRepository(db))
    return _graph
