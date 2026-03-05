"""
Tests for the MCP resource subscription functionality.
"""

import asyncio
import sys
import os

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.server import MCPServer
from mcp.mcp_types import (
    MCPRequest, RequestMethod, Resource
)


async def test_resource_subscribe():
    """Test resource subscribe functionality."""
    server = MCPServer()
    await server.start()
    
    # Register a resource
    resource = Resource(
        uri="file:///test-subscribe.txt",
        name="Test Subscribe Resource",
        description="A test resource for subscription",
        mime_type="text/plain"
    )
    server.register_resource(resource)
    
    # Subscribe to the resource
    request = MCPRequest(
        method=RequestMethod.RESOURCE_SUBSCRIBE,
        params={"uri": "file:///test-subscribe.txt"}
    )
    
    response = await server.handle_request(request)
    
    assert response.result is not None
    assert response.error is None
    assert response.result["uri"] == "file:///test-subscribe.txt"
    assert response.result["subscribed"] is True
    
    print("✓ Resource subscribe test passed")


async def test_resource_unsubscribe():
    """Test resource unsubscribe functionality."""
    server = MCPServer()
    await server.start()
    
    # Register a resource
    resource = Resource(
        uri="file:///test-unsubscribe.txt",
        name="Test Unsubscribe Resource",
        description="A test resource for unsubscription",
        mime_type="text/plain"
    )
    server.register_resource(resource)
    
    # Unsubscribe from the resource
    request = MCPRequest(
        method=RequestMethod.RESOURCE_UNSUBSCRIBE,
        params={"uri": "file:///test-unsubscribe.txt"}
    )
    
    response = await server.handle_request(request)
    
    assert response.result is not None
    assert response.error is None
    assert response.result["uri"] == "file:///test-unsubscribe.txt"
    assert response.result["unsubscribed"] is True
    
    print("✓ Resource unsubscribe test passed")


async def test_subscribe_nonexistent_resource():
    """Test subscribing to a nonexistent resource should fail."""
    server = MCPServer()
    await server.start()
    
    # Try to subscribe to a nonexistent resource
    request = MCPRequest(
        method=RequestMethod.RESOURCE_SUBSCRIBE,
        params={"uri": "file:///nonexistent.txt"}
    )
    
    response = await server.handle_request(request)
    
    assert response.result is None
    assert response.error is not None
    assert "Resource not found" in response.error["message"]
    
    print("✓ Subscribe to nonexistent resource test passed")


async def test_subscribe_without_uri():
    """Test subscribing without URI should fail."""
    server = MCPServer()
    await server.start()
    
    # Try to subscribe without URI
    request = MCPRequest(
        method=RequestMethod.RESOURCE_SUBSCRIBE,
        params={}  # No URI
    )
    
    response = await server.handle_request(request)
    
    assert response.result is None
    assert response.error is not None
    assert "URI is required" in response.error["message"]
    
    print("✓ Subscribe without URI test passed")


async def test_unsubscribe_without_uri():
    """Test unsubscribing without URI should fail."""
    server = MCPServer()
    await server.start()
    
    # Try to unsubscribe without URI
    request = MCPRequest(
        method=RequestMethod.RESOURCE_UNSUBSCRIBE,
        params={}  # No URI
    )
    
    response = await server.handle_request(request)
    
    assert response.result is None
    assert response.error is not None
    assert "URI is required" in response.error["message"]
    
    print("✓ Unsubscribe without URI test passed")


async def run_all_tests():
    """Run all resource subscription tests."""
    await test_resource_subscribe()
    await test_resource_unsubscribe()
    await test_subscribe_nonexistent_resource()
    await test_subscribe_without_uri()
    await test_unsubscribe_without_uri()
    print("\n🎉 All resource subscription tests passed!")


if __name__ == "__main__":
    # Run all tests
    asyncio.run(run_all_tests())
