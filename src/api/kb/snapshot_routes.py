"""KB snapshot management endpoints — /snapshots."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.kb._deps import require_2fa, get_snapshot_service
from application.snapshot_service import SnapshotService
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter()


# ===========================================================================
# Pydantic models
# ===========================================================================

class SnapshotSaveBody(BaseModel):
    topic_query: str
    summary: str
    source_ids: Optional[List[str]] = None
    session_id: Optional[str] = None


class SnapshotCompareQuery(BaseModel):
    topic_query: str


# ===========================================================================
# Snapshots
# ===========================================================================

@router.get("/snapshots")
async def list_snapshots(
    topic: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(require_2fa),
    svc: SnapshotService = Depends(get_snapshot_service),
):
    """
    List topic snapshots for the authenticated user.

    If `topic` is given, returns only snapshots for that topic (full content).
    Otherwise returns the index (no summary bodies).
    """
    rows = await svc.list_snapshots(
        user_id=current_user["id"],
        topic=topic,
        limit=limit,
    )
    return {"snapshots": rows}


@router.post("/snapshots", status_code=status.HTTP_201_CREATED)
async def save_snapshot(
    body: SnapshotSaveBody,
    current_user: dict = Depends(require_2fa),
    svc: SnapshotService = Depends(get_snapshot_service),
):
    """Save a new topic snapshot."""
    snapshot_id = await svc.create_snapshot(
        user_id=current_user["id"],
        topic_query=body.topic_query,
        summary=body.summary,
        source_ids=body.source_ids,
        session_id=body.session_id,
    )
    return {"snapshot_id": snapshot_id}


@router.get("/snapshots/compare")
async def compare_snapshots(
    topic: str,
    current_user: dict = Depends(require_2fa),
    svc: SnapshotService = Depends(get_snapshot_service),
):
    """
    Diff the two most recent snapshots for `topic` using the LLM.

    Returns a markdown changelog or 404 if fewer than 2 snapshots exist.
    """
    try:
        diff = await svc.compare_snapshots(
            user_id=current_user["id"],
            topic_query=topic,
        )
        return {"topic": topic, "diff": diff}
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Need at least 2 snapshots for this topic to compute a diff.",
        )


@router.delete("/snapshots/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_snapshot(
    snapshot_id: str,
    current_user: dict = Depends(require_2fa),
    svc: SnapshotService = Depends(get_snapshot_service),
):
    """Delete a snapshot by ID."""
    try:
        await svc.delete_snapshot(
            snapshot_id=snapshot_id,
            user_id=current_user["id"],
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Snapshot not found")
