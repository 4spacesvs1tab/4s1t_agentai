"""Discovery queue management endpoints — /discovery."""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.kb._deps import get_discovery_service, require_2fa
from application.discovery_service import DiscoveryService
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter()


# ===========================================================================
# Pydantic models
# ===========================================================================

class ApproveBody(BaseModel):
    domains: str = Field(..., description="Pipe-separated domain IDs (as defined in kb_domains.yaml), e.g. 'domain_a|domain_b'")
    aliases: Optional[Dict[str, str]] = None


class SetStatusBody(BaseModel):
    status: str = Field(..., description="New status: 'pending' | 'rejected' | 'blacklisted'")


# ===========================================================================
# Discovery queue
# ===========================================================================

@router.get("/discovery")
async def list_discovery_candidates(
    status_filter: Optional[str] = None,
    min_mentions: int = 1,
    current_user: dict = Depends(require_2fa),
    svc: DiscoveryService = Depends(get_discovery_service),
):
    """
    List L2 discovery candidates for the authenticated user.

    Query params:
      status_filter: 'pending' | 'approved' | 'rejected' | all (default)
      min_mentions: only return candidates with mention_count >= this (default 1)
    """
    return await svc.list_candidates(
        user_id=current_user["id"],
        status_filter=status_filter,
        min_mentions=min_mentions,
    )


@router.post("/discovery/{candidate_id}/approve", status_code=status.HTTP_201_CREATED)
async def approve_discovery_candidate(
    candidate_id: int,
    body: ApproveBody,
    current_user: dict = Depends(require_2fa),
    svc: DiscoveryService = Depends(get_discovery_service),
):
    """
    Approve a discovery candidate: create an L2 kb_accounts row.

    Returns the new account_id on success.
    """
    account_id = await svc.approve_candidate(
        candidate_id=candidate_id,
        user_id=current_user["id"],
        domains=body.domains,
        aliases=body.aliases,
    )
    if account_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery candidate {candidate_id} not found for this user.",
        )
    return {"account_id": account_id}


@router.post("/discovery/{candidate_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_discovery_candidate(
    candidate_id: int,
    current_user: dict = Depends(require_2fa),
    svc: DiscoveryService = Depends(get_discovery_service),
):
    """Mark a discovery candidate as rejected."""
    ok = await svc.reject_candidate(
        candidate_id=candidate_id,
        user_id=current_user["id"],
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Discovery candidate {candidate_id} not found for this user.",
        )


@router.post("/discovery/{candidate_id}/set-status", status_code=status.HTTP_204_NO_CONTENT)
async def set_discovery_candidate_status(
    candidate_id: int,
    body: SetStatusBody,
    current_user: dict = Depends(require_2fa),
    svc: DiscoveryService = Depends(get_discovery_service),
):
    """Force-set a candidate's status (pending / rejected / blacklisted) without creating an account."""
    ok = await svc.set_status(
        candidate_id=candidate_id,
        user_id=current_user["id"],
        new_status=body.status,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not set status to '{body.status}' for candidate {candidate_id}.",
        )
