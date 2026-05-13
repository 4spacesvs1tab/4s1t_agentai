"""
P3-5: CSRF token generation and validation.

Implements the Synchronizer Token Pattern using itsdangerous
URLSafeTimedSerializer (HMAC-SHA1 + timestamp).

Usage
-----
Server side (GET handler):
    from core.csrf import generate_csrf_token
    token = generate_csrf_token()
    return templates.TemplateResponse("login.html", {"request": request, "csrf_token": token})

Server side (POST validation, as a FastAPI dependency):
    from core.csrf import require_csrf_token
    @router.post("/login")
    async def login(..., _csrf=Depends(require_csrf_token)):
        ...

Client side (in fetch):
    headers: { 'X-CSRF-Token': document.getElementById('csrf_token').value }
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from utils.logger import setup_logger

logger = setup_logger(__name__)

_CSRF_MAX_AGE = 3600        # 1 hour — matches session window
_CSRF_SALT = "4s1t-csrf-double-submit"


def _get_serializer() -> URLSafeTimedSerializer:
    # Import lazily to avoid circular import at module level
    from config.settings import settings  # noqa: PLC0415
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_CSRF_SALT)


def generate_csrf_token(payload: str = "form") -> str:
    """
    Generate a signed, time-limited CSRF token.

    Args:
        payload: An arbitrary string bound into the token (e.g. form name).
                 Not secret — the signature is what matters.

    Returns:
        URL-safe signed token string.
    """
    return _get_serializer().dumps(payload)


def validate_csrf_token(token: str) -> bool:
    """
    Validate a CSRF token.

    Returns:
        True if the token signature is valid and not expired, False otherwise.
    """
    try:
        _get_serializer().loads(token, max_age=_CSRF_MAX_AGE)
        return True
    except SignatureExpired:
        logger.warning("[csrf] Token rejected: expired")
        return False
    except BadSignature:
        logger.warning("[csrf] Token rejected: bad signature")
        return False
    except Exception as exc:
        logger.warning(f"[csrf] Token validation error: {exc}")
        return False


async def require_csrf_token(request: Request) -> None:
    """
    FastAPI dependency — validates the X-CSRF-Token request header.

    Raises:
        HTTPException 403 if the header is missing or the token is invalid.
    """
    token: Optional[str] = request.headers.get("X-CSRF-Token")
    if not token:
        logger.warning(f"[csrf] Missing X-CSRF-Token header on {request.method} {request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing",
        )
    if not validate_csrf_token(token):
        logger.warning(f"[csrf] Invalid CSRF token on {request.method} {request.url.path}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token invalid or expired",
        )
