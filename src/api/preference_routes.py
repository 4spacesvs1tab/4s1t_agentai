"""
User model preference and favourites routes.

6C.3 — GET /api/v1/user/preferences  and  PUT /api/v1/user/preferences
6C.4 — POST /api/v1/user/favourites/{provider}/{model_id}
        DELETE /api/v1/user/favourites/{provider}/{model_id}
"""
from typing import Dict, Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.security_dependencies import require_2fa
from database.connection import get_database_connection
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(prefix="/api/v1/user", tags=["user-preferences"])

VALID_ROUTES = {"webui", "nip17", "api"}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RoutePreference(BaseModel):
    provider_name: Optional[str] = None
    model_id: Optional[str] = None


class UserPreferencesRequest(BaseModel):
    """Body for PUT /api/v1/user/preferences.

    Each key must be one of: webui, nip17, api.
    Omitting a key leaves that route's preference unchanged.
    """
    preferences: Dict[str, RoutePreference]


class FavouriteModel(BaseModel):
    provider_name: str
    model_id: str


class UserPreferencesResponse(BaseModel):
    preferences: Dict[str, RoutePreference]
    favourites: List[FavouriteModel]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_preferences(user_id: str) -> Dict[str, RoutePreference]:
    db = get_database_connection()
    rows = db.execute_query(
        "SELECT route, provider_name, model_id FROM user_model_preferences WHERE user_id = ?",
        (user_id,),
    )
    return {
        row["route"]: RoutePreference(
            provider_name=row["provider_name"],
            model_id=row["model_id"],
        )
        for row in rows
    }


def _get_favourites(user_id: str) -> List[FavouriteModel]:
    db = get_database_connection()
    rows = db.execute_query(
        "SELECT provider_name, model_id FROM user_model_favourites WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )
    return [FavouriteModel(provider_name=r["provider_name"], model_id=r["model_id"]) for r in rows]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/preferences", response_model=UserPreferencesResponse)
async def get_preferences(current_user: dict = Depends(require_2fa)):
    """Return the authenticated user's model preferences and favourite models."""
    user_id = current_user["id"]
    return UserPreferencesResponse(
        preferences=_get_preferences(user_id),
        favourites=_get_favourites(user_id),
    )


@router.put("/preferences", response_model=UserPreferencesResponse)
async def update_preferences(
    body: UserPreferencesRequest,
    current_user: dict = Depends(require_2fa),
):
    """
    Upsert per-route model preferences for the authenticated user.

    Only routes present in the request body are updated; others are untouched.
    """
    user_id = current_user["id"]

    invalid = set(body.preferences) - VALID_ROUTES
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid route(s): {sorted(invalid)}. Must be one of: {sorted(VALID_ROUTES)}",
        )

    db = get_database_connection()
    for route, pref in body.preferences.items():
        db.execute_command(
            """
            INSERT INTO user_model_preferences (user_id, route, provider_name, model_id, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id, route) DO UPDATE SET
                provider_name = excluded.provider_name,
                model_id      = excluded.model_id,
                updated_at    = excluded.updated_at
            """,
            (user_id, route, pref.provider_name, pref.model_id),
        )

    logger.info(f"Updated model preferences for user {user_id}: {list(body.preferences)}")
    return UserPreferencesResponse(
        preferences=_get_preferences(user_id),
        favourites=_get_favourites(user_id),
    )


@router.post("/favourites/{provider}/{model_id:path}", status_code=status.HTTP_201_CREATED)
async def add_favourite(
    provider: str,
    model_id: str,
    current_user: dict = Depends(require_2fa),
):
    """Pin a model as a favourite. Idempotent — adding the same model twice is a no-op."""
    user_id = current_user["id"]
    db = get_database_connection()
    try:
        db.execute_command(
            """
            INSERT OR IGNORE INTO user_model_favourites (user_id, provider_name, model_id)
            VALUES (?, ?, ?)
            """,
            (user_id, provider, model_id),
        )
    except Exception as exc:
        logger.error(f"Error adding favourite: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add favourite")

    logger.info(f"User {user_id} added favourite: {provider}/{model_id}")
    return {"provider_name": provider, "model_id": model_id, "status": "added"}


@router.delete("/favourites/{provider}/{model_id:path}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favourite(
    provider: str,
    model_id: str,
    current_user: dict = Depends(require_2fa),
):
    """Remove a model from favourites. No-op if it was not pinned."""
    user_id = current_user["id"]
    db = get_database_connection()
    try:
        db.execute_command(
            "DELETE FROM user_model_favourites WHERE user_id = ? AND provider_name = ? AND model_id = ?",
            (user_id, provider, model_id),
        )
    except Exception as exc:
        logger.error(f"Error removing favourite: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to remove favourite")

    logger.info(f"User {user_id} removed favourite: {provider}/{model_id}")
