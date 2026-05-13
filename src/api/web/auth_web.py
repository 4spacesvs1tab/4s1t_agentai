"""
Authentication and MFA web pages.

Routes:
  GET  /             → home_page (redirect to login template)
  GET  /login        → login_page
  GET  /register     → register_page
  GET  /logout       → logout_page (revokes JTI, clears cookie)
  GET  /auth/mfa/verify  → mfa_verify_page
  GET  /auth/2fa/enroll  → mfa_enroll_page
  POST /auth/mfa/verify  → verify_mfa
  GET  /users        → admin_users_page (admin only)
"""
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.security_dependencies import revoke_token
from api.web._templates import (
    templates,
    _tctx,
    get_user_from_request,
)
from core.security import decode_access_token
from core.csrf import generate_csrf_token
from services.auth_service import get_auth_service
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(tags=["web-auth"])


@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    """Home page with login option."""
    user = await get_user_from_request(request)
    return templates.TemplateResponse("login.html", _tctx(
        user, request, csrf_token=generate_csrf_token("login")
    ))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    user = await get_user_from_request(request)
    return templates.TemplateResponse("login.html", _tctx(
        user, request, csrf_token=generate_csrf_token("login")
    ))


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page."""
    user = await get_user_from_request(request)
    return templates.TemplateResponse("register.html", _tctx(
        user, request, csrf_token=generate_csrf_token("register")
    ))


@router.get("/logout", response_class=RedirectResponse)
async def logout_page(request: Request):
    """Logout endpoint - revokes JTI, clears cookie and redirects to login."""
    token = request.cookies.get("access_token") or (
        request.headers.get("Authorization", "")[7:]
        if request.headers.get("Authorization", "").startswith("Bearer ")
        else None
    )
    if token:
        payload = decode_access_token(token)
        if payload:
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                revoke_token(jti, float(exp))
    response = RedirectResponse(url="/login")
    response.delete_cookie("access_token", path="/")
    return response


@router.get("/auth/mfa/verify", response_class=HTMLResponse)
async def mfa_verify_page(request: Request):
    """MFA verification page."""
    user = await get_user_from_request(request)
    return templates.TemplateResponse("mfa_verify.html", _tctx(user, request))


@router.get("/auth/2fa/enroll", response_class=HTMLResponse)
async def mfa_enroll_page(request: Request):
    """2FA enrollment page for setting up TOTP."""
    return templates.TemplateResponse("mfa_enroll.html", {"request": request})


@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    """Admin user management panel — requires admin role."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    auth_service = get_auth_service()
    users = auth_service.get_all_users()
    return templates.TemplateResponse("users.html", _tctx(user, request, users=users))


@router.post("/auth/mfa/verify")
async def verify_mfa(request: Request):
    """
    Verify MFA code and complete authentication.
    This endpoint receives form data from the MFA verification page.
    """
    import jwt
    from datetime import timedelta
    from jwt.exceptions import PyJWTError
    from fastapi.responses import JSONResponse
    from config.settings import settings
    from services.auth_service import get_auth_service
    from services.exceptions import AuthenticationError
    from services.mfa.service import MFAService
    import logging

    logger = logging.getLogger(__name__)

    form = await request.form()
    code = form.get("code")
    mfa_token = form.get("token")

    if not code or not mfa_token:
        return JSONResponse(
            status_code=400,
            content={"error": "Code and MFA token are required"}
        )

    # Verify MFA token
    try:
        # Decode the mfa_token to get the user_id
        mfa_payload = jwt.decode(mfa_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = mfa_payload.get("sub")
        token_type = mfa_payload.get("token_type")

        if not user_id or token_type != "mfa":
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid MFA token"}
            )

        # Get auth service and MFA service
        auth_service = get_auth_service()
        mfa_service = MFAService(auth_service.db)

        # Verify the MFA code
        if not mfa_service.verify_mfa_code(user_id, code):
            logger.warning(f"MFA verification failed for user: {user_id}")
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid MFA code"}
            )

        # Generate a proper access token
        access_token = auth_service.create_access_token(user_id)

        # Update last login
        auth_service.update_last_login(user_id)

        logger.info(f"MFA verification successful for user: {user_id}")

        return JSONResponse(
            status_code=200,
            content={
                "access_token": access_token,
                "token_type": "bearer",
                "message": "MFA verification successful"
            }
        )

    except PyJWTError:
        logger.error("Invalid MFA token - JWT decode failed")
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid MFA token"}
        )
    except Exception as e:
        logger.error(f"MFA verification error: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error during MFA verification"}
        )
