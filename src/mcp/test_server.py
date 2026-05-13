"""
Tests for the MCP (Model Context Protocol) server implementation.
"""

import asyncio
import sys
import os
from datetime import datetime

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mcp.server import MCPServer
from mcp.mcp_types import (
    MCPRequest, RequestMethod, Resource, Tool, Prompt, Configuration
)


async def test_server_initialization():
    """Test MCP server initialization."""
    # Create server with default config
    server = MCPServer()
    assert server is not None
    assert server.config is not None
    assert server.running == False
    
    # Create server with custom config
    config = Configuration(max_resource_size=2048*1024, timeout_seconds=60)
    server = MCPServer(config)
    assert server.config.max_resource_size == 2048*1024
    assert server.config.timeout_seconds == 60
    
    print("✓ Server initialization test passed")


async def test_server_lifecycle():
    """Test MCP server start/stop lifecycle."""
    server = MCPServer()
    
    # Start server
    result = await server.start()
    assert result == True
    assert server.running == True
    
    # Stop server
    result = await server.stop()
    assert result == True
    assert server.running == False
    
    print("✓ Server lifecycle test passed")


async def test_resource_registration():
    """Test resource registration functionality."""
    server = MCPServer()
    
    # Create test resource
    resource = Resource(
        uri="file:///test.txt",
        name="Test Resource",
        description="A test resource",
        mime_type="text/plain"
    )
    
    # Register resource
    result = server.register_resource(resource)
    assert result == True
    assert "file:///test.txt" in server.resources
    assert server.resources["file:///test.txt"].name == "Test Resource"
    
    # Unregister resource
    result = server.unregister_resource("file:///test.txt")
    assert result == True
    assert "file:///test.txt" not in server.resources
    
    # Try to unregister non-existent resource
    result = server.unregister_resource("file:///nonexistent.txt")
    assert result == False
    
    print("✓ Resource registration test passed")


async def test_tool_registration():
    """Test tool registration functionality."""
    server = MCPServer()
    
    # Create test tool
    tool = Tool(
        name="test_tool",
        description="A test tool",
        input_schema={"type": "object"}
    )
    
    # Create test executor
    async def test_executor(arguments):
        return {"result": "success"}
    
    # Register tool
    result = server.register_tool(tool, test_executor)
    assert result == True
    assert "test_tool" in server.tools
    assert "test_tool" in server.tool_executors
    
    # Unregister tool
    result = server.unregister_tool("test_tool")
    assert result == True
    assert "test_tool" not in server.tools
    assert "test_tool" not in server.tool_executors
    
    print("✓ Tool registration test passed")


async def test_prompt_registration():
    """Test prompt registration functionality."""
    server = MCPServer()
    
    # Create test prompt
    prompt = Prompt(
        name="test_prompt",
        description="A test prompt",
        arguments=[{"name": "input", "required": True}]
    )
    
    # Register prompt
    result = server.register_prompt(prompt)
    assert result == True
    assert "test_prompt" in server.prompts
    assert server.prompts["test_prompt"].description == "A test prompt"
    
    # Unregister prompt
    result = server.unregister_prompt("test_prompt")
    assert result == True
    assert "test_prompt" not in server.prompts
    
    print("✓ Prompt registration test passed")


async def test_initialize_handler():
    """Test initialize request handler."""
    server = MCPServer()
    await server.start()
    
    # Create initialize request
    request = MCPRequest(
        method=RequestMethod.INITIALIZE,
        params={
            "protocolVersion": "2024-01-01",
            "capabilities": {
                "prompts": True,
                "resources": True,
                "tools": True
            },
            "clientInfo": {
                "name": "Test Client",
                "version": "1.0.0"
            }
        }
    )
    
    # Handle request
    response = await server.handle_request(request)
    
    # Check response
    assert response.result is not None
    assert response.error is None
    assert "protocolVersion" in response.result
    assert "capabilities" in response.result
    assert response.result["capabilities"]["prompts"] == True
    
    print("✓ Initialize handler test passed")


async def test_ping_handler():
    """Test ping request handler."""
    server = MCPServer()
    await server.start()
    
    # Create ping request
    request = MCPRequest(method=RequestMethod.PING)
    
    # Handle request
    response = await server.handle_request(request)
    
    # Check response
    assert response.result is not None
    assert response.error is None
    assert response.result["pong"] == True
    assert "timestamp" in response.result
    
    print("✓ Ping handler test passed")


async def test_resource_list_handler():
    """Test resource list request handler."""
    server = MCPServer()
    await server.start()
    
    # Register some resources
    resource1 = Resource(uri="file:///res1.txt", name="Resource 1")
    resource2 = Resource(uri="file:///res2.txt", name="Resource 2")
    server.register_resource(resource1)
    server.register_resource(resource2)
    
    # Create resource list request
    request = MCPRequest(method=RequestMethod.RESOURCE_LIST)
    
    # Handle request
    response = await server.handle_request(request)
    
    # Check response
    assert response.result is not None
    assert response.error is None
    assert "resources" in response.result
    assert len(response.result["resources"]) == 2
    
    print("✓ Resource list handler test passed")


async def test_tool_list_handler():
    """Test tool list request handler."""
    server = MCPServer()
    await server.start()
    
    # Register some tools
    async def dummy_executor(args):
        return {"dummy": True}
        
    tool1 = Tool(name="tool1", description="Tool 1", input_schema={})
    tool2 = Tool(name="tool2", description="Tool 2", input_schema={})
    server.register_tool(tool1, dummy_executor)
    server.register_tool(tool2, dummy_executor)
    
    # Create tool list request
    request = MCPRequest(method=RequestMethod.TOOL_LIST)
    
    # Handle request
    response = await server.handle_request(request)
    
    # Check response
    assert response.result is not None
    assert response.error is None
    assert "tools" in response.result
    assert len(response.result["tools"]) == 2
    
    print("✓ Tool list handler test passed")


async def test_tool_call_handler():
    """Test tool call request handler."""
    server = MCPServer()
    await server.start()
    
    # Register a tool with executor
    async def add_executor(arguments):
        a = arguments.get("a", 0)
        b = arguments.get("b", 0)
        return {"result": a + b}
    
    tool = Tool(
        name="add",
        description="Add two numbers",
        input_schema={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"}
            }
        }
    )
    server.register_tool(tool, add_executor)
    
    # Create tool call request
    request = MCPRequest(
        method=RequestMethod.TOOL_CALL,
        params={
            "name": "add",
            "arguments": {"a": 5, "b": 3}
        }
    )
    
    # Handle request
    response = await server.handle_request(request)
    
    # Check response
    assert response.result is not None
    assert response.error is None
    assert response.result["toolName"] == "add"
    assert response.result["result"]["result"] == 8
    assert response.result["isError"] == False
    
    print("✓ Tool call handler test passed")


async def test_error_handling():
    """Test error handling in request processing."""
    server = MCPServer()
    await server.start()
    
    # Test missing required parameters
    request = MCPRequest(
        method=RequestMethod.RESOURCE_GET,
        params={}  # Missing 'uri'
    )
    response = await server.handle_request(request)
    
    # Should return error
    assert response.result is None
    assert response.error is not None
    assert response.error["code"] == -32603  # Internal error
    
    print("✓ Error handling test passed")


async def test_server_info():
    """Test server information retrieval."""
    server = MCPServer()
    
    # Get server info before start
    info = server.get_server_info()
    assert info is not None
    assert info["running"] == False
    assert info["resources_count"] == 0
    assert info["tools_count"] == 0
    
    # Register some items
    resource = Resource(uri="file:///info.txt", name="Info Resource")
    tool = Tool(name="info_tool", description="Info Tool", input_schema={})
    
    async def dummy_executor(args):
        return {"info": True}
    
    server.register_resource(resource)
    server.register_tool(tool, dummy_executor)
    
    # Get server info after registration
    info = server.get_server_info()
    assert info["resources_count"] == 1
    assert info["tools_count"] == 1
    
    print("✓ Server info test passed")


async def run_all_tests():
    """Run all tests."""
    await test_server_initialization()
    await test_server_lifecycle()
    await test_resource_registration()
    await test_tool_registration()
    await test_prompt_registration()
    await test_initialize_handler()
    await test_ping_handler()
    await test_resource_list_handler()
    await test_tool_list_handler()
    await test_tool_call_handler()
    await test_error_handling()
    await test_server_info()
    print("\n🎉 All MCP server tests passed!")


if __name__ == "__main__":
    # Run all tests
    asyncio.run(run_all_tests())
