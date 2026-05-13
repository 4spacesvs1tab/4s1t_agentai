"""
Shared Jinja2 template helpers and context builders for the web UI package.

All route modules in src/api/web/ import from here — no route file
imports from another route file.
"""
import os
from typing import Dict, Any, Optional

import jwt
from jwt.exceptions import PyJWTError
from fastapi import Request
from fastapi.templating import Jinja2Templates

from config.settings import settings
from i18n import get_t, LANGUAGES
from services.auth_service import get_auth_service
from utils.logger import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Jinja2 template engine
# ---------------------------------------------------------------------------

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "web", "templates")
)


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _lang(user: Optional[Dict[str, Any]]) -> str:
    """Return the user's language preference, defaulting to 'en'."""
    return (user or {}).get("language_preference", "en") or "en"


def _tctx(user: Optional[Dict[str, Any]], request: Request, **extra) -> Dict[str, Any]:
    """
    Build a Jinja2 template context that includes i18n helpers.

    Every template rendered via this helper automatically receives:
      - ``t``    : translation function for the user's language
      - ``lang`` : current language code string (e.g. 'en', 'pl')
      - ``languages`` : dict of {code: display_name} for the language picker
    """
    lang = _lang(user)
    return {
        "request": request,
        "user": user,
        "lang": lang,
        "t": get_t(lang),
        "languages": LANGUAGES,
        **extra,
    }


async def get_user_from_request(request: Request) -> Optional[Dict[str, Any]]:
    """Extract and verify user from request token for template rendering."""
    token = None

    # Check Authorization header
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # Fallback to cookie
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        return None

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            return None

        auth_service = get_auth_service()
        return auth_service.get_user_by_id(user_id)

    except (PyJWTError, ValueError, AttributeError):
        return None
    except Exception as e:
        logger.error("Unexpected error in get_user_from_request: %s", e)
        return None

