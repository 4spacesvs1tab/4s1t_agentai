"""
Dashboard, profile, and user settings pages.

Routes:
  GET  /dashboard              → dashboard_page
  GET  /dashboard/profile      → profile_page (with MFA status)
  GET  /dashboard/health       → health_dashboard_page
  GET  /dashboard/mcp          → mcp_tools_page
  GET  /profile                → profile_terminal_page
  POST /profile/language       → update_language_preference
  POST /profile/pii-scrubbing  → update_pii_scrubbing_preference
  POST /profile/theme          → update_theme_preference
  GET  /mcp                    → mcp_terminal_page
  GET  /api-keys               → api_keys_terminal_page
  GET  /api/models             → models_api_page
"""
import os
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.security_dependencies import optional_auth
from api.web._templates import templates, _tctx, get_user_from_request
from config.kb_config import get_domains_for_ui
from i18n import LANGUAGES
from services.auth_service import get_auth_service
from services.mfa.service import get_mfa_service
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(tags=["web-settings"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(optional_auth)
):
    """User dashboard page - requires authentication."""
    if not current_user:
        return RedirectResponse(url="/login")
    db_user = await get_user_from_request(request)
    try:
        from config.provider_config import get_active_provider
        api_provider = get_active_provider().display_name
    except Exception:
        api_provider = "Nano-GPT"
    u = db_user or current_user
    tor_proxy = "{}:{}".format(
        os.environ.get("TOR_PROXY_HOST", "localhost"),
        os.environ.get("TOR_PROXY_PORT", "9050"),
    )
    return templates.TemplateResponse("dashboard.html", _tctx(u, request, api_provider=api_provider, tor_proxy=tor_proxy, kb_domains=get_domains_for_ui()))


@router.get("/dashboard/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    """User profile page."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")

    # Check MFA status
    mfa_service = get_mfa_service()
    user_id = user["id"]
    mfa_enabled = mfa_service.is_totp_enabled(user_id)

    backup_codes_remaining = 0
    if mfa_enabled:
        codes = mfa_service.get_backup_codes(user_id)
        backup_codes_remaining = len([c for c in codes if not c["used"]])

    return templates.TemplateResponse(
        "profile.html",
        _tctx(user, request, mfa_enabled=mfa_enabled, backup_codes_remaining=backup_codes_remaining)
    )


@router.get("/dashboard/health", response_class=HTMLResponse)
async def health_dashboard_page(request: Request):
    """System health dashboard page - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("health.html", _tctx(user, request))


@router.get("/dashboard/mcp", response_class=HTMLResponse)
async def mcp_tools_page(request: Request):
    """MCP tools page - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("mcp_tools.html", _tctx(user, request))


@router.get("/profile", response_class=HTMLResponse)
async def profile_terminal_page(request: Request):
    """User profile page at clean URL - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("profile.html", _tctx(user, request))


@router.post("/profile/language", response_class=RedirectResponse)
async def update_language_preference(request: Request):
    """
    Handle language preference form submission from profile page.
    Updates user's language preference in database and redirects back to profile.
    """
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")

    form = await request.form()
    lang = form.get("language_preference")

    if lang not in LANGUAGES:
        logger.warning(f"Invalid language preference from user {user['id']}: {lang}")
        return RedirectResponse(url="/profile", status_code=303)

    auth_service = get_auth_service()
    try:
        auth_service.update_user_language(user["id"], lang)
        logger.info(f"Updated language preference for user {user['id']}: {lang}")
    except Exception as e:
        logger.error(f"Failed to update language preference: {str(e)}", exc_info=True)

    return RedirectResponse(url="/profile", status_code=303)


@router.post("/profile/pii-scrubbing", response_class=RedirectResponse)
async def update_pii_scrubbing_preference(request: Request):
    """
    Handle PII scrubbing toggle form submission from profile page.
    Updates user's pii_scrubbing_enabled flag and redirects back to profile.
    """
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")

    form = await request.form()
    enabled = form.get("pii_scrubbing_enabled") == "on"

    auth_service = get_auth_service()
    try:
        auth_service.update_user_pii_scrubbing(user["id"], enabled)
        logger.info(f"Updated PII scrubbing for user {user['id']}: {enabled}")
    except Exception as e:
        logger.error(f"Failed to update PII scrubbing preference: {str(e)}", exc_info=True)

    return RedirectResponse(url="/profile", status_code=303)


@router.post("/profile/theme", response_class=RedirectResponse)
async def update_theme_preference(request: Request):
    """
    Handle theme preference form submission from profile page.
    Updates user's theme preference in database and redirects back to profile.
    """
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")

    form = await request.form()
    theme_preference = form.get("theme_preference")

    # Validate theme preference
    valid_themes = ["terminal", "dark_grey_technical", "teal_modern", "blue_professional"]
    if not theme_preference or theme_preference not in valid_themes:
        logger.warning(f"Invalid theme preference from user {user['id']}: {theme_preference}")
        return RedirectResponse(url="/profile")

    # Update theme preference in database
    auth_service = get_auth_service()
    try:
        auth_service.update_user_theme(user['id'], theme_preference)
        logger.info(f"Updated theme preference for user {user['id']}: {theme_preference}")
    except Exception as e:
        logger.error(f"Failed to update theme preference: {str(e)}", exc_info=True)

    return RedirectResponse(url="/profile", status_code=303)


@router.get("/mcp", response_class=HTMLResponse)
async def mcp_terminal_page(request: Request):
    """MCP tools page at clean URL - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("mcp_tools.html", _tctx(user, request))


@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_terminal_page(request: Request):
    """API Keys management page at clean URL - requires authentication."""
    user = await get_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("api_keys.html", _tctx(user, request))


@router.get("/api/models", response_class=HTMLResponse)
async def models_api_page(request: Request):
    """Models API page for testing."""
    return templates.TemplateResponse("models_api.html", {"request": request})
