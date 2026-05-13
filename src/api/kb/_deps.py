"""Shared dependencies for KB API sub-routers."""
import os

from api.security_dependencies import require_2fa
from application.account_service import AccountService
from application.alert_service import AlertService
from application.brief_service import BriefService
from application.discovery_service import DiscoveryService
from application.ingestion_service import IngestionService
from application.snapshot_service import SnapshotService
from core.db_path import get_db_path
from infrastructure.sqlite.sqlite_account_repository import SqliteAccountRepository
from infrastructure.sqlite.sqlite_alert_repository import SqliteAlertRepository
from infrastructure.sqlite.sqlite_discovery_repository import SqliteDiscoveryRepository

__all__ = [
    "require_2fa",
    "get_account_service",
    "get_alert_service",
    "get_brief_service",
    "get_discovery_service",
    "get_ingestion_service",
    "get_snapshot_service",
]


def get_account_service() -> AccountService:
    """FastAPI dependency — returns a per-request AccountService instance."""
    db_path = str(get_db_path())
    return AccountService(account_repo=SqliteAccountRepository(db_path))


def get_alert_service() -> AlertService:
    """FastAPI dependency — returns a per-request AlertService instance."""
    db_path = str(get_db_path())
    api_key = os.environ.get("NANO_GPT_API_KEY", "")
    return AlertService(
        alert_repo=SqliteAlertRepository(db_path),
        db_path=db_path,
        api_key=api_key,
    )


def get_brief_service() -> BriefService:
    """FastAPI dependency — returns a per-request BriefService instance."""
    return BriefService(db_path=str(get_db_path()))


def get_discovery_service() -> DiscoveryService:
    """FastAPI dependency — returns a per-request DiscoveryService instance."""
    db_path = str(get_db_path())
    return DiscoveryService(
        discovery_repo=SqliteDiscoveryRepository(db_path),
        account_repo=SqliteAccountRepository(db_path),
    )


def get_ingestion_service() -> IngestionService:
    """FastAPI dependency — returns a per-request IngestionService instance."""
    db_path = str(get_db_path())
    api_key = os.environ.get("NANO_GPT_API_KEY", "")
    return IngestionService(db_path=db_path, api_key=api_key)


def get_snapshot_service() -> SnapshotService:
    """FastAPI dependency — returns a per-request SnapshotService instance."""
    return SnapshotService(db_path=str(get_db_path()))
