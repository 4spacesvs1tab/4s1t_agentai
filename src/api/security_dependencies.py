"""
Security Dependencies for FastAPI
Phase 2 Security Hardening - Mandatory 2FA Enforcement
KB-26-F: persistent JWT JTI revocation via SQLite (replaces in-memory dict).
"""

from fastapi import Request, HTTPException, Depends, status
import jwt
from jwt.exceptions import PyJWTError
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import os
import sqlite3
from pathlib import Path

from core.db_path import get_db_path

# Use absolute imports
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config.settings import settings
from services.exceptions import AuthenticationError

from utils.logger import setup_logger
logger = setup_logger(__name__)

# Simple in-memory session tracking for 2FA (use Redis in production)
_pending_2fa_sessions: Dict[str, Dict[str, Any]] = {}

# ── KB-26-F: SQLite-backed JTI revocation ────────────────────────────────────
# In-memory cache for the hot path (avoids a DB lookup on every request).
# The DB is the authoritative store — cache is rebuilt from DB after restart.
_revoked_jtis_cache: Dict[str, float] = {}





def _jti_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def revoke_token(jti: str, exp: float) -> None:
    """Add a JTI to the revocation store (memory + SQLite).

    KB-26-F: persists across service restarts, closing the gap where old tokens
    were briefly re-accepted after a restart (up to their 60-min expiry).
    """
    import time
    now = time.time()

    # Prune expired entries from memory cache
    expired = [k for k, v in _revoked_jtis_cache.items() if v < now]
    for k in expired:
        del _revoked_jtis_cache[k]
    _revoked_jtis_cache[jti] = exp

    # Persist to SQLite
    from datetime import datetime as _dt
    exp_iso = _dt.fromtimestamp(exp, tz=timezone.utc).isoformat()
    try:
        with _jti_db_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO revoked_tokens (jti, expires_at) VALUES (?, ?)",
                (jti, exp_iso),
            )
            conn.commit()
    except Exception as exc:
        # Non-fatal: memory cache still prevents replay within this process lifetime
        logger.warning("JTI revocation DB write failed (memory cache still active): %s", exc)


def _is_jti_revoked(jti: str) -> bool:
    """Check if a JTI is revoked — memory first, then DB fallback."""
    if jti in _revoked_jtis_cache:
        return True
    # Not in cache: check DB (covers tokens revoked before this process started)
    try:
        with _jti_db_conn() as conn:
            row = conn.execute(
                "SELECT jti FROM revoked_tokens "
                "WHERE jti = ? AND expires_at > datetime('now')",
                (jti,),
            ).fetchone()
            if row:
                # Warm the cache so subsequent requests skip the DB lookup
                import time
                _revoked_jtis_cache[jti] = time.time() + 3600  # approximate TTL
                return True
    except Exception as exc:
        logger.warning("JTI revocation DB read failed: %s", exc)
    return False


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

        # Check JTI revocation (KB-26-F: memory + SQLite-backed)
        jti = payload.get("jti")
        if jti and _is_jti_revoked(jti):
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

    except PyJWTError:
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
