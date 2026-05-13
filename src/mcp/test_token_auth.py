"""
Test for token-based authentication in MCP server.
"""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.server import MCPServer
from mcp.mcp_types import MCPRequest, RequestMethod


async def test_token_authentication():
    """Test token-based authentication."""
    print("=== Token-Based Authentication Test ===")
    
    # Create server
    server = MCPServer()
    await server.start()
    
    # Add a valid token
    server.add_valid_token("test-token-123", {"read", "write"})
    
    # Test authentication with valid token
    print("1. Testing authentication with valid token...")
    auth_result = server.authenticate_client("test_client_1", "test-token-123")
    print(f"   Authentication result: {auth_result}")
    
    if auth_result:
        # Check permissions
        has_read = server.has_client_permission("test_client_1", "read")
        has_write = server.has_client_permission("test_client_1", "write")
        has_admin = server.has_client_permission("test_client_1", "admin")
        print(f"   Has 'read' permission: {has_read}")
        print(f"   Has 'write' permission: {has_write}")
        print(f"   Has 'admin' permission: {has_admin}")
    
    # Test authentication with invalid token
    print("\n2. Testing authentication with invalid token...")
    auth_result = server.authenticate_client("test_client_2", "invalid-token")
    print(f"   Authentication result: {auth_result}")
    
    # Test authentication without token
    print("\n3. Testing authentication without token...")
    # This should work for public endpoints (like ping)
    ping_request = MCPRequest(method=RequestMethod.PING)
    response = await server.handle_request(ping_request)
    print(f"   Ping response: {response.result}")
    
    await server.stop()
    print("\n✅ Token authentication test completed!")


if __name__ == "__main__":
    asyncio.run(test_token_authentication())
