"""
IdentityPort — cross-context port for authentication, authorisation, and user lookup.

All bounded contexts that need to verify identity or check permissions must
import and call this port.  Direct imports of AuthService, MFAService, or
RBACService from outside the identity_security context are a DDD violation.

Rule: this file must never import httpx, sqlite3, os.environ, or any I/O
library.  Only standard-library ABCs are allowed here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class IdentityPort(ABC):
    """Abstract interface for authentication, RBAC, and user resolution.

    Implementations live in src/services/ (e.g. IdentityServiceAdapter
    wrapping AuthService + RBACService).
    Wire a concrete adapter at the composition root.

    Method selection rationale:
      verify_token  — called by route middleware to validate JWT access tokens.
      get_user      — called by application services that need user metadata
                      beyond what the JWT payload carries.
      has_permission — called by route handlers and application services for
                      fine-grained RBAC checks.
    """

    @abstractmethod
    def verify_token(self, token: str) -> Optional[dict]:
        """Validate *token* and return the decoded user claims dict, or None.

        The returned dict contains at minimum: {"id": str, "username": str}.
        Returns None if the token is missing, expired, revoked, or malformed.
        """
        ...

    @abstractmethod
    def get_user(self, user_id: str) -> Optional[dict]:
        """Return the user record for *user_id*, or None if not found.

        The returned dict mirrors the users table row.
        """
        ...

    @abstractmethod
    def has_permission(self, user_id: str, permission: str) -> bool:
        """Return True if *user_id* holds *permission* under any of their roles.

        *permission* must be a value from the Permission enum (passed as str).
        Returns False if the user or permission does not exist.
        """
        ...
