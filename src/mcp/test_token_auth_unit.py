"""
Tests for token-based authentication functionality.
"""

import unittest
import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mcp.security import AuthenticationManager, ClientIdentity


class TestTokenAuthentication(unittest.TestCase):
    """Test cases for token-based authentication."""

    def setUp(self):
        """Set up test authentication manager."""
        self.auth_manager = AuthenticationManager({
            "admin-token-123": {"read", "write", "admin"},
            "service-token-456": {"read", "write"},
            "readonly-token-789": {"read"}
        })

    def test_valid_token_authentication(self):
        """Test authentication with valid token."""
        client_identity = self.auth_manager.authenticate_client("test_client", "admin-token-123")
        
        self.assertIsNotNone(client_identity)
        self.assertIsInstance(client_identity, ClientIdentity)
        self.assertEqual(client_identity.client_id, "test_client")
        self.assertIn("admin", client_identity.permissions)
        self.assertIn("read", client_identity.permissions)
        self.assertIn("write", client_identity.permissions)

    def test_invalid_token_authentication(self):
        """Test authentication with invalid token."""
        client_identity = self.auth_manager.authenticate_client("test_client", "invalid-token")
        self.assertIsNone(client_identity)

    def test_permission_checking(self):
        """Test permission checking for authenticated clients."""
        # Test admin token
        admin_client = self.auth_manager.authenticate_client("admin_client", "admin-token-123")
        self.assertIsNotNone(admin_client)
        self.assertTrue(self.auth_manager.has_permission(admin_client, "admin"))
        self.assertTrue(self.auth_manager.has_permission(admin_client, "read"))
        self.assertTrue(self.auth_manager.has_permission(admin_client, "write"))
        
        # Test service token
        service_client = self.auth_manager.authenticate_client("service_client", "service-token-456")
        self.assertIsNotNone(service_client)
        self.assertFalse(self.auth_manager.has_permission(service_client, "admin"))
        self.assertTrue(self.auth_manager.has_permission(service_client, "read"))
        self.assertTrue(self.auth_manager.has_permission(service_client, "write"))
        
        # Test readonly token
        readonly_client = self.auth_manager.authenticate_client("readonly_client", "readonly-token-789")
        self.assertIsNotNone(readonly_client)
        self.assertFalse(self.auth_manager.has_permission(readonly_client, "admin"))
        self.assertFalse(self.auth_manager.has_permission(readonly_client, "write"))
        self.assertTrue(self.auth_manager.has_permission(readonly_client, "read"))

    def test_adding_valid_tokens(self):
        """Test adding new valid tokens."""
        result = self.auth_manager.add_valid_token("new-token", {"special-permission"})
        self.assertTrue(result)
        
        # Test authentication with new token
        client_identity = self.auth_manager.authenticate_client("new_client", "new-token")
        self.assertIsNotNone(client_identity)
        self.assertIn("special-permission", client_identity.permissions)

    def test_token_validation(self):
        """Test token validation."""
        self.assertTrue(self.auth_manager.is_valid_token("admin-token-123"))
        self.assertTrue(self.auth_manager.is_valid_token("service-token-456"))
        self.assertTrue(self.auth_manager.is_valid_token("readonly-token-789"))
        self.assertFalse(self.auth_manager.is_valid_token("invalid-token"))

    def test_empty_permissions(self):
        """Test token with empty permissions."""
        self.auth_manager.add_valid_token("no-perms-token", set())
        client_identity = self.auth_manager.authenticate_client("no_perms_client", "no-perms-token")
        self.assertIsNotNone(client_identity)
        self.assertEqual(len(client_identity.permissions), 0)


if __name__ == '__main__':
    unittest.main()
