"""
Tests for the MCP resource caching functionality.
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.server import MCPServer
from mcp.mcp_types import (
    MCPRequest, RequestMethod, Resource, Tool, Prompt, Configuration
)


async def test_resource_caching_basic():
    """Test basic resource caching functionality."""
    server = MCPServer()
    await server.start()
    
    # Register a resource
    resource = Resource(
        uri="file:///test-cache.txt",
        name="Test Cache Resource",
        description="A test resource for caching",
        mime_type="text/plain"
    )
    server.register_resource(resource)
    
    # First request should cache the resource
    request1 = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={"uri": "file:///test-cache.txt"}
    )
    
    response1 = await server.handle_request(request1)
    
    assert response1.result is not None
    assert response1.error is None
    assert "contents" in response1.result
    assert "Contents of resource: Test Cache Resource" in response1.result["contents"]
    
    # Second request should use cached content
    request2 = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={"uri": "file:///test-cache.txt"}
    )
    
    response2 = await server.handle_request(request2)
    
    assert response2.result is not None
    assert response2.error is None
    assert response2.result["contents"] == response1.result["contents"]
    
    print("✓ Basic resource caching test passed")


async def test_cache_clearing():
    """Test cache clearing functionality."""
    server = MCPServer()
    await server.start()
    
    # Register a resource
    resource = Resource(
        uri="file:///test-clear.txt",
        name="Test Clear Resource",
        description="A test resource for cache clearing",
        mime_type="text/plain"
    )
    server.register_resource(resource)
    
    # First request to cache the resource
    request1 = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={"uri": "file:///test-clear.txt"}
    )
    
    response1 = await server.handle_request(request1)
    cached_content = response1.result["contents"]
    
    # Clear specific cache entry
    server.clear_resource_cache("file:///test-clear.txt")
    
    # Second request should generate new content (not from cache)
    request2 = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={"uri": "file:///test-clear.txt"}
    )
    
    response2 = await server.handle_request(request2)
    
    # Content should be the same (since it's simulated) but it should be a fresh fetch
    assert response2.result is not None
    assert response2.result["contents"] == cached_content
    
    print("✓ Cache clearing test passed")


async def test_cache_clearing_all():
    """Test clearing all cache functionality."""
    server = MCPServer()
    await server.start()
    
    # Register resources
    resource1 = Resource(
        uri="file:///test-clear1.txt",
        name="Test Clear Resource 1",
        description="A test resource for cache clearing",
        mime_type="text/plain"
    )
    resource2 = Resource(
        uri="file:///test-clear2.txt",
        name="Test Clear Resource 2",
        description="Another test resource for cache clearing",
        mime_type="text/plain"
    )
    server.register_resource(resource1)
    server.register_resource(resource2)
    
    # Request both resources to cache them
    request1 = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={"uri": "file:///test-clear1.txt"}
    )
    request2 = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={"uri": "file:///test-clear2.txt"}
    )
    
    await server.handle_request(request1)
    await server.handle_request(request2)
    
    # Verify both are in cache
    assert len(server.resource_cache) == 2
    
    # Clear all cache
    server.clear_resource_cache()
    
    # Verify cache is empty
    assert len(server.resource_cache) == 0
    
    print("✓ Clear all cache test passed")


async def run_all_tests():
    """Run all resource caching tests."""
    await test_resource_caching_basic()
    await test_cache_clearing()
    await test_cache_clearing_all()
    print("\n🎉 All resource caching tests passed!")


if __name__ == "__main__":
    # Run all tests
    asyncio.run(run_all_tests())
