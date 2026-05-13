"""
Route registration for the 4S1T Agent AI application.

Extracted from src/main.py (B4 refactor).
"""
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.auth_routes import router as auth_router
from api.web import router as web_router
from api.mobile_routes import router as mobile_router
from api.agent_routes import router as agent_router
from api.api_key_routes import router as api_key_router
from api.mfa_routes import router as mfa_router
from api.preference_routes import router as preference_router
from api.kb import router as kb_router
from api.conversation_routes import router as conversation_router
from api.document_routes import router as document_router


def register_routes(app: FastAPI) -> None:
    """Mount static files, register all routers, and add utility endpoints."""
    static_dir = os.path.join(os.path.dirname(__file__), "..", "web", "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(auth_router)
    app.include_router(web_router)
    app.include_router(mobile_router)
    app.include_router(agent_router)
    app.include_router(api_key_router)
    app.include_router(mfa_router)
    app.include_router(preference_router)
    app.include_router(kb_router)
    app.include_router(conversation_router)
    app.include_router(document_router)

    @app.get("/")
    async def root():
        """Root endpoint returning system information."""
        return {
            "message": "4S1T Agent AI System",
            "version": "0.1.0",
            "status": "operational"
        }

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "components": {
                "api": "operational"
            }
        }
