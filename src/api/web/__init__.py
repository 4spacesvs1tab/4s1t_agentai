"""
Web UI package — assembles all web sub-routers into a single APIRouter.

Exposes a single `router` that is registered in src/app/routes.py exactly
as the old api.web_routes router was.
"""
from fastapi import APIRouter
from fastapi.staticfiles import StaticFiles

from api.web.auth_web import router as auth_router
from api.web.chat_web import router as chat_router
from api.web.settings_web import router as settings_router
from api.web.kb_web import router as kb_router

router = APIRouter(prefix="", tags=["web"])

router.include_router(auth_router)
router.include_router(chat_router)
router.include_router(settings_router)
router.include_router(kb_router)

# Static files previously mounted by web_routes.py
router.mount("/web/static", StaticFiles(directory="web/static"), name="web_static")
