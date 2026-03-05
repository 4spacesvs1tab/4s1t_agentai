"""
Protocol compliance validation tests for the MCP (Model Context Protocol) implementation.

These tests validate that the MCP server implementation complies with the 
Model Context Protocol specification at https://modelcontextprotocol.io
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
async def test_protocol_version_compliance():
    """Test that the server complies with protocol version requirements."""
    server = MCPServer()
    await server.start()
    
    # Test initialize with correct protocol version
    request = MCPRequest(
        method=RequestMethod.INITIALIZE,
        params={
            "protocolVersion": "2024-01-01",
            "capabilities": {
                "prompts": True,
                "resources": True,
                "tools": True
            }
        }
    )
    
    response = await server.handle_request(request)
    
    # Server should respond with the same protocol version
    assert response.result is not None
    assert response.result["protocolVersion"] == "2024-01-01"
    
    # Server info should also show correct version
    server_info = server.get_server_info()
    # This is checked in the initialize response, not server info
    
    print("✓ Protocol version compliance test passed")


@pytest.mark.asyncio
async def test_message_structure_compliance():
    """Test that messages comply with MCP structure requirements."""
    server = MCPServer()
    await server.start()
    
    # Test that requests have required fields
    request = MCPRequest(
        method=RequestMethod.PING,
        id="test-message-id"
    )
    
    # Check request structure
    assert request.id is not None
    assert request.type == MessageType.REQUEST
    assert request.protocol_version == "2024-01-01"
    assert isinstance(request.timestamp, datetime)
    
    response = await server.handle_request(request)
    
    # Check response structure
    assert isinstance(response, MCPResponse)
    assert response.request_id == "test-message-id"
    assert response.type == MessageType.RESPONSE
    assert response.protocol_version == "2024-01-01"
    assert isinstance(response.timestamp, datetime)
    
    print("✓ Message structure compliance test passed")


@pytest.mark.asyncio
async def test_error_response_compliance():
    """Test that error responses comply with MCP specification."""
    server = MCPServer()
    await server.start()
    
    # Trigger an error by sending a request for non-existent resource
    request = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={
            "uri": "file:///nonexistent.txt"
        }
    )
    
    response = await server.handle_request(request)
    
    # Error responses should have specific structure
    assert response.result is None
    assert response.error is not None
    assert "code" in response.error
    assert "message" in response.error
    # Error code should follow JSON-RPC specification
    assert isinstance(response.error["code"], int)
    assert isinstance(response.error["message"], str)
    
    print("✓ Error response compliance test passed")


@pytest.mark.asyncio
async def test_resource_interface_compliance():
    """Test that resource interface complies with MCP specification."""
    server = MCPServer()
    await server.start()
    
    # Register a resource
    resource = Resource(
        uri="file:///test-resource.txt",
        name="Test Resource",
        description="A test resource for compliance testing",
        mime_type="text/plain",
        size=100
    )
    server.register_resource(resource)
    
    # Test resource list response structure
    list_request = MCPRequest(method=RequestMethod.RESOURCE_LIST)
    list_response = await server.handle_request(list_request)
    
    assert "resources" in list_response.result
    resources = list_response.result["resources"]
    assert len(resources) > 0
    
    # Check first resource structure
    first_resource = resources[0]
    required_fields = ["uri", "name"]
    for field in required_fields:
        assert field in first_resource
    
    # Test resource get response structure
    get_request = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={"uri": "file:///test-resource.txt"}
    )
    get_response = await server.handle_request(get_request)
    
    # Resource content response should have required fields
    required_content_fields = ["uri", "contents", "mimeType"]
    for field in required_content_fields:
        assert field in get_response.result
    
    print("✓ Resource interface compliance test passed")


@pytest.mark.asyncio
async def test_tool_interface_compliance():
    """Test that tool interface complies with MCP specification."""
    server = MCPServer()
    await server.start()
    
    # Register a tool
    async def test_executor(arguments):
        return {"test": "result"}
    
    tool = Tool(
        name="test-tool",
        description="A test tool for compliance testing",
        input_schema={
            "type": "object",
            "properties": {
                "param1": {"type": "string"}
            }
        }
    )
    server.register_tool(tool, test_executor)
    
    # Test tool list response structure
    list_request = MCPRequest(method=RequestMethod.TOOL_LIST)
    list_response = await server.handle_request(list_request)
    
    assert "tools" in list_response.result
    tools = list_response.result["tools"]
    assert len(tools) > 0
    
    # Check first tool structure
    first_tool = tools[0]
    required_fields = ["name", "description", "inputSchema"]
    for field in required_fields:
        assert field in first_tool
    
    # Test tool call response structure
    call_request = MCPRequest(
        method=RequestMethod.TOOL_CALL,
        params={
            "name": "test-tool",
            "arguments": {"param1": "test-value"}
        }
    )
    call_response = await server.handle_request(call_request)
    
    # Tool call response should have required fields
    required_response_fields = ["toolName", "result", "isError"]
    for field in required_response_fields:
        assert field in call_response.result
    
    print("✓ Tool interface compliance test passed")


@pytest.mark.asyncio
async def test_method_not_found_compliance():
    """Test that unsupported methods return proper error responses."""
    server = MCPServer()
    await server.start()
    
    # We'll test this by directly calling the server's method lookup
    # Create a mock request-like object with an unsupported method
    from mcp.mcp_types import MCPRequest, RequestMethod
    from dataclasses import dataclass, field
    from typing import Optional, Dict, Any
    from datetime import datetime
    import uuid
    
    # Create a request with a method that's not registered
    # We can do this by temporarily removing a handler
    original_handlers = server.request_handlers.copy()
    
    # Remove one handler to test method not found
    if RequestMethod.PING in server.request_handlers:
        del server.request_handlers[RequestMethod.PING]
    
    request = MCPRequest(
        method=RequestMethod.PING,  # This method is now unhandled
        params={},
        id="test-ping-method-not-found"
    )
    
    response = await server.handle_request(request)
    
    # Restore handlers
    server.request_handlers = original_handlers
    
    # Should return method not found error
    assert response.result is None
    assert response.error is not None
    assert response.error["code"] == -32601  # Method not found
    assert "Method not found" in response.error["message"]
    
    print("✓ Method not found compliance test passed")


@pytest.mark.asyncio
async def test_server_capabilities_compliance():
    """Test that server capabilities are properly reported."""
    server = MCPServer()
    await server.start()
    
    # Test initialize response includes capabilities
    request = MCPRequest(
        method=RequestMethod.INITIALIZE,
        params={
            "protocolVersion": "2024-01-01",
            "capabilities": {
                "prompts": True,
                "resources": True,
                "tools": True
            }
        }
    )
    
    response = await server.handle_request(request)
    
    # Check capabilities structure
    assert "capabilities" in response.result
    capabilities = response.result["capabilities"]
    
    # All required capabilities should be present
    required_capabilities = ["prompts", "resources", "tools", "notifications"]
    for cap in required_capabilities:
        assert cap in capabilities
        assert isinstance(capabilities[cap], bool)
    
    print("✓ Server capabilities compliance test passed")


@pytest.mark.asyncio
async def test_request_id_persistence():
    """Test that request IDs are properly preserved in responses."""
    server = MCPServer()
    await server.start()
    
    # Send request with specific ID
    custom_id = "custom-request-id-123"
    request = MCPRequest(
        method=RequestMethod.PING,
        id=custom_id
    )
    
    response = await server.handle_request(request)
    
    # Response should reference the same request ID
    assert response.request_id == custom_id
    
    print("✓ Request ID persistence test passed")


if __name__ == "__main__":
    # Run all protocol compliance tests
    asyncio.run(test_protocol_version_compliance())
    asyncio.run(test_message_structure_compliance())
    asyncio.run(test_error_response_compliance())
    asyncio.run(test_resource_interface_compliance())
    asyncio.run(test_tool_interface_compliance())
    asyncio.run(test_method_not_found_compliance())
    asyncio.run(test_server_capabilities_compliance())
    asyncio.run(test_request_id_persistence())
    print("\n🎉 All MCP protocol compliance tests passed!")
