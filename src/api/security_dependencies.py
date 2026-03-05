"""
Security Dependencies for FastAPI
Phase 2 Security Hardening - Mandatory 2FA Enforcement
"""

from fastapi import Request, HTTPException, Depends, status
from jose import JWTError, jwt
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import logging

# Use absolute imports
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config.settings import settings
from services.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

# Simple in-memory session tracking for 2FA (use Redis in production)
_pending_2fa_sessions: Dict[str, Dict[str, Any]] = {}

# In-memory JTI revocation blocklist — revoked on logout
# Maps jti -> expiry timestamp so we can prune expired entries
_revoked_jtis: Dict[str, float] = {}


def revoke_token(jti: str, exp: float) -> None:
    """Add a JTI to the revocation blocklist until it expires."""
    import time
    # Prune already-expired entries to keep memory bounded
    now = time.time()
    expired = [k for k, v in _revoked_jtis.items() if v < now]
    for k in expired:
        del _revoked_jtis[k]
    _revoked_jtis[jti] = exp


async def require_auth(request: Request) -> Dict[str, Any]:
    """
    Dependency to enforce authentication on endpoints.
    Extracts and validates JWT token from Authorization header or cookies.

    Returns:
        User data dict including mfa_verified claim from the token.

    Raises:
        HTTPException: 401 if not authenticated
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Try to get token from Authorization header
    auth_header = request.headers.get("Authorization")
    token = None

    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]  # Remove "Bearer " prefix

    # Fallback to cookie
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        # Note: the claim is stored as "token_type" in create_access_token; legacy
        # tokens may use "type" — check both to avoid breaking existing sessions.
        token_type: str = payload.get("token_type") or payload.get("type", "access")

        if user_id is None:
            raise credentials_exception

        # Check JTI revocation blocklist (set on logout)
        jti = payload.get("jti")
        if jti and jti in _revoked_jtis:
            raise credentials_exception

        return {
            "id": user_id,
            "token_type": token_type,
            "exp": payload.get("exp"),
            "jti": jti,
            # mfa_verified is set to True only by the verify-2fa and token-exchange
            # endpoints, meaning the user completed TOTP in the current session.
            "mfa_verified": bool(payload.get("mfa_verified", False)),
        }

    except JWTError:
        raise credentials_exception


async def require_2fa(current_user: Dict[str, Any] = Depends(require_auth)) -> Dict[str, Any]:
    """
    Dependency to enforce 2FA completion.

    Guards are met when the JWT contains mfa_verified=True, which is only
    stamped by the /auth/verify-2fa and /auth/token-exchange endpoints after
    a successful TOTP check in the current login session.

    Enrollment tokens (mfa_enrollment type) are allowed through so the
    enrollment flow itself can complete.

    Raises:
        HTTPException: 403 if the session has not been TOTP-verified
    """
    token_type = current_user.get("token_type", "access")

    # Let the enrollment flow through so users can set up TOTP
    if token_type == "mfa_enrollment":
        return current_user

    # Reject any token that was not produced after a successful TOTP check.
    # This covers the case where a user has enrolled but has not completed
    # TOTP verification for THIS login session (e.g., a stale token from
    # before enforcement was added).
    if not current_user.get("mfa_verified", False):
        user_id = current_user.get("id", "unknown")
        logger.warning(
            f"User {user_id} attempted to access 2FA-protected endpoint "
            "without a TOTP-verified token"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Two-Factor Authentication verification required. "
                "Please complete login at /auth/verify-2fa"
            ),
            headers={"X-Requires-MFA": "true"},
        )

    return current_user


async def optional_auth(request: Request) -> Optional[Dict[str, Any]]:
    """
    Dependency for optionally authenticated endpoints.
    Returns user data if authenticated, None otherwise (no exception).
    """
    try:
        return await require_auth(request)
    except HTTPException:
        return None
