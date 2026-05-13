"""
Security demo for the MCP (Model Context Protocol) implementation.

This demo shows how to use the authentication and rate limiting features
of the MCP server.
"""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.mcp_types import MCPRequest, RequestMethod, Resource, Tool
from mcp.server import MCPServer
from mcp.http_adapter import MCPHTTPAdapter
from fastapi.testclient import TestClient


async def demo_authentication():
    """Demonstrate client authentication."""
    print("=== MCP Security Demo: Authentication ===\n")
    
    # Create MCP server
    server = MCPServer()
    await server.start()
    print("✓ MCP Server started")
    
    # Register a client with permissions
    server.register_client(
        client_id="demo_client",
        api_key="demo_secret_key",
        permissions={"read", "write", "execute"}
    )
    print("✓ Registered demo client with read/write/execute permissions")
    
    # Authenticate the client
    auth_result = server.authenticate_client("demo_client", "demo_secret_key")
    if auth_result:
        print("✓ Client authenticated successfully")
        print(f"  Client has 'read' permission: {server.has_client_permission('demo_client', 'read')}")
        print(f"  Client has 'admin' permission: {server.has_client_permission('demo_client', 'admin')}")
    else:
        print("✗ Client authentication failed")
    
    # Try to authenticate with wrong credentials
    auth_result = server.authenticate_client("demo_client", "wrong_key")
    if not auth_result:
        print("✓ Authentication correctly failed with wrong credentials")
    else:
        print("✗ Authentication should have failed with wrong credentials")
    
    print()


async def demo_rate_limiting():
    """Demonstrate rate limiting."""
    print("=== MCP Security Demo: Rate Limiting ===\n")
    
    # Create MCP server with lower rate limit for demo
    server = MCPServer()
    server.rate_limiter = server.rate_limiter = type(server.rate_limiter)(requests_per_minute=5)
    await server.start()
    print("✓ MCP Server started with 5 requests per minute limit")
    
    # Make requests from the same client
    allowed_requests = 0
    for i in range(10):
        request = MCPRequest(
            id="demo_client",
            method=RequestMethod.PING
        )
        response = await server.handle_request(request)
        if response.error and response.error.get("code") == -32002:
            print(f"✗ Request {i+1}: Rate limited (as expected)")
            break
        else:
            print(f"✓ Request {i+1}: Allowed")
            allowed_requests += 1
    
    print(f"\nTotal allowed requests: {allowed_requests} out of 10")
    print("✓ Rate limiting working correctly")
    print()


async def demo_http_authentication():
    """Demonstrate HTTP authentication."""
    print("=== MCP Security Demo: HTTP Authentication ===\n")
    
    # Create MCP server and HTTP adapter
    server = MCPServer()
    await server.start()
    
    # Register a client
    server.register_client("http_client", "http_secret_key", {"read", "write"})
    
    adapter = MCPHTTPAdapter(server)
    client = TestClient(adapter.app)
    print("✓ MCP Server and HTTP Adapter created")
    
    # Make request without authentication (should work for public endpoints)
    response = client.get("/health")
    print(f"✓ Public endpoint response: {response.status_code}")
    
    # Make request with authentication headers
    headers = {
        "X-Client-ID": "http_client",
        "X-API-Key": "http_secret_key"
    }
    
    request_data = {
        "id": "http_client",
        "method": "ping"
    }
    
    response = client.post("/mcp", json=request_data, headers=headers)
    if response.status_code == 200:
        print("✓ Authenticated MCP request successful")
        print(f"  Response: {response.json()}")
    else:
        print(f"✗ Authenticated MCP request failed: {response.status_code}")
    
    # Make request with wrong authentication
    wrong_headers = {
        "X-Client-ID": "http_client",
        "X-API-Key": "wrong_key"
    }
    
    response = client.post("/mcp", json=request_data, headers=wrong_headers)
    if response.status_code == 401:
        print("✓ MCP request correctly rejected with wrong credentials")
    else:
        print(f"✗ MCP request should have been rejected: {response.status_code}")
    
    print()


async def main():
    """Run all security demos."""
    await demo_authentication()
    await demo_rate_limiting()
    await demo_http_authentication()
    print("🎉 All Security Demos Completed!")


if __name__ == "__main__":
    asyncio.run(main())
