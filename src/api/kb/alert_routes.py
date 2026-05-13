"""KB alert subscription endpoints — /alerts."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.kb._deps import require_2fa, get_alert_service
from application.alert_service import AlertService
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter()


# ===========================================================================
# Pydantic models
# ===========================================================================

class AlertCreateBody(BaseModel):
    query: str = Field(..., description="Natural-language alert query")
    domain_filter: Optional[List[str]] = None
    account_filter: Optional[List[str]] = None
    similarity_threshold: float = Field(0.85, ge=0.0, le=1.0)


# ===========================================================================
# Alerts
# ===========================================================================

@router.get("/alerts")
async def list_kb_alerts(
    current_user: dict = Depends(require_2fa),
    svc: AlertService = Depends(get_alert_service),
):
    """List all active alert subscriptions for the authenticated user."""
    try:
        alerts = await svc.list_alerts(user_id=current_user["id"])
        return {"alerts": alerts}
    except Exception as exc:
        logger.error("list_kb_alerts failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load alerts")


@router.post("/alerts", status_code=status.HTTP_201_CREATED)
async def create_kb_alert(
    body: AlertCreateBody,
    current_user: dict = Depends(require_2fa),
    svc: AlertService = Depends(get_alert_service),
):
    """
    Create a new semantic alert subscription.

    The server embeds the query text using bge-m3 (NANO_GPT_API_KEY required).
    """
    try:
        alert_id = await svc.create_alert(
            user_id=current_user["id"],
            query=body.query,
            domain_filter=body.domain_filter,
            account_filter=body.account_filter,
            similarity_threshold=body.similarity_threshold,
        )
        return {"alert_id": alert_id}
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb_alert(
    alert_id: str,
    current_user: dict = Depends(require_2fa),
    svc: AlertService = Depends(get_alert_service),
):
    """Deactivate an alert (soft delete — sets active=0)."""
    try:
        await svc.delete_alert(alert_id=alert_id, user_id=current_user["id"])
    except LookupError:
        raise HTTPException(status_code=404, detail="Alert not found")
    except Exception as exc:
        logger.error("delete_kb_alert failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete alert")
