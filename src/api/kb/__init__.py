"""
Knowledge Base API package — assembles all KB sub-routers.

Exposes a single `router` with prefix /api/v1/kb that is registered
in src/app/routes.py exactly as the old api.kb_routes router was.
"""
from fastapi import APIRouter

from api.kb.discovery_routes import router as discovery_router
from api.kb.account_routes import router as account_router
from api.kb.alert_routes import router as alert_router
from api.kb.snapshot_routes import router as snapshot_router
from api.kb.ingest_routes import router as ingest_router
from api.kb.graph_routes import router as graph_router

router = APIRouter(prefix="/api/v1/kb", tags=["knowledge-base"])

router.include_router(discovery_router)
router.include_router(account_router)
router.include_router(alert_router)
router.include_router(snapshot_router)
router.include_router(ingest_router)
router.include_router(graph_router)
