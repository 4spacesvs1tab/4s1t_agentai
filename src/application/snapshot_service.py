"""
SnapshotService — application service for KB topic snapshot management.

Thin orchestration wrapper over kb.snapshot_service.SnapshotService.
Route handlers call this service; they never instantiate the domain service directly.

DDD Rule 3: No FastAPI / HTTPException / Request imports here.
No sqlite3.connect() — db_path injected at construction and forwarded to domain service.
"""
from __future__ import annotations

from typing import Optional

from utils.logger import setup_logger

logger = setup_logger(__name__)


class SnapshotService:
    """Application service for KB topic snapshots.

    Constructor receives db_path so unit tests can supply an in-memory path
    without touching the live database.  All persistence delegates to
    kb.snapshot_service.SnapshotService (domain layer).
    """

    def __init__(self, db_path: str) -> None:
        from kb.snapshot_service import SnapshotService as _KbSnapshotService
        self._svc = _KbSnapshotService(db_path)

    # ------------------------------------------------------------------
    # list_snapshots
    # ------------------------------------------------------------------

    async def list_snapshots(
        self,
        user_id: str,
        topic: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return topic snapshots for *user_id*.

        If *topic* is given, returns full content for that topic (newest first).
        Otherwise returns the index without summary bodies.

        Mirrors snapshot_routes.list_snapshots verbatim.
        """
        if topic:
            return self._svc.get_by_topic(user_id=user_id, topic_query=topic, limit=limit)
        return self._svc.get_all(user_id=user_id, limit=limit)

    # ------------------------------------------------------------------
    # create_snapshot
    # ------------------------------------------------------------------

    async def create_snapshot(
        self,
        user_id: str,
        topic_query: str,
        summary: str,
        source_ids: Optional[list[str]] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Save a new topic snapshot and return its UUID.

        Mirrors snapshot_routes.save_snapshot verbatim.
        """
        return self._svc.save(
            user_id=user_id,
            topic_query=topic_query,
            summary=summary,
            source_ids=source_ids,
            session_id=session_id,
        )

    # ------------------------------------------------------------------
    # compare_snapshots
    # ------------------------------------------------------------------

    async def compare_snapshots(self, user_id: str, topic_query: str) -> str:
        """Diff the two most recent snapshots for *topic_query* using the LLM.

        Raises LookupError if fewer than 2 snapshots exist (route maps to 404).
        Returns a markdown changelog string.

        Mirrors snapshot_routes.compare_snapshots verbatim.
        """
        diff = self._svc.compare(user_id=user_id, topic_query=topic_query)
        if diff is None:
            raise LookupError(
                "Need at least 2 snapshots for this topic to compute a diff."
            )
        return diff

    # ------------------------------------------------------------------
    # delete_snapshot
    # ------------------------------------------------------------------

    async def delete_snapshot(self, snapshot_id: str, user_id: str) -> None:
        """Delete a snapshot by ID (owner check via user_id).

        Raises LookupError if the snapshot does not exist (route maps to 404).
        Mirrors snapshot_routes.delete_snapshot verbatim.
        """
        ok = self._svc.delete(snapshot_id=snapshot_id, user_id=user_id)
        if not ok:
            raise LookupError(f"Snapshot not found: {snapshot_id}")
