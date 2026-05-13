"""
API Key management routes for 4S1T Agent AI system.
Handles generation, listing, and revocation of API keys.
"""
from typing import Optional, List
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from services.api_key_service import get_api_key_service, APIKeyService
from services.auth_service import get_auth_service, AuthService
from services.exceptions import DatabaseError, ValidationError, AuthError
from api.security_dependencies import require_2fa
from utils.logger import setup_logger

logger = setup_logger(__name__)

# Create router
router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


# Pydantic models for request/response
class APIKeyCreate(BaseModel):
    """API key creation request model."""
    name: str = Field(..., min_length=3, max_length=100, description="Human-readable name for the key")
    description: Optional[str] = Field(None, max_length=500, description="Optional description")
    scopes: str = Field(default="read", description="Comma-separated scopes (e.g., 'read,write')")
    expires_days: Optional[int] = Field(None, ge=1, le=365, description="Expiration in days (optional)")
    provider_override: Optional[str] = Field(None, max_length=100, description="Override AI provider for this key")
    model_override: Optional[str] = Field(None, max_length=200, description="Override AI model for this key")


class APIKeyResponse(BaseModel):
    """API key response model (without the actual key)."""
    id: str
    name: str
    description: Optional[str]
    scopes: str
    created_at: str
    expires_at: Optional[str]
    last_used_at: Optional[str]
    is_active: bool
    provider_override: Optional[str] = None
    model_override: Optional[str] = None


class APIKeyCreateResponse(BaseModel):
    """API key creation response with the plain key (shown only once)."""
    id: str
    key: str = Field(..., description="The API key - save this now, it won't be shown again!")
    name: str
    description: Optional[str]
    scopes: str
    created_at: str
    expires_at: Optional[str]
    provider_override: Optional[str] = None
    model_override: Optional[str] = None


class APIKeyUpdate(BaseModel):
    """API key update request model."""
    name: Optional[str] = Field(None, min_length=3, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


@router.post("", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    key_data: APIKeyCreate,
    current_user: dict = Depends(require_2fa),
    api_key_service: APIKeyService = Depends(get_api_key_service)
):
    """
    Generate a new API key for the authenticated user.
    
    **IMPORTANT**: The `key` field in the response is shown ONLY ONCE.
    You must save it immediately - it cannot be retrieved later!
    
    Args:
        key_data: API key creation parameters
        
    Returns:
        New API key details including the plain key value
        
    Raises:
        HTTPException: If creation fails
    """
    logger.info(f"API key creation request for user: {current_user['id']}")
    
    try:
        result = api_key_service.generate_api_key(
            user_id=current_user["id"],
            name=key_data.name,
            description=key_data.description,
            scopes=key_data.scopes,
            expires_days=key_data.expires_days,
            provider_override=key_data.provider_override,
            model_override=key_data.model_override,
        )
        
        logger.info(f"API key created successfully: {result['id']} for user {current_user['id']}")
        return APIKeyCreateResponse(**result)
        
    except ValidationError as e:
        logger.warning(f"API key creation validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except DatabaseError as e:
        logger.error(f"Database error creating API key: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service temporarily unavailable"
        )
    except Exception as e:
        logger.error(f"Unexpected error creating API key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.get("", response_model=List[APIKeyResponse])
async def list_api_keys(
    current_user: dict = Depends(require_2fa),
    api_key_service: APIKeyService = Depends(get_api_key_service)
):
    """
    List all API keys for the authenticated user.
    
    Returns metadata about each key, but NOT the actual key values
    (those are only shown once during creation).
    
    Returns:
        List of API key metadata
        
    Raises:
        HTTPException: If retrieval fails
    """
    logger.info(f"API key list request for user: {current_user['id']}")
    
    try:
        keys = api_key_service.get_user_api_keys(current_user["id"])
        return [APIKeyResponse(**key) for key in keys]
        
    except DatabaseError as e:
        logger.error(f"Database error retrieving API keys: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service temporarily unavailable"
        )
    except Exception as e:
        logger.error(f"Unexpected error retrieving API keys: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.patch("/{key_id}", response_model=APIKeyResponse)
async def update_api_key(
    key_id: str,
    update_data: APIKeyUpdate,
    current_user: dict = Depends(require_2fa),
    api_key_service: APIKeyService = Depends(get_api_key_service)
):
    """
    Update API key metadata.
    
    Args:
        key_id: ID of the API key to update
        update_data: Fields to update
        
    Returns:
        Updated API key metadata
        
    Raises:
        HTTPException: If update fails or key not found
    """
    logger.info(f"API key update request: {key_id} by user {current_user['id']}")
    
    try:
        success = api_key_service.update_api_key(
            key_id=key_id,
            user_id=current_user["id"],
            name=update_data.name,
            description=update_data.description
        )
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update API key"
            )
        
        # Return updated key data
        keys = api_key_service.get_user_api_keys(current_user["id"])
        updated_key = next((k for k in keys if k["id"] == key_id), None)
        
        if not updated_key:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="API key not found after update"
            )
        
        return APIKeyResponse(**updated_key)
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except HTTPException:
        raise
    except DatabaseError as e:
        logger.error(f"Database error updating API key: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service temporarily unavailable"
        )
    except Exception as e:
        logger.error(f"Unexpected error updating API key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/{key_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    current_user: dict = Depends(require_2fa),
    api_key_service: APIKeyService = Depends(get_api_key_service)
):
    """
    Revoke (deactivate) an API key.
    
    Revoked keys cannot be used for authentication but remain
    in the list for audit purposes. Use DELETE to permanently remove.
    
    Args:
        key_id: ID of the API key to revoke
        
    Raises:
        HTTPException: If revocation fails
    """
    logger.info(f"API key revoke request: {key_id} by user {current_user['id']}")
    
    try:
        success = api_key_service.revoke_api_key(key_id, current_user["id"])
        
        if success:
            logger.info(f"API key revoked: {key_id}")
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to revoke API key"
            )
            
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN if "authorized" in str(e) 
            else status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except HTTPException:
        raise
    except DatabaseError as e:
        logger.error(f"Database error revoking API key: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service temporarily unavailable"
        )
    except Exception as e:
        logger.error(f"Unexpected error revoking API key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: str,
    current_user: dict = Depends(require_2fa),
    api_key_service: APIKeyService = Depends(get_api_key_service)
):
    """
    Permanently delete an API key.
    
    This action cannot be undone. The key will be completely
    removed from the system.
    
    Args:
        key_id: ID of the API key to delete
        
    Raises:
        HTTPException: If deletion fails
    """
    logger.info(f"API key delete request: {key_id} by user {current_user['id']}")
    
    try:
        success = api_key_service.delete_api_key(key_id, current_user["id"])
        
        if success:
            logger.info(f"API key deleted: {key_id}")
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete API key"
            )
            
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN if "authorized" in str(e)
            else status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except HTTPException:
        raise
    except DatabaseError as e:
        logger.error(f"Database error deleting API key: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database service temporarily unavailable"
        )
    except Exception as e:
        logger.error(f"Unexpected error deleting API key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
