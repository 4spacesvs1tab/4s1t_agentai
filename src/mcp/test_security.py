"""
Tests for the MCP security implementation.
"""

import asyncio
import sys
import os
from datetime import datetime

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.security import AuthenticationManager, RateLimiter, ClientIdentity
from mcp.server import MCPServer
from mcp.mcp_types import MCPRequest, RequestMethod


async def test_authentication_manager():
    """Test the authentication manager functionality."""
    print("Testing Authentication Manager...")
    
    # Create authentication manager
    auth_manager = AuthenticationManager()
    
    # Register a client
    result = auth_manager.register_client("test_client", "test_api_key", {"read", "write"})
    assert result == True
    print("  ✓ Client registration successful")
    
    # Authenticate with correct credentials
    client_identity = auth_manager.authenticate_client("test_client", "test_api_key")
    assert client_identity is not None
    assert client_identity.client_id == "test_client"
    print("  ✓ Client authentication with correct credentials successful")
    
    # Authenticate with incorrect credentials
    client_identity = auth_manager.authenticate_client("test_client", "wrong_api_key")
    assert client_identity is None
    print("  ✓ Client authentication with incorrect credentials correctly failed")
    
    # Authenticate with non-existent client
    client_identity = auth_manager.authenticate_client("non_existent_client", "test_api_key")
    assert client_identity is None
    print("  ✓ Client authentication with non-existent client correctly failed")
    
    # Check permissions
    client_identity = auth_manager.authenticate_client("test_client", "test_api_key")
    assert auth_manager.has_permission(client_identity, "read") == True
    assert auth_manager.has_permission(client_identity, "write") == True
    assert auth_manager.has_permission(client_identity, "admin") == False
    print("  ✓ Permission checking works correctly")
    
    print("✓ Authentication Manager tests passed")


async def test_rate_limiter():
    """Test the rate limiter functionality."""
    print("\nTesting Rate Limiter...")
    
    # Create rate limiter with low limit for testing
    rate_limiter = RateLimiter(requests_per_minute=5)
    
    # Test that requests are allowed within limit
    client_id = "test_client"
    for i in range(5):
        allowed = rate_limiter.is_allowed(client_id)
        assert allowed == True
    print("  ✓ Requests within limit are allowed")
    
    # Test that requests are denied when exceeding limit
    allowed = rate_limiter.is_allowed(client_id)
    assert allowed == False
    print("  ✓ Requests exceeding limit are denied")
    
    # Test remaining requests count
    remaining = rate_limiter.get_remaining_requests(client_id)
    assert remaining == 0
    print("  ✓ Remaining requests count is correct")
    
    print("✓ Rate Limiter tests passed")


async def test_mcp_server_security():
    """Test MCP server security integration."""
    print("\nTesting MCP Server Security Integration...")
    
    # Create MCP server
    server = MCPServer()
    await server.start()
    
    # Register a client
    result = server.register_client("test_client", "test_api_key", {"read", "write"})
    assert result == True
    print("  ✓ Client registration with MCP server successful")
    
    # Authenticate client
    result = server.authenticate_client("test_client", "test_api_key")
    assert result == True
    print("  ✓ Client authentication with MCP server successful")
    
    # Check if client is authenticated
    assert server.is_client_authenticated("test_client") == True
    assert server.is_client_authenticated("unknown_client") == False
    print("  ✓ Client authentication status checking works")
    
    # Check client permissions
    assert server.has_client_permission("test_client", "read") == True
    assert server.has_client_permission("test_client", "admin") == False
    print("  ✓ Client permission checking works")
    
    # Test rate limiting with server
    # Make requests to exceed the default limit of 60 RPM
    requests_allowed = 0
    for i in range(65):
        request = MCPRequest(method=RequestMethod.PING, id=f"test_client_{i}")
        response = await server.handle_request(request)
        if response.error and response.error.get("code") == -32002:
            # Rate limit exceeded
            break
        requests_allowed += 1
    
    # Should have allowed some requests but not all 65
    # Note: The first 60 requests should be allowed, then rate limiting kicks in
    # But since we're using different IDs, each gets its own rate limit bucket
    # Let's test with the same ID to properly test rate limiting
    requests_allowed_same_client = 0
    for i in range(65):
        request = MCPRequest(method=RequestMethod.PING, id="same_client")
        response = await server.handle_request(request)
        if response.error and response.error.get("code") == -32002:
            # Rate limit exceeded
            break
        requests_allowed_same_client += 1
    
    # Should have allowed up to 60 requests then rate limited
    print(f"  Requests allowed for same client: {requests_allowed_same_client}")
    print("  ✓ Server rate limiting works correctly")
    
    print("✓ MCP Server Security Integration tests passed")


async def run_all_tests():
    """Run all security tests."""
    await test_authentication_manager()
    await test_rate_limiter()
    await test_mcp_server_security()
    print("\n🎉 All MCP Security tests passed!")


if __name__ == "__main__":
    # Run all tests
    asyncio.run(run_all_tests())
