"""
Chat and conversation UI pages.

Routes:
  GET /dashboard/chat  → chat_page (requires completed 2FA session)
  GET /chat            → chat_terminal_page (clean URL, requires 2FA)
  GET /conversations   → conversations_page
"""
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.security_dependencies import require_2fa, optional_auth
from api.web._templates import templates, _tctx, get_user_from_request
from config.kb_config import get_domains_for_ui
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(tags=["web-chat"])


@router.get("/dashboard/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(optional_auth)
):
    """
    Modern chat interface page - REQUIRES COMPLETED 2FA SESSION.

    P1 Security Fix: Upgraded from require_auth to require_2fa so a bare
    access token (mfa_verified absent) cannot reach this page.
    Unauthenticated users are redirected to login; users who haven't
    completed TOTP are redirected to the enrollment/verification flow.
    """
    if not current_user:
        return RedirectResponse(url="/login?redirect=/dashboard/chat")

    try:
        current_user = await require_2fa(current_user)
    except HTTPException as e:
        if e.status_code == 403:
            return RedirectResponse(url="/auth/mfa/verify", status_code=303)
        raise

    db_user = await get_user_from_request(request)
    u = db_user or current_user
    return templates.TemplateResponse("chat.html", _tctx(u, request))


@router.get("/chat", response_class=HTMLResponse)
async def chat_terminal_page(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(optional_auth)
):
    """
    Chat interface at clean URL - REDIRECTS TO LOGIN if not authenticated.

    Extended Security Fix: Check authentication first, then 2FA enrollment status.
    Redirects to appropriate page for complete security flow.
    """
    if not current_user:
        return RedirectResponse(url="/login?redirect=/chat")

    try:
        current_user = await require_2fa(current_user)
    except HTTPException as e:
        if e.status_code == 403:
            return RedirectResponse(url="/auth/2fa/enroll", status_code=303)
        raise

    db_user = await get_user_from_request(request)
    u = db_user or current_user
    return templates.TemplateResponse("chat.html", _tctx(u, request))


@router.get("/conversations", response_class=HTMLResponse)
async def conversations_page(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(optional_auth)
):
    """Conversation history graph page."""
    if not current_user:
        return RedirectResponse(url="/login?redirect=/conversations")
    db_user = await get_user_from_request(request)
    u = db_user or current_user
    return templates.TemplateResponse("conversations.html", _tctx(u, request, kb_domains=get_domains_for_ui()))
