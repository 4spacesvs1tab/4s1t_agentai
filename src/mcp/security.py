"""
Security module for the MCP (Model Context Protocol) implementation.

This module provides authentication, authorization, and rate limiting
functionality for the MCP server using token-based access.
"""

import hashlib
import hmac
import time
from typing import Dict, Optional, Set
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from utils.logger import setup_logger
logger = setup_logger(__name__)


@dataclass
class ClientIdentity:
    """Represents an authenticated client identity."""
    
    client_id: str
    permissions: Set[str] = field(default_factory=set)
    created_at: datetime = field(default_factory=datetime.now)
    last_access: Optional[datetime] = None


class AuthenticationManager:
    """Manages client authentication for the MCP server using API tokens."""
    
    def __init__(self, valid_tokens: Optional[Dict[str, Set[str]]] = None):
        """
        Initialize the authentication manager.
        
        Args:
            valid_tokens: Pre-configured valid tokens with their permissions
                         Format: {"token": {"permission1", "permission2"}}
        """
        self.valid_tokens = valid_tokens or {}
        self.client_permissions: Dict[str, Set[str]] = {}  # client_id -> permissions
        self.logger = logger
    
    def add_valid_token(self, token: str, permissions: Set[str] = None) -> bool:
        """
        Add a valid token with associated permissions.
        
        Args:
            token: API token
            permissions: Set of permissions granted to this token
            
        Returns:
            bool: True if token was added successfully
        """
        try:
            self.valid_tokens[token] = permissions or set()
            self.logger.info("Added valid token")
            return True
        except Exception as e:
            self.logger.error(f"Failed to add valid token: {e}")
            return False
    
    def authenticate_client(self, client_id: str, token: str) -> Optional[ClientIdentity]:
        """
        Authenticate a client using their client ID and token.
        
        Args:
            client_id: Client identifier
            token: Client's API token
            
        Returns:
            ClientIdentity: Authenticated client identity or None if authentication failed
        """
        try:
            # Check if token is valid
            if token not in self.valid_tokens:
                self.logger.warning(f"Authentication attempt with invalid token for client: {client_id}")
                return None
            
            # Get permissions for this token
            permissions = self.valid_tokens[token]
            
            # Create client identity
            client_identity = ClientIdentity(
                client_id=client_id,
                permissions=permissions,
                last_access=datetime.now()
            )
            
            # Store client permissions for later lookup
            self.client_permissions[client_id] = permissions
            
            self.logger.debug(f"Authenticated client: {client_id}")
            return client_identity
                
        except Exception as e:
            self.logger.error(f"Error during authentication for client {client_id}: {e}")
            return None
    
    def has_permission(self, client_identity: ClientIdentity, permission: str) -> bool:
        """
        Check if a client has a specific permission.
        
        Args:
            client_identity: Authenticated client identity
            permission: Permission to check
            
        Returns:
            bool: True if client has the permission, False otherwise
        """
        return permission in client_identity.permissions
    
    def is_valid_token(self, token: str) -> bool:
        """
        Check if a token is valid.
        
        Args:
            token: Token to check
            
        Returns:
            bool: True if token is valid, False otherwise
        """
        return token in self.valid_tokens


class RateLimiter:
    """Implements rate limiting for MCP clients."""
    
    def __init__(self, requests_per_minute: int = 60):
        """
        Initialize the rate limiter.
        
        Args:
            requests_per_minute: Maximum requests allowed per minute per client
        """
        self.requests_per_minute = requests_per_minute
        self.client_requests: Dict[str, list] = {}  # client_id -> [timestamps]
        self.logger = logger
    
    def is_allowed(self, client_id: str) -> bool:
        """
        Check if a client is allowed to make a request based on rate limits.
        
        Args:
            client_id: Client identifier
            
        Returns:
            bool: True if request is allowed, False if rate limited
        """
        try:
            now = time.time()
            one_minute_ago = now - 60  # 60 seconds ago
            
            # Initialize client requests list if not exists
            if client_id not in self.client_requests:
                self.client_requests[client_id] = []
            
            # Remove old requests (older than 1 minute)
            self.client_requests[client_id] = [
                timestamp for timestamp in self.client_requests[client_id]
                if timestamp > one_minute_ago
            ]
            
            # Check if client is within rate limit
            current_requests = len(self.client_requests[client_id])
            if current_requests < self.requests_per_minute:
                # Add current request
                self.client_requests[client_id].append(now)
                return True
            else:
                self.logger.warning(f"Rate limit exceeded for client: {client_id}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error checking rate limit for client {client_id}: {e}")
            # Fail open - allow request in case of error
            return True
    
    def get_remaining_requests(self, client_id: str) -> int:
        """
        Get the number of remaining requests for a client in the current window.
        
        Args:
            client_id: Client identifier
            
        Returns:
            int: Number of remaining requests
        """
        try:
            now = time.time()
            one_minute_ago = now - 60
            
            # Initialize client requests list if not exists
            if client_id not in self.client_requests:
                self.client_requests[client_id] = []
            
            # Remove old requests
            self.client_requests[client_id] = [
                timestamp for timestamp in self.client_requests[client_id]
                if timestamp > one_minute_ago
            ]
            
            current_requests = len(self.client_requests[client_id])
            return max(0, self.requests_per_minute - current_requests)
            
        except Exception as e:
            self.logger.error(f"Error getting remaining requests for client {client_id}: {e}")
            return 0


# Example usage
if __name__ == "__main__":
    # Create authentication manager with pre-configured tokens
    auth_manager = AuthenticationManager({
        "admin-token-123": {"read", "write", "admin"},
        "service-token-456": {"read", "write"},
        "readonly-token-789": {"read"}
    })
    
    # Authenticate a client
    client = auth_manager.authenticate_client("test_client", "admin-token-123")
    if client:
        print(f"Authenticated client: {client.client_id}")
        print(f"Has 'read' permission: {auth_manager.has_permission(client, 'read')}")
        print(f"Has 'admin' permission: {auth_manager.has_permission(client, 'admin')}")
    
    # Create rate limiter
    rate_limiter = RateLimiter(requests_per_minute=5)
    
    # Test rate limiting
    for i in range(7):
        allowed = rate_limiter.is_allowed("test_client")
        remaining = rate_limiter.get_remaining_requests("test_client")
        print(f"Request {i+1}: Allowed={allowed}, Remaining={remaining}")
