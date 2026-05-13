"""
IdentityPort adapter.

Implements IdentityPort by delegating to AuthService (verify_token, get_user)
and RBACService (has_permission).  Deferred imports avoid circular dependencies
at module load time — both services have their own singleton factories.
"""
from __future__ import annotations

from typing import Optional

import jwt
from jwt.exceptions import PyJWTError

from core.ports.identity_port import IdentityPort
from utils.logger import setup_logger

logger = setup_logger(__name__)


class IdentityServiceAdapter(IdentityPort):
    """Adapts AuthService + RBACService to the IdentityPort interface."""

    def verify_token(self, token: str) -> Optional[dict]:
        try:
            from config.settings import settings
            from api.security_dependencies import _is_jti_revoked
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            user_id: str = payload.get("sub")
            if not user_id:
                return None
            jti = payload.get("jti")
            if jti and _is_jti_revoked(jti):
                return None
            return {
                "id": user_id,
                "token_type": payload.get("token_type") or payload.get("type", "access"),
                "exp": payload.get("exp"),
                "jti": jti,
                "mfa_verified": bool(payload.get("mfa_verified", False)),
            }
        except PyJWTError:
            return None
        except Exception as exc:
            logger.warning("verify_token failed: %s", exc)
            return None

    def get_user(self, user_id: str) -> Optional[dict]:
        try:
            from services.auth_service import get_auth_service
            return get_auth_service().get_user_by_id(user_id)
        except Exception as exc:
            logger.warning("get_user failed user_id=%s: %s", user_id, exc)
            return None

    def has_permission(self, user_id: str, permission: str) -> bool:
        try:
            from services.rbac.permissions import Permission, get_rbac_service
            perm = next((p for p in Permission if p.value == permission), None)
            if perm is None:
                logger.warning("Unknown permission string: %s", permission)
                return False
            return get_rbac_service().check_permission(user_id, perm)
        except Exception as exc:
            logger.warning("has_permission failed user_id=%s perm=%s: %s", user_id, permission, exc)
            return False


_adapter: Optional[IdentityServiceAdapter] = None


def get_identity_port() -> IdentityServiceAdapter:
    """Singleton factory — returns a shared IdentityServiceAdapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = IdentityServiceAdapter()
    return _adapter
