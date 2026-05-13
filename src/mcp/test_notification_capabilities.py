"""
Notification capabilities tests for the MCP (Model Context Protocol) implementation.

These tests validate that the MCP server properly handles notification-related requests.
"""

import asyncio
import sys
import os
from datetime import datetime
import pytest

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.server import MCPServer
from mcp.mcp_types import (
    MCPRequest, RequestMethod, Resource, Tool, Prompt, Configuration,
    MCPResponse, MessageType
)


@pytest.mark.asyncio
async def test_notifications_capabilities():
    """Test notifications/capabilities request handling."""
    server = MCPServer()
    await server.start()
    
    # Test notifications capabilities request
    request = MCPRequest(
        method=RequestMethod.NOTIFICATIONS_CAPABILITIES,
        params={}
    )
    
    response = await server.handle_request(request)
    
    # Should return notification information
    assert response.result is not None
    assert "notifications" in response.result
    notifications = response.result["notifications"]
    assert isinstance(notifications, list)
    assert len(notifications) > 0
    
    # Check that required notification types are present
    notification_methods = [n["method"] for n in notifications]
    expected_methods = [
        "notifications/resources",
        "notifications/tools", 
        "notifications/prompts"
    ]
    
    for method in expected_methods:
        assert method in notification_methods
    
    print("✓ Notifications capabilities test passed")


@pytest.mark.asyncio
async def test_notifications_resources():
    """Test notifications/resources request handling."""
    server = MCPServer()
    await server.start()
    
    # Test notifications resources request
    request = MCPRequest(
        method=RequestMethod.NOTIFICATIONS_RESOURCES,
        params={
            "uris": ["file:///test1.txt", "file:///test2.txt"]
        }
    )
    
    response = await server.handle_request(request)
    
    # Should acknowledge subscription
    assert response.result is not None
    assert response.result["subscribed"] == True
    assert "uris" in response.result
    assert response.result["uris"] == ["file:///test1.txt", "file:///test2.txt"]
    
    print("✓ Notifications resources test passed")


@pytest.mark.asyncio
async def test_notifications_tools():
    """Test notifications/tools request handling."""
    server = MCPServer()
    await server.start()
    
    # Test notifications tools request
    request = MCPRequest(
        method=RequestMethod.NOTIFICATIONS_TOOLS,
        params={
            "tools": ["calculator", "web_search"]
        }
    )
    
    response = await server.handle_request(request)
    
    # Should acknowledge subscription
    assert response.result is not None
    assert response.result["subscribed"] == True
    assert "tools" in response.result
    assert response.result["tools"] == ["calculator", "web_search"]
    
    print("✓ Notifications tools test passed")


@pytest.mark.asyncio
async def test_notifications_prompts():
    """Test notifications/prompts request handling."""
    server = MCPServer()
    await server.start()
    
    # Test notifications prompts request
    request = MCPRequest(
        method=RequestMethod.NOTIFICATIONS_PROMPTS,
        params={
            "prompts": ["summarize", "translate"]
        }
    )
    
    response = await server.handle_request(request)
    
    # Should acknowledge subscription
    assert response.result is not None
    assert response.result["subscribed"] == True
    assert "prompts" in response.result
    assert response.result["prompts"] == ["summarize", "translate"]
    
    print("✓ Notifications prompts test passed")


if __name__ == "__main__":
    # Run all notification tests
    asyncio.run(test_notifications_capabilities())
    asyncio.run(test_notifications_resources())
    asyncio.run(test_notifications_tools())
    asyncio.run(test_notifications_prompts())
    print("\n🎉 All MCP notification capability tests passed!")
