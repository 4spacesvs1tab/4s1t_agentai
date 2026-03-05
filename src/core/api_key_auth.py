"""
API Key authentication middleware and utilities.
Integrates API key validation with FastAPI dependency system.
"""
from typing import Optional, Dict, Any
import logging

from fastapi import HTTPException, status, Depends
from fastapi.security import APIKeyHeader
from fastapi.requests import Request

from services.api_key_service import get_api_key_service, APIKeyService
from services.auth_service import get_auth_service, AuthService
from utils.logger import setup_logger

logger = setup_logger(__name__)

# API Key header scheme
api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    scheme_name="API-Key"
)


async def validate_api_key_header(
    api_key: Optional[str] = Depends(api_key_header),
    api_key_service: APIKeyService = Depends(get_api_key_service)
) -> Optional[Dict[str, Any]]:
    """
    Validate API key from header.
    
    Args:
        api_key: API key from X-API-Key header
        api_key_service: API key service instance
        
    Returns:
        User data if API key is valid, None if no key provided
        
    Raises:
        HTTPException: If API key is invalid or expired
    """
    if not api_key:
        return None
    
    try:
        key_data = api_key_service.validate_api_key(api_key)
        
        if not key_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired API key",
                headers={"WWW-Authenticate": "API-Key"}
            )
        
        return key_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating API key: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during API key validation"
        )


async def get_current_user_or_api_key(
    request: Request,
    api_key_data: Optional[Dict[str, Any]] = Depends(validate_api_key_header),
    auth_service: AuthService = Depends(get_auth_service)
) -> Dict[str, Any]:
    """
    Get current user from either JWT token OR API key.
    This allows endpoints to accept both authentication methods.
    
    Priority:
    1. API Key (if X-API-Key header present)
    2. JWT Bearer token (from Authorization header)
    
    Args:
        request: FastAPI request object
        api_key_data: API key validation result from dependency
        auth_service: Auth service instance
        
    Returns:
        User data with authentication method indicator
        
    Raises:
        HTTPException: If neither authentication method succeeds
    """
    # If API key validation succeeded, use that
    if api_key_data:
        return {
            "id": api_key_data["user_id"],
            "role": api_key_data["role"],
            "is_active": True,  # API keys are only valid for active users
            "auth_method": "api_key",
            "key_id": api_key_data["key_id"],
            "scopes": api_key_data["scopes"]
        }
    
    # Otherwise, try JWT token
    auth_header = request.headers.get("Authorization", "")
    
    if auth_header.startswith("Bearer "):
        try:
            from api.auth_routes import get_current_user
            jwt_user = await get_current_user(auth_header.replace("Bearer ", ""), auth_service)
            return {
                **jwt_user,
                "auth_method": "jwt"
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error validating JWT token: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer, API-Key"}
            )
    
    # No valid authentication found
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing valid authentication. Provide either: Bearer token (Authorization header) or API Key (X-API-Key header)",
        headers={"WWW-Authenticate": "Bearer, API-Key"}
    )


def require_scopes(required_scopes: list):
    """
    Dependency factory to require specific scopes for an endpoint.
    
    Usage:
        @router.get("/protected")
        async def protected_endpoint(
            user: dict = Depends(require_scopes(["read", "write"]))
        ):
            ...
    
    Args:
        required_scopes: List of required scope strings
        
    Returns:
        Dependency function
    """
    async def check_scopes(
        user: Dict[str, Any] = Depends(get_current_user_or_api_key)
    ) -> Dict[str, Any]:
        # JWT authentication doesn't have scopes restriction (assumes full access)
        if user.get("auth_method") == "jwt":
            return user
        
        # API key authentication has scope restrictions
        user_scopes = user.get("scopes", "").split(",")
        user_scopes = [s.strip() for s in user_scopes]
        
        # Check if user has all required scopes
        missing_scopes = [s for s in required_scopes if s not in user_scopes]
        
        if missing_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient scopes. Missing: {', '.join(missing_scopes)}"
            )
        
        return user
    
    return check_scopes
