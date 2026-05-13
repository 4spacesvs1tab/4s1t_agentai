"""
Tests for the MCP HTTP adapter.
"""

import asyncio
import json
import sys
import os
from unittest.mock import AsyncMock, Mock

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcp.mcp_types import MCPRequest, RequestMethod, Resource, Tool, Prompt
from mcp.server import MCPServer
from mcp.http_adapter import MCPHTTPAdapter


def test_http_adapter_initialization():
    """Test HTTP adapter initialization."""
    # Create MCP server
    mcp_server = MCPServer()
    
    # Create HTTP adapter
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Check that app is created
    assert adapter.app is not None
    assert isinstance(adapter.app, FastAPI)
    
    # Check that routes are set up
    routes = [route.path for route in adapter.app.routes]
    assert "/health" in routes
    assert "/mcp" in routes
    assert "/mcp/info" in routes
    
    print("✓ HTTP adapter initialization test passed")


def test_health_endpoint():
    """Test health check endpoint."""
    # Create MCP server and adapter
    mcp_server = MCPServer()
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Create test client
    client = TestClient(adapter.app)
    
    # Test health endpoint
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert data["status"] == "healthy"
    assert "timestamp" in data
    
    print("✓ Health endpoint test passed")


def test_mcp_info_endpoint():
    """Test MCP info endpoint."""
    # Create MCP server and adapter
    mcp_server = MCPServer()
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Create test client
    client = TestClient(adapter.app)
    
    # Test info endpoint
    response = client.get("/mcp/info")
    assert response.status_code == 200
    data = response.json()
    assert "running" in data
    assert "resources_count" in data
    assert "tools_count" in data
    assert "prompts_count" in data
    
    print("✓ MCP info endpoint test passed")


def test_resource_endpoints():
    """Test resource-related endpoints."""
    # Create MCP server and adapter
    mcp_server = MCPServer()
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Create test client
    client = TestClient(adapter.app)
    
    # Start the MCP server (needed for handling requests)
    import asyncio
    asyncio.run(mcp_server.start())
    
    # Register a resource
    resource = Resource(
        uri="file:///test.txt",
        name="Test Resource",
        description="A test resource"
    )
    mcp_server.register_resource(resource)
    
    # Test resources list endpoint
    response = client.get("/mcp/resources")
    assert response.status_code == 200
    data = response.json()
    assert "resources" in data
    assert len(data["resources"]) == 1
    assert data["resources"][0]["uri"] == "file:///test.txt"
    
    # Test resource get endpoint
    response = client.get("/mcp/resources/file:///test.txt")
    assert response.status_code == 200
    data = response.json()
    assert "uri" in data
    assert data["uri"] == "file:///test.txt"
    
    print("✓ Resource endpoints test passed")


def test_tool_endpoints():
    """Test tool-related endpoints."""
    # Create MCP server and adapter
    mcp_server = MCPServer()
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Create test client
    client = TestClient(adapter.app)
    
    # Register a tool
    async def test_executor(arguments):
        return {"result": "success"}
    
    tool = Tool(
        name="test_tool",
        description="A test tool",
        input_schema={"type": "object"}
    )
    mcp_server.register_tool(tool, test_executor)
    
    # Test tools list endpoint
    response = client.get("/mcp/tools")
    assert response.status_code == 200
    data = response.json()
    assert "tools" in data
    assert len(data["tools"]) == 1
    assert data["tools"][0]["name"] == "test_tool"
    
    print("✓ Tool endpoints test passed")


def test_prompt_endpoints():
    """Test prompt-related endpoints."""
    # Create MCP server and adapter
    mcp_server = MCPServer()
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Create test client
    client = TestClient(adapter.app)
    
    # Register a prompt
    prompt = Prompt(
        name="test_prompt",
        description="A test prompt",
        arguments=[{"name": "input", "required": True}]
    )
    mcp_server.register_prompt(prompt)
    
    # Test prompts list endpoint
    response = client.get("/mcp/prompts")
    assert response.status_code == 200
    data = response.json()
    assert "prompts" in data
    assert len(data["prompts"]) == 1
    assert data["prompts"][0]["name"] == "test_prompt"
    
    # Test prompt get endpoint
    response = client.get("/mcp/prompts/test_prompt")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert data["name"] == "test_prompt"
    
    print("✓ Prompt endpoints test passed")


def test_mcp_protocol_endpoint():
    """Test the main MCP protocol endpoint."""
    # Create MCP server and adapter
    mcp_server = MCPServer()
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Create test client
    client = TestClient(adapter.app)
    
    # Test ping request
    request_data = {
        "id": "test-1",
        "method": "ping",
        "params": {}
    }
    
    response = client.post("/mcp", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert "result" in data
    assert data["result"]["pong"] == True
    
    # Test invalid JSON
    response = client.post("/mcp", content="invalid json")
    assert response.status_code == 400
    
    print("✓ MCP protocol endpoint test passed")


def test_cors_middleware():
    """Test that CORS middleware is properly configured."""
    # Create MCP server and adapter
    mcp_server = MCPServer()
    adapter = MCPHTTPAdapter(mcp_server)
    
    # Check that CORS middleware is added
    middleware_names = [mw.__class__.__name__ for mw in adapter.app.user_middleware]
    assert "CORSMiddleware" in middleware_names
    
    print("✓ CORS middleware test passed")


def test_custom_app():
    """Test using a custom FastAPI app."""
    # Create custom app
    custom_app = FastAPI(title="Custom App", version="1.0.0")
    
    # Create MCP server and adapter with custom app
    mcp_server = MCPServer()
    adapter = MCPHTTPAdapter(mcp_server, custom_app)
    
    # Check that custom app is used
    assert adapter.app == custom_app
    assert adapter.app.title == "Custom App"
    assert adapter.app.version == "1.0.0"
    
    print("✓ Custom app test passed")


if __name__ == "__main__":
    # Run all tests
    test_http_adapter_initialization()
    test_health_endpoint()
    test_mcp_info_endpoint()
    test_resource_endpoints()
    test_tool_endpoints()
    test_prompt_endpoints()
    test_mcp_protocol_endpoint()
    test_cors_middleware()
    test_custom_app()
    print("\n🎉 All MCP HTTP adapter tests passed!")
